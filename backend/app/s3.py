"""S3 object storage: referential sources read from a bucket, and full backups written to one.

Amazon S3 and every S3-compatible store (MinIO, Ceph RADOS Gateway, Scaleway, OVH, Wasabi, Garage...) over the
same REST API, signed with AWS Signature Version 4.

- **No new dependency**: the requests go through the httpx client of network.py, so the proxy, the company
  certificate authorities and REFEX_HTTP_VERIFY of the rest of the application apply here too. SigV4 is a
  handful of HMAC-SHA256 from the standard library (see `_sign`).
- **With or without credentials**: a public bucket (or one open on the internal network) is read anonymously,
  nothing is signed and no key is needed. Credentials are only sent when both an access key and a secret key
  are configured.
- The secret key never reaches a log, a page or an error message: it is referenced as ${secret:<name>} (secret
  manager) or masked by secretstore.py, like the token of a Git source.
- Addresses are checked by network.validate_outbound() before any call: the cloud metadata service, which
  answers with the credentials of the instance, is never a legitimate endpoint.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree

import httpx

from .settings import Settings

log = logging.getLogger(__name__)

UNSIGNED = "UNSIGNED-PAYLOAD"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
CHUNK = 1 << 20
# A single PUT carries 5 GiB at most, so anything larger goes up in parts. 64 MiB x 10 000 parts allows an
# object of 640 GiB, which is the size a full backup of a large instance reaches.
PART_SIZE = 64 << 20
MAX_PARTS = 10_000
# A bucket name as the REST API accepts it, plus the shapes a compatible store allows (a dot is valid, "_" is
# not, and a name that looks like an IP address is refused by S3 itself)
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$")
# ElementTree keeps the namespace in every tag name: Amazon sends one, several compatible stores send none
_NS = re.compile(r"^\{[^}]*\}")


class S3Error(Exception):
    """Something the store answered, or a configuration it cannot be called with."""


def _text(payload: bytes, tag: str) -> str | None:
    """Text of the first `tag` of an S3 XML answer (the namespace is ignored), or None."""
    try:
        root = ElementTree.fromstring(payload or b"")
    except ElementTree.ParseError:
        return None
    if _NS.sub("", root.tag) == tag:
        return (root.text or "").strip()
    return next((((node.text or "").strip()) for node in root.iter() if _NS.sub("", node.tag) == tag), None)


def _blocks(source: Path, offset: int, length: int, progress: Callable[[int], None] | None = None) -> Iterator[bytes]:
    """`length` bytes of `source` from `offset`, read in chunks: a part is never held in memory whole."""
    with source.open("rb") as f:
        f.seek(offset)
        left = length
        while left > 0:
            block = f.read(min(CHUNK, left))
            if not block:
                return
            left -= len(block)
            if progress:
                progress(len(block))
            yield block


def key_base(prefix: str) -> str:
    """The prefix as a folder: what is cut off a key to get the path a pattern is matched against."""
    prefix = prefix.lstrip("/")
    return prefix if not prefix or prefix.endswith("/") else prefix + "/"


def glob_match(path: str, pattern: str) -> bool:
    """Glob of a path, as `Path.glob` understands it rather than as `fnmatch` does.

    A bucket has no directories, only keys that look like paths, so the pattern is matched segment by segment:
    `*` and `?` stay inside one segment (`*.csv` does **not** match `extra/other.csv`, which is what an
    administrator writing a pattern expects), and `**` matches any number of segments.
    """
    parts, patterns = path.split("/"), pattern.strip("/").split("/")

    def match(i: int, j: int) -> bool:
        if j == len(patterns):
            return i == len(parts)
        if patterns[j] == "**":
            # Any number of segments, including none
            return any(match(k, j + 1) for k in range(i, len(parts) + 1))
        if i == len(parts):
            return False
        return fnmatch.fnmatchcase(parts[i], patterns[j]) and match(i + 1, j + 1)

    return match(0, 0)


@dataclass
class S3Object:
    key: str
    size: int
    etag: str
    last_modified: str

    @property
    def name(self) -> str:
        """Last segment of the key: the file name once downloaded."""
        return self.key.rsplit("/", 1)[-1]


@dataclass
class S3Config:
    """Where a bucket is and how it is reached. `secret_access_key` is already resolved (no ${...} left)."""

    endpoint: str  # https://s3.eu-west-3.amazonaws.com, https://minio.example.com:9000...
    bucket: str
    region: str = "us-east-1"
    access_key_id: str = ""
    secret_access_key: str = ""
    session_token: str = ""  # temporary credentials (STS / IAM role)
    # Amazon uses <bucket>.<endpoint> (virtual host); MinIO and most self-hosted stores use <endpoint>/<bucket>
    path_style: bool = False
    verify_tls: bool = True

    @property
    def signed(self) -> bool:
        return bool(self.access_key_id and self.secret_access_key)

    @property
    def base_url(self) -> str:
        """Endpoint of the bucket, without a trailing slash."""
        endpoint = self.endpoint.rstrip("/")
        if self.path_style:
            return f"{endpoint}/{self.bucket}"
        parts = urlsplit(endpoint)
        return f"{parts.scheme}://{self.bucket}.{parts.netloc}{parts.path}".rstrip("/")

    def describe(self) -> str:
        """What may be shown or logged: never a credential."""
        return f"s3://{self.bucket} ({self.endpoint}{', anonymous' if not self.signed else ''})"


def check(cfg: S3Config) -> S3Config:
    """Refuse a configuration before anything is called with it."""
    from .network import UnsafeUrl, validate_outbound

    if not cfg.endpoint.strip():
        raise S3Error("the endpoint of the store is missing (e.g. https://s3.eu-west-3.amazonaws.com)")
    if not BUCKET_RE.match(cfg.bucket or ""):
        raise S3Error("invalid bucket name (lowercase letters, digits, '-' and '.', 3 to 63 characters)")
    if cfg.access_key_id and not cfg.secret_access_key:
        raise S3Error("a secret access key is needed with an access key id (leave both empty for an anonymous bucket)")
    if cfg.secret_access_key and not cfg.access_key_id:
        raise S3Error("an access key id is needed with a secret access key")
    try:
        validate_outbound(cfg.endpoint, what="S3 endpoint")
    except UnsafeUrl as e:
        raise S3Error(str(e)) from e
    return cfg


# --------------------------------------------------------------------------- Signature Version 4

def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def _canonical_query(query: dict[str, str]) -> str:
    """Parameters sorted by name, each encoded as the signature expects (every reserved character escaped)."""
    return "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(query.items()))


def _canonical_path(key: str) -> str:
    """The object key as a path: every segment encoded, the slashes kept."""
    return "/" + "/".join(quote(segment, safe="-_.~") for segment in key.split("/")) if key else "/"


def _sign(cfg: S3Config, method: str, url: str, query: dict[str, str], headers: dict[str, str],
          payload_sha256: str) -> dict[str, str]:
    """Headers to send: the given ones plus the SigV4 Authorization (unchanged when anonymous).

    AWS Signature Version 4, as every S3-compatible store implements it: a canonical request is hashed, that
    hash is signed with a key derived from the secret key for this day, this region and this service, and the
    signature travels in the Authorization header with the list of headers it covers.
    """
    parts = urlsplit(url)
    now = datetime.now(timezone.utc)
    stamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    headers = {**headers, "host": parts.netloc, "x-amz-date": stamp, "x-amz-content-sha256": payload_sha256}
    if not cfg.signed:
        # Anonymous: the store is read without credentials, so there is nothing to sign
        return {k: v for k, v in headers.items() if k != "host"}
    if cfg.session_token:
        headers["x-amz-security-token"] = cfg.session_token
    signed_names = sorted(h.lower() for h in headers)
    canonical = "\n".join([
        method,
        _canonical_path(parts.path.lstrip("/")) if parts.path.strip("/") else "/",
        _canonical_query(query),
        "".join(f"{name}:{str(headers[name]).strip()}\n" for name in signed_names),
        ";".join(signed_names),
        payload_sha256,
    ])
    scope = f"{day}/{cfg.region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(f"AWS4{cfg.secret_access_key}".encode(), day), cfg.region), "s3"), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={cfg.access_key_id}/{scope}, "
                                f"SignedHeaders={';'.join(signed_names)}, Signature={signature}")
    return {k: v for k, v in headers.items() if k != "host"}


# --------------------------------------------------------------------------- client

def _message(resp: httpx.Response, cfg: S3Config, what: str) -> str:
    """The <Message> of an S3 error, never the body as it came (it may echo what was sent)."""
    code, message = _text(resp.content, "Code"), _text(resp.content, "Message")
    detail = f": {code or ''} {message or ''}".rstrip() if code or message else ""
    hint = ""
    if resp.status_code in (401, 403):
        hint = (" (check the access key and its read permission on the bucket)" if cfg.signed
                else " (the bucket is not public: configure an access key)")
    elif resp.status_code == 404:
        hint = " (unknown bucket or object; a store that needs <endpoint>/<bucket> addressing needs 'path_style')"
    return f"{what}: HTTP {resp.status_code}{detail}{hint}"


class S3Client:
    """The few operations RefExposer needs: list, get, put, delete. One httpx client per instance."""

    def __init__(self, cfg: S3Config, settings: Settings, timeout: float | None = None):
        from .network import client

        self.cfg = check(cfg)
        self._client = client(settings, verify_tls=cfg.verify_tls, timeout=timeout, follow_redirects=False)

    def __enter__(self) -> "S3Client":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _url(self, key: str = "") -> str:
        return f"{self.cfg.base_url}{_canonical_path(key)}" if key else self.cfg.base_url + "/"

    def _request(self, method: str, key: str, *, query: dict[str, str] | None = None, what: str = "S3 request",
                 headers: dict[str, str] | None = None, content: bytes | None = None, stream: bool = False,
                 accept: tuple[int, ...] = (200,)) -> httpx.Response:
        query = query or {}
        url = self._url(key)
        payload = hashlib.sha256(content).hexdigest() if content is not None else (
            EMPTY_SHA256 if method in ("GET", "HEAD", "DELETE") else UNSIGNED)
        signed = _sign(self.cfg, method, url, query, headers or {}, payload)
        request = self._client.build_request(method, url, params=query or None, headers=signed, content=content)
        try:
            resp = self._client.send(request, stream=stream)
        except httpx.HTTPError as e:
            raise S3Error(f"{what}: {self.cfg.endpoint} unreachable ({type(e).__name__}: {e})") from e
        if resp.status_code not in accept:
            if stream:
                resp.read()
            resp.close()
            raise S3Error(_message(resp, self.cfg, what))
        return resp

    # ------------------------------------------------------------------ listing
    def list(self, prefix: str = "", limit: int = 10_000) -> list[S3Object]:
        """Objects under `prefix` (ListObjectsV2, every page). Folder markers (keys ending in '/') are left out."""
        out: list[S3Object] = []
        token = ""
        while True:
            query = {"list-type": "2", "max-keys": "1000"}
            if prefix:
                query["prefix"] = prefix
            if token:
                query["continuation-token"] = token
            resp = self._request("GET", "", query=query, what=f"listing of s3://{self.cfg.bucket}")
            root = ElementTree.fromstring(resp.content)
            for node in root:
                if _NS.sub("", node.tag) != "Contents":
                    continue
                values = {_NS.sub("", child.tag): (child.text or "") for child in node}
                key = values.get("Key", "")
                if not key or key.endswith("/"):
                    continue
                out.append(S3Object(key=key, size=int(values.get("Size") or 0),
                                    etag=(values.get("ETag") or "").strip('"'),
                                    last_modified=values.get("LastModified") or ""))
                if len(out) >= limit:
                    return out
            token = next((child.text or "" for child in root
                          if _NS.sub("", child.tag) == "NextContinuationToken"), "")
            if not token:
                return out

    def matching(self, prefix: str, pattern: str | None, limit: int = 10_000) -> list[S3Object]:
        """Objects of `prefix` whose key matches the glob `pattern` (relative to the prefix). No pattern: all of them."""
        objects = self.list(prefix, limit)
        if not pattern or pattern in ("*", "**", "**/*"):
            return sorted(objects, key=lambda o: o.key)
        base = key_base(prefix)
        kept = [o for o in objects if glob_match(o.key[len(base):] if base and o.key.startswith(base) else o.key, pattern)]
        return sorted(kept, key=lambda o: o.key)

    # ------------------------------------------------------------------ objects
    def head(self, key: str) -> S3Object:
        resp = self._request("HEAD", key, what=f"s3://{self.cfg.bucket}/{key}")
        return S3Object(key=key, size=int(resp.headers.get("content-length") or 0),
                        etag=(resp.headers.get("etag") or "").strip('"'),
                        last_modified=resp.headers.get("last-modified") or "")

    def download(self, key: str, target: Path, progress: Callable[[int], None] | None = None) -> int:
        """Stream an object into `target` (through a .part file: a partial download never replaces a good file)."""
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        written = 0
        resp = self._request("GET", key, what=f"download of s3://{self.cfg.bucket}/{key}", stream=True)
        try:
            with part.open("wb") as f:
                for block in resp.iter_bytes(CHUNK):
                    f.write(block)
                    written += len(block)
                    if progress:
                        progress(len(block))
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        finally:
            resp.close()
        part.replace(target)
        return written

    def upload(self, key: str, source: Path, progress: Callable[[int], None] | None = None) -> str:
        """Send a file as one object, without reading it into memory. Returns its ETag.

        A single PUT cannot carry more than 5 GiB, and a full backup routinely passes that, so a large file goes
        up as a **multipart upload**: parts of `PART_SIZE`, then one call to assemble them. An upload that fails
        halfway is aborted, so the store is not left paying for parts nobody will finish.
        """
        size = source.stat().st_size
        if size > PART_SIZE:
            return self._upload_parts(key, source, size, progress)
        what = f"upload to s3://{self.cfg.bucket}/{key}"
        url = self._url(key)
        headers = _sign(self.cfg, "PUT", url, {}, {"content-type": "application/octet-stream"}, UNSIGNED)
        headers["content-length"] = str(size)
        resp = self._send("PUT", url, headers, _blocks(source, 0, size, progress), what)
        return (resp.headers.get("etag") or "").strip('"')

    def _send(self, method: str, url: str, headers: dict[str, str], content: Any, what: str,
              query: dict[str, str] | None = None) -> httpx.Response:
        try:
            resp = self._client.request(method, url, params=query or None, headers=headers, content=content)
        except httpx.HTTPError as e:
            raise S3Error(f"{what}: {type(e).__name__}: {e}") from e
        if resp.status_code != 200:
            raise S3Error(_message(resp, self.cfg, what))
        return resp

    def _upload_parts(self, key: str, source: Path, size: int, progress: Callable[[int], None] | None) -> str:
        what = f"upload to s3://{self.cfg.bucket}/{key}"
        parts = (size + PART_SIZE - 1) // PART_SIZE
        if parts > MAX_PARTS:
            raise S3Error(f"{what}: {size:,} bytes is more than this upload can carry "
                          f"({MAX_PARTS} parts of {PART_SIZE // (1 << 20)} MiB)")
        started = self._request("POST", key, query={"uploads": ""}, what=f"start of the {what}", content=b"")
        upload_id = _text(started.content, "UploadId")
        if not upload_id:
            raise S3Error(f"start of the {what}: the store did not return an UploadId")
        tags: list[tuple[int, str]] = []
        try:
            for number in range(1, parts + 1):
                offset = (number - 1) * PART_SIZE
                length = min(PART_SIZE, size - offset)
                query = {"partNumber": str(number), "uploadId": upload_id}
                url = self._url(key)
                headers = _sign(self.cfg, "PUT", url, query, {}, UNSIGNED)
                headers["content-length"] = str(length)
                resp = self._send("PUT", url, headers, _blocks(source, offset, length, progress),
                                  f"part {number}/{parts} of the {what}", query)
                etag = (resp.headers.get("etag") or "").strip('"')
                if not etag:
                    raise S3Error(f"part {number}/{parts} of the {what}: the store did not return an ETag")
                tags.append((number, etag))
            body = ("<CompleteMultipartUpload>"
                    + "".join(f"<Part><PartNumber>{n}</PartNumber><ETag>&quot;{e}&quot;</ETag></Part>" for n, e in tags)
                    + "</CompleteMultipartUpload>").encode()
            done = self._request("POST", key, query={"uploadId": upload_id}, what=f"end of the {what}", content=body,
                                 headers={"content-type": "application/xml"})
            # A store may answer 200 with an <Error> body: the upload only succeeded if a result came back
            if _text(done.content, "ETag") is None and b"<Error" in done.content:
                raise S3Error(_message(done, self.cfg, f"end of the {what}"))
            return (_text(done.content, "ETag") or "").strip('"')
        except BaseException:
            # Nothing is left half-uploaded: the parts already sent are dropped
            try:
                self._request("DELETE", key, query={"uploadId": upload_id}, what=f"abort of the {what}",
                              accept=(200, 204, 404))
            except S3Error:
                log.warning("Could not abort the multipart upload of %s: parts may remain in the bucket", key)
            raise

    def delete(self, key: str) -> None:
        self._request("DELETE", key, what=f"deletion of s3://{self.cfg.bucket}/{key}", accept=(200, 204, 404))

    def check_access(self, prefix: str = "", write: bool = False) -> dict[str, Any]:
        """Used by the "Test" buttons: lists the prefix and, when asked, writes then deletes a probe object."""
        objects = self.list(prefix, limit=5)
        out: dict[str, Any] = {"ok": True, "bucket": self.cfg.bucket, "endpoint": self.cfg.endpoint,
                               "anonymous": not self.cfg.signed, "objects": [o.key for o in objects],
                               "writable": None}
        if write:
            probe = f"{prefix.rstrip('/') + '/' if prefix else ''}.refexposer-write-test"
            self._request("PUT", probe, what="write test", content=b"refexposer",
                          headers={"content-type": "text/plain"})
            self.delete(probe)
            out["writable"] = True
        return out


# --------------------------------------------------------------------------- referential sources

@dataclass
class Fetched:
    """What an update got from a bucket."""

    files: list[Path] = field(default_factory=list)
    objects: list[S3Object] = field(default_factory=list)
    bytes_downloaded: int = 0

    @property
    def fingerprint(self) -> str:
        """Identifies the set of objects as the store describes it: nothing is read again to compare versions."""
        h = hashlib.sha256()
        for obj in self.objects:
            h.update(f"{obj.key}|{obj.size}|{obj.etag}\n".encode())
        return h.hexdigest()


def source_config(src: Any, settings: Settings) -> S3Config:
    """S3Config of a referential source (SourceConfig), with ${secret:...} and ${REFEX_SOURCE_*} resolved."""
    from .config import expand_env

    endpoint = (src.endpoint or "").strip()
    secret_key = expand_env(src.secret_access_key, endpoint).strip() if src.secret_access_key else ""
    token = expand_env(src.session_token, endpoint).strip() if src.session_token else ""
    return S3Config(endpoint=endpoint, bucket=(src.bucket or "").strip(), region=(src.region or "us-east-1").strip(),
                    access_key_id=(src.access_key_id or "").strip(), secret_access_key=secret_key,
                    session_token=token, path_style=bool(src.path_style),
                    verify_tls=settings.verify is not False)


def fetch(settings: Settings, src: Any, dest: Path, log_line: Callable[[str], None] = lambda m: None,
          cancelled: Callable[[], bool] = lambda: False, guard: Any = None) -> Fetched:
    """Download the objects of `src` matching its prefix and path into `dest`, emptied first.

    `guard`: a resources.DiskGuard watching the free space while writing (an update of a bucket of tens of GB
    must stop with a clear error rather than fill the disk the database shares)."""
    cfg = source_config(src, settings)
    prefix = (src.prefix or "").lstrip("/")
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    out = Fetched()
    with S3Client(cfg, settings) as client:
        objects = client.matching(prefix, (src.path or "").strip() or None)
        if not objects:
            return out
        total = sum(o.size for o in objects)
        log_line(f"S3: {cfg.describe()}, {len(objects)} object(s) matching '{src.path or '*'}' ({total:,} bytes)")
        base = key_base(prefix)
        for obj in objects:
            if cancelled():
                from .ingest import RunCancelled

                raise RunCancelled()
            relative = obj.key[len(base):] if base and obj.key.startswith(base) else obj.key
            target = dest / relative.replace("\\", "/")
            if not target.resolve().is_relative_to(dest.resolve()):  # a key such as "../etc/passwd"
                raise S3Error(f"the object key leaves the destination folder: {obj.key}")
            client.download(obj.key, target, progress=guard.wrote if guard else None)
            out.files.append(target)
            out.objects.append(obj)
            out.bytes_downloaded += obj.size
    return out
