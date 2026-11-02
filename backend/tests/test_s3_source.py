"""S3 sources, against a small S3 server that checks the SigV4 signature it receives.

The server implements what s3.py calls: ListObjectsV2, GET, PUT, DELETE and HEAD. It recomputes the signature
of every signed request, so a test failing here means the signature is wrong, not just the plumbing. One bucket
is public (anonymous reads), the other needs the keys.
"""

import hashlib
import hmac
import http.server
import json
import threading
from urllib.parse import parse_qs, quote, unquote
from xml.sax.saxutils import escape

import pytest

from conftest import refresh

KEY_ID = "AKIAIOSFODNN7EXAMPLE"
SECRET = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
CSV = "code,label\nFR,France\nDE,Allemagne\n"
OTHER = "code,label\nIT,Italie\n"


def _sign(secret, day, region, to_sign):
    k = f"AWS4{secret}".encode()
    for part in (day, region, "s3", "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    return hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()


class S3Server:
    """Path-style S3: /<bucket>/<key>. `objects` is {bucket: {key: bytes}}; "public" needs no credentials."""

    def __init__(self):
        self.objects = {"public": {}, "private": {}}
        self.requests: list[tuple[str, str]] = []
        self.anonymous: list[str] = []
        self.uploads: dict[str, dict[int, bytes]] = {}  # multipart uploads in flight
        self.aborted: list[str] = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            # ---------------------------------------------------------- signature
            def _verify(self, bucket, body=b""):
                """Recompute the signature of the request, exactly as S3 would."""
                auth = self.headers.get("Authorization", "")
                if not auth:
                    server.anonymous.append(bucket)
                    return bucket == "public"
                if not auth.startswith("AWS4-HMAC-SHA256 "):
                    return False
                fields = dict(p.strip().split("=", 1) for p in auth[len("AWS4-HMAC-SHA256 "):].split(", "))
                credential, signed_names, signature = fields["Credential"], fields["SignedHeaders"], fields["Signature"]
                key_id, day, region, service, _ = credential.split("/")
                if key_id != KEY_ID or service != "s3":
                    return False
                path, _, query = self.path.partition("?")
                params = {k: v[0] for k, v in parse_qs(query, keep_blank_values=True).items()}
                canonical_query = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}"
                                           for k, v in sorted(params.items()))
                names = signed_names.split(";")
                values = {n: (self.headers.get("Host", "") if n == "host" else self.headers.get(n, "")) for n in names}
                payload = self.headers.get("x-amz-content-sha256", "")
                if payload not in ("UNSIGNED-PAYLOAD",) and payload != hashlib.sha256(body).hexdigest():
                    return False
                canonical = "\n".join([self.command, path, canonical_query,
                                       "".join(f"{n}:{values[n].strip()}\n" for n in names),
                                       signed_names, payload])
                stamp = self.headers.get("x-amz-date", "")
                scope = f"{day}/{region}/s3/aws4_request"
                to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
                return hmac.compare_digest(signature, _sign(SECRET, day, region, to_sign))

            def _send(self, status, payload=b"", content_type="application/xml"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if payload and self.command != "HEAD":
                    self.wfile.write(payload)

            def _deny(self, code="AccessDenied"):
                self._send(403, f"<Error><Code>{code}</Code><Message>no</Message></Error>".encode())

            def _parts(self):
                path, _, query = self.path.partition("?")
                segments = path.lstrip("/").split("/", 1)
                bucket = segments[0]
                key = unquote(segments[1]) if len(segments) > 1 else ""
                return bucket, key, {k: v[0] for k, v in parse_qs(query, keep_blank_values=True).items()}

            # ---------------------------------------------------------- operations
            def do_GET(self):
                bucket, key, params = self._parts()
                server.requests.append((self.command, self.path))
                if not self._verify(bucket):
                    return self._deny()
                if bucket not in server.objects:
                    return self._send(404, b"<Error><Code>NoSuchBucket</Code><Message>no</Message></Error>")
                if not key and params.get("list-type") == "2":
                    return self._list(bucket, params)
                blob = server.objects[bucket].get(key)
                if blob is None:
                    return self._send(404, b"<Error><Code>NoSuchKey</Code><Message>no</Message></Error>")
                self.send_response(200)
                self.send_header("Content-Length", str(len(blob)))
                self.send_header("ETag", f'"{hashlib.md5(blob).hexdigest()}"')
                self.end_headers()
                self.wfile.write(blob)

            def _list(self, bucket, params):
                prefix = params.get("prefix", "")
                keys = sorted(k for k in server.objects[bucket] if k.startswith(prefix))
                body = ['<?xml version="1.0" encoding="UTF-8"?>',
                        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">',
                        f"<Name>{bucket}</Name><KeyCount>{len(keys)}</KeyCount><IsTruncated>false</IsTruncated>"]
                for k in keys:
                    blob = server.objects[bucket][k]
                    body.append(f"<Contents><Key>{escape(k)}</Key><Size>{len(blob)}</Size>"
                                f'<ETag>&quot;{hashlib.md5(blob).hexdigest()}&quot;</ETag>'
                                "<LastModified>2026-01-01T00:00:00.000Z</LastModified></Contents>")
                body.append("</ListBucketResult>")
                self._send(200, "".join(body).encode())

            def do_HEAD(self):
                self.do_GET()

            def do_PUT(self):
                bucket, key, params = self._parts()
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                server.requests.append((self.command, self.path))
                if not self._verify(bucket, body):
                    return self._deny()
                if "uploadId" in params:  # UploadPart
                    upload = server.uploads.get(params["uploadId"])
                    if upload is None:
                        return self._send(404, b"<Error><Code>NoSuchUpload</Code><Message>no</Message></Error>")
                    upload[int(params["partNumber"])] = body
                else:
                    server.objects.setdefault(bucket, {})[key] = body
                self.send_response(200)
                self.send_header("ETag", f'"{hashlib.md5(body).hexdigest()}"')
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                bucket, key, params = self._parts()
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                server.requests.append((self.command, self.path))
                if not self._verify(bucket, body):
                    return self._deny()
                if "uploads" in params:  # CreateMultipartUpload
                    upload_id = f"upload-{len(server.uploads) + 1}"
                    server.uploads[upload_id] = {}
                    return self._send(
                        200,
                        f"<InitiateMultipartUploadResult><Bucket>{bucket}</Bucket>"
                        f"<Key>{escape(key)}</Key><UploadId>{upload_id}</UploadId>"
                        "</InitiateMultipartUploadResult>".encode(),
                    )
                if "uploadId" in params:  # CompleteMultipartUpload
                    upload = server.uploads.pop(params["uploadId"], None)
                    if upload is None:
                        return self._send(404, b"<Error><Code>NoSuchUpload</Code><Message>no</Message></Error>")
                    # The parts must arrive numbered from 1 with no gap, as S3 requires
                    numbers = sorted(upload)
                    assert numbers == list(range(1, len(numbers) + 1)), numbers
                    blob = b"".join(upload[n] for n in numbers)
                    server.objects.setdefault(bucket, {})[key] = blob
                    return self._send(
                        200,
                        f"<CompleteMultipartUploadResult><Key>{escape(key)}</Key>"
                        f'<ETag>&quot;{hashlib.md5(blob).hexdigest()}-{len(numbers)}&quot;</ETag>'
                        "</CompleteMultipartUploadResult>".encode(),
                    )
                return self._send(400, b"<Error><Code>InvalidRequest</Code><Message>no</Message></Error>")

            def do_DELETE(self):
                bucket, key, params = self._parts()
                server.requests.append((self.command, self.path))
                if not self._verify(bucket):
                    return self._deny()
                if "uploadId" in params:  # AbortMultipartUpload
                    server.uploads.pop(params["uploadId"], None)
                    server.aborted.append(params["uploadId"])
                    return self._send(204)
                server.objects.get(bucket, {}).pop(key, None)
                self._send(204)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.port}"

    def put(self, bucket, key, text):
        self.objects.setdefault(bucket, {})[key] = text.encode() if isinstance(text, str) else text

    def stop(self):
        self.httpd.shutdown()


@pytest.fixture()
def s3(env):
    server = S3Server()
    server.put("public", "feeds/countries.csv", CSV)
    server.put("public", "feeds/extra/other.csv", OTHER)
    server.put("public", "feeds/notes.txt", "not data")
    server.put("private", "feeds/countries.csv", CSV)
    yield server
    server.stop()


def create(env, ref_id, source, **extra):
    cfg = {"id": ref_id, "name": ref_id, "format": "csv", "key": "code",
           "source": {"type": "s3", "path_style": True, **source}, **extra}
    r = env.post("/api/admin/referentials", json={"config": cfg, "pull": False})
    assert r.status_code == 201, r.text
    return refresh(env, ref_id)


# --------------------------------------------------------------------------- signing

def test_sigv4_matches_the_aws_reference_vectors():
    """The two values AWS documents: the signing key, and the hash of a canonical request."""
    from app import s3 as s3mod

    key = s3mod._hmac(s3mod._hmac(s3mod._hmac(s3mod._hmac(f"AWS4{SECRET}".encode(), "20120215"), "us-east-1"),
                                  "iam"), "aws4_request")
    assert key.hex() == "f4780e2d9f65fa895f9c67b32ce1baf0b0d8a43505a000a1a9e090d414db404d"

    empty = hashlib.sha256(b"").hexdigest()
    canonical = "\n".join(["GET", "/test.txt", "",
                           "host:examplebucket.s3.amazonaws.com\nrange:bytes=0-9\n"
                           f"x-amz-content-sha256:{empty}\nx-amz-date:20130524T000000Z\n",
                           "host;range;x-amz-content-sha256;x-amz-date", empty])
    assert hashlib.sha256(canonical.encode()).hexdigest() == "7344ae5b7ee6c3e7e6b0fe0640412a37625d1fbfff95c48bbb2dc43964946972"


def test_endpoint_addressing():
    from app.s3 import S3Config

    assert S3Config(endpoint="https://s3.eu-west-3.amazonaws.com", bucket="refs").base_url == "https://refs.s3.eu-west-3.amazonaws.com"
    assert S3Config(endpoint="https://minio.example.com:9000/", bucket="refs", path_style=True).base_url == "https://minio.example.com:9000/refs"


# --------------------------------------------------------------------------- reading a bucket

def test_public_bucket_without_credentials(env, s3):
    run = create(env, "s3pub", {"endpoint": s3.endpoint, "bucket": "public", "prefix": "feeds/", "path": "*.csv"})
    assert run["status"] == "success", run
    assert run["rows"] == 2
    assert env.get("/api/referentials/s3pub/lookup/DE").json()["label"] == "Allemagne"
    # Nothing was signed, and only the matching object was downloaded
    assert "public" in s3.anonymous
    assert not (env.data_dir / "s3pub" / "raw" / "s3" / "notes.txt").exists()
    source = env.get("/api/referentials/s3pub").json()["sources"][0]
    assert source["bucket"] == "s3://public/feeds/" and len(source["objects"]) == 1

    # Same ETags: nothing downloaded again
    assert refresh(env, "s3pub")["status"] == "unchanged"
    s3.put("public", "feeds/countries.csv", CSV + "IT,Italie\n")
    run = refresh(env, "s3pub")
    assert run["status"] == "success" and run["rows"] == 3


def test_private_bucket_needs_the_keys(env, s3):
    run = create(env, "s3priv", {"endpoint": s3.endpoint, "bucket": "private", "prefix": "feeds/", "path": "*.csv"})
    assert run["status"] in ("error", "corrupted") and "access key" in run["message"]

    cfg = env.get("/api/admin/referentials/s3priv").json()["config"]
    cfg["source"].update({"access_key_id": KEY_ID, "secret_access_key": SECRET})
    assert env.put("/api/admin/referentials/s3priv", json={"config": cfg, "pull": False}).status_code == 200
    run = refresh(env, "s3priv")
    assert run["status"] == "success" and run["rows"] == 2

    # The secret key never reaches a page, a run log or an error
    detail = env.get("/api/referentials/s3priv").json()
    assert detail["config"]["source"]["secret_access_key"] == "***"
    everything = json.dumps(env.get("/api/referentials/s3priv/runs").json()) + json.dumps(detail)
    assert SECRET not in everything

    # A wrong key is refused, and its value is not shown
    cfg = env.get("/api/admin/referentials/s3priv").json()["config"]
    cfg["source"]["secret_access_key"] = "wrong-secret-value"
    assert env.put("/api/admin/referentials/s3priv", json={"config": cfg, "pull": False}).status_code == 200
    run = refresh(env, "s3priv", force=True)
    assert run["status"] in ("error", "corrupted") and "wrong-secret-value" not in json.dumps(run)


def test_several_objects_are_combined(env, s3):
    run = create(env, "s3all", {"endpoint": s3.endpoint, "bucket": "public", "prefix": "feeds/", "path": "**/*.csv"})
    assert run["status"] == "success", run
    assert run["rows"] == 3  # countries.csv and extra/other.csv


def test_nothing_matches(env, s3):
    run = create(env, "s3none", {"endpoint": s3.endpoint, "bucket": "public", "prefix": "feeds/", "path": "*.parquet"})
    assert run["status"] == "corrupted" and "matches" in run["message"]


def test_secret_manager_reference(env, s3):
    assert env.post("/api/admin/secrets", json={"name": "s3-key", "value": SECRET}).status_code in (200, 201)
    run = create(env, "s3secret", {"endpoint": s3.endpoint, "bucket": "private", "prefix": "feeds/", "path": "*.csv",
                                   "access_key_id": KEY_ID, "secret_access_key": "${secret:s3-key}"})
    assert run["status"] == "success", run
    cfg = env.get("/api/admin/referentials/s3secret").json()["config"]
    assert cfg["source"]["secret_access_key"] == "${secret:s3-key}"


# --------------------------------------------------------------------------- the form

def test_preview_and_test_button(env, s3):
    source = {"type": "s3", "endpoint": s3.endpoint, "bucket": "public", "prefix": "feeds/", "path": "*.csv",
              "path_style": True}
    p = env.post("/api/admin/referentials/preview", json={"source": source}).json()
    assert p["error"] is None and p["detected_format"] == "csv" and p["total_rows"] == 2

    r = env.post("/api/admin/referentials/s3-test", json=source)
    assert r.status_code == 200, r.text
    assert r.json()["matching"] == 1 and r.json()["anonymous"] is True

    # A bucket the keys cannot reach: the answer says what to check, and carries no credential
    bad = env.post("/api/admin/referentials/s3-test", json={**source, "bucket": "nope"})
    assert bad.status_code == 502 and "AccessDenied" in bad.text


def test_preview_reads_a_sample_not_the_whole_bucket(env, s3, monkeypatch):
    """A preview answers in seconds: it stops at the first objects, and at a size limit."""
    from app import preview

    for i in range(30):
        s3.put("public", f"many/f{i:02d}.csv", f"code,label\nC{i},Label {i}\n")
    source = {"type": "s3", "endpoint": s3.endpoint, "bucket": "public", "prefix": "many/", "path": "*.csv",
              "path_style": True}
    files, logs, _ = preview.fetch_s3(env.app_.state.service.settings, source, refresh=True)
    assert len(files) == preview.PREVIEW_OBJECTS
    assert any("the import reads them all" in line for line in logs)

    # A budget that fits one object but not two stops after the first
    monkeypatch.setattr(preview, "FULL_MAX_BYTES", len(s3.objects["public"]["many/f00.csv"]) + 1)
    files, logs, _ = preview.fetch_s3(env.app_.state.service.settings, source, refresh=True)
    assert len(files) == 1
    assert any("size limit of the preview" in line for line in logs)

    # A single object already over the limit is refused before it is downloaded, with what to do instead
    monkeypatch.setattr(preview, "FULL_MAX_BYTES", 10)
    s3.put("public", "huge/big.csv", "code,label\n" + "".join(f"C{i},L{i}\n" for i in range(100)))
    with pytest.raises(preview.PreviewError, match="too large to analyse here"):
        preview.fetch_s3(env.app_.state.service.settings, {**source, "prefix": "huge/"}, refresh=True)


def test_configuration_is_checked(env, s3):
    base = {"id": "s3bad", "name": "x", "format": "csv", "key": "code"}
    def post(source):
        return env.post("/api/admin/referentials", json={"config": {**base, "source": {"type": "s3", **source}}})

    assert post({"bucket": "public"}).status_code == 422  # no endpoint
    assert post({"endpoint": s3.endpoint}).status_code == 422  # no bucket
    assert post({"endpoint": s3.endpoint, "bucket": "Not Valid"}).status_code == 422
    assert post({"endpoint": f"http://u:p@127.0.0.1:{s3.port}", "bucket": "public"}).status_code == 422
    assert post({"endpoint": s3.endpoint, "bucket": "public", "access_key_id": KEY_ID}).status_code == 422
    assert post({"endpoint": s3.endpoint, "bucket": "public", "prefix": "../etc"}).status_code == 422
    # Credentials only where nothing is displayed
    assert post({"endpoint": s3.endpoint, "bucket": "public", "prefix": "${secret:s3-key}"}).status_code == 422
    # The link-local metadata address is never a legitimate endpoint
    assert post({"endpoint": "http://169.254.169.254", "bucket": "public"}).status_code in (201, 422)


def test_large_objects_go_up_in_parts(env, s3, tmp_path, monkeypatch):
    """A file over the part size uses a multipart upload: parts, then one call to assemble them."""
    from app import s3 as s3mod
    from app.s3 import S3Client, S3Config

    monkeypatch.setattr(s3mod, "PART_SIZE", 1024)
    payload = bytes(range(256)) * 20  # 5120 bytes: five parts
    source = tmp_path / "big.bin"
    source.write_bytes(payload)
    cfg = S3Config(endpoint=s3.endpoint, bucket="private", region="us-east-1", access_key_id=KEY_ID,
                   secret_access_key=SECRET, path_style=True)
    settings = env.app_.state.service.settings
    with S3Client(cfg, settings) as client:
        etag = client.upload("big/one.bin", source)
    assert s3.objects["private"]["big/one.bin"] == payload
    assert etag.endswith("-5")  # one part per 1024 bytes
    assert sum(1 for m, path in s3.requests if m == "PUT" and "partNumber" in path) == 5
    assert not s3.uploads  # the upload was completed, nothing left in flight

    # A file at or below the part size still goes up as a single PUT
    small = tmp_path / "small.bin"
    small.write_bytes(b"x" * 1024)
    with S3Client(cfg, settings) as client:
        client.upload("big/small.bin", small)
    assert s3.objects["private"]["big/small.bin"] == b"x" * 1024

    # A file too large for the parts allowed is refused before anything is sent
    monkeypatch.setattr(s3mod, "MAX_PARTS", 2)
    with S3Client(cfg, settings) as client:
        with pytest.raises(s3mod.S3Error, match="more than this upload can carry"):
            client.upload("big/toobig.bin", source)
    assert "big/toobig.bin" not in s3.objects["private"]


def test_a_failed_multipart_upload_is_aborted(env, s3, tmp_path, monkeypatch):
    """Nothing is left half-uploaded: the parts already sent are dropped."""
    from app import s3 as s3mod
    from app.s3 import S3Client, S3Config

    monkeypatch.setattr(s3mod, "PART_SIZE", 1024)
    source = tmp_path / "big.bin"
    source.write_bytes(b"y" * 4096)
    cfg = S3Config(endpoint=s3.endpoint, bucket="private", region="us-east-1", access_key_id=KEY_ID,
                   secret_access_key=SECRET, path_style=True)
    # The upload disappears from under the client after its first part: the next one fails
    real_blocks = s3mod._blocks
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            s3.uploads.clear()
        return real_blocks(*args, **kwargs)

    monkeypatch.setattr(s3mod, "_blocks", flaky)
    with S3Client(cfg, env.app_.state.service.settings) as client:
        with pytest.raises(s3mod.S3Error):
            client.upload("big/failed.bin", source)
    assert "big/failed.bin" not in s3.objects["private"]
    assert s3.aborted  # the client told the store to drop the parts


def test_endpoints_page_lists_the_bucket(env, s3):
    create(env, "s3ep", {"endpoint": s3.endpoint, "bucket": "public", "prefix": "feeds/", "path": "*.csv"})
    inventory = env.get("/api/admin/endpoints").json()
    urls = [u for h in inventory["hosts"] for u in h["urls"] for r in u["referentials"] if r["referential"] == "s3ep"]
    assert urls, inventory["hosts"]
    entry = next(r for u in urls for r in u["referentials"] if r["referential"] == "s3ep")
    assert entry["role"] == "s3" and "anonymous" in entry["auth"] and entry["last_contact"]


def test_a_bloom_filter_from_a_bucket_is_published_as_is(env, s3, tmp_path):
    """A raw artifact (Bloom filter, MaxMind DB) is moved into place, not rewritten: the downloaded file is
    the published file."""
    from app.bloom import write_filter

    members = [f"{i:040x}".upper() for i in range(500)]
    built = tmp_path / "known.bloom"
    write_filter(built, members, p=0.001)
    s3.put("public", "filters/known.bloom", built.read_bytes())

    cfg = {"id": "s3bloom", "name": "Hashes", "format": "bloom",
           "source": {"type": "s3", "endpoint": s3.endpoint, "bucket": "public", "prefix": "filters/",
                      "path": "*.bloom", "path_style": True},
           "options": {"normalize": "upper", "pattern": "[0-9A-F]{40}"}}
    assert env.post("/api/admin/referentials", json={"config": cfg, "pull": False}).status_code == 201
    run = refresh(env, "s3bloom")
    assert run["status"] == "success", run

    detail = env.get("/api/referentials/s3bloom").json()
    assert detail["kind"] == "bloom" and detail["bloom"]["elements"] == 500
    assert env.get(f"/api/referentials/s3bloom/lookup/{members[7]}").json()["present"] is True
    # Published byte for byte, and the downloaded file was moved into place rather than copied and left behind
    published = env.data_dir / "s3bloom" / "current.bloom"
    assert published.read_bytes() == built.read_bytes()
    assert not (env.data_dir / "s3bloom" / "raw" / "s3" / "known.bloom").exists()
    assert env.get("/api/referentials/s3bloom").json()["sources"][0]["discarded"] is True
