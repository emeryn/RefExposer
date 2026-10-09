"""Bulk discovery: one referential per data file of a Git repository or of an HTTP folder.

1. scan: the files are listed (Git: tree of the commit, without downloading the content; HTTP: links of the directory
   listing, Apache / nginx / Caddy / python http.server, one or more levels deep), filtered by a glob pattern and by the
   known data extensions. Each file becomes a candidate definition: identifier and name from the file name, Git or
   HTTP source, format from the extension.
2. analyse (optional): the files are fetched (Git: only them, in one sparse fetch; HTTP: sampled) and analysed like the
   preview of the editor: format, JSON / XML record path, columns, first rows, errors.
3. apply: the selected definitions (possibly edited) are created in one go.

Credentials of the scanned source must be ${secret:...} references: they are copied into every definition.
"""

from __future__ import annotations

import fnmatch
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from .config import SourceConfig
from .preview import _EXT_FORMATS, PreviewError, preview
from .secretstore import MASK, mask_source
from .settings import Settings
from .sync import slug, title

MAX_FILES = 1000
MAX_ANALYSED = 200
ANALYSE_THREADS = 4
_COMPRESSED = re.compile(r"\.(gz|zip|tgz|tar\.gz|tar|bz2|xz)$", re.I)
_HREF = re.compile(r"""href\s*=\s*["']([^"'#]+)["']""", re.I)
_EXTRA_FORMATS = {".txt": "txt", ".list": "txt", ".lst": "txt"}


class DiscoveryError(ValueError):
    pass


def guess_format(name: str) -> str | None:
    ext = PurePosixPath(_COMPRESSED.sub("", name.lower())).suffix
    return _EXT_FORMATS.get(ext) or _EXTRA_FORMATS.get(ext)


def _matches(path: str, pattern: str | None) -> bool:
    if not pattern or pattern in ("*", "**", "**/*"):
        return True
    return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(path, pattern.replace("**/", ""))


def _no_literal_credentials(source: dict[str, Any]) -> None:
    masked = mask_source(source) or {}
    if MASK in (masked.get("headers") or {}).values() or MASK in (masked.get("basic_auth"), masked.get("token")):
        raise DiscoveryError("credentials of a discovered source are copied into every definition: store them in the "
                             "secret manager and use ${secret:<name>} references")


# --------------------------------------------------------------------------- listing

def list_git(settings: Settings, source: dict[str, Any]) -> tuple[SourceConfig, str, list[str]]:
    from .gitsource import GitError, list_files

    try:
        src = SourceConfig.model_validate({"type": "git", "repository": (source.get("repository") or "").strip(),
                                           "ref": source.get("ref") or None, "path": source.get("path") or "**",
                                           "token": source.get("token") or None, "username": source.get("username") or None})
        commit, paths = list_files(settings, src)
    except (GitError, ValueError) as e:
        raise DiscoveryError(str(e).splitlines()[-1]) from e
    return src, commit, [p for p in paths if _matches(p, source.get("path"))]


def list_http(settings: Settings, source: dict[str, Any]) -> tuple[str, list[tuple[str, str]]]:
    """(folder URL, [(file URL, path relative to the folder)]) from the HTML directory listing(s)."""
    from .network import client

    url = (source.get("url") or "").strip()
    if not re.match(r"^https?://\S+$", url):
        raise DiscoveryError("URL of the folder expected (http:// or https://)")
    base = url if url.endswith("/") or urlparse(url).path.endswith(".html") else url + "/"
    base_dir = base.rsplit("/", 1)[0] + "/"
    depth = max(0, min(int(source.get("depth") or 0), 5))
    cfg = SourceConfig.model_validate({"type": "http", "urls": [base], "headers": source.get("headers") or {},
                                       "basic_auth": source.get("basic_auth") or None})
    found: dict[str, str] = {}
    queue, seen = [(base, 0)], {base}
    with client(settings, timeout=60) as c:
        while queue:
            folder, level = queue.pop(0)
            r = c.get(folder, headers=cfg.request_headers(folder))
            if r.status_code >= 400:
                raise DiscoveryError(f"{folder}: HTTP {r.status_code}")
            if "html" not in r.headers.get("content-type", "html").lower():
                raise DiscoveryError(f"{folder} is not a directory listing (HTML page of links expected)")
            for href in _HREF.findall(r.text):
                full = urljoin(str(r.url), href.strip()).split("?")[0]
                if not full.startswith(base_dir) or full in seen:
                    continue  # parent folder, sort links, other sites
                seen.add(full)
                rel = unquote(full[len(base_dir):])
                if full.endswith("/"):
                    if level < depth:
                        queue.append((full, level + 1))
                elif rel:
                    found[full] = rel
            if len(found) > MAX_FILES:
                break
    return base_dir, sorted(found.items(), key=lambda x: x[1])


# --------------------------------------------------------------------------- candidates

def _unique_id(base: str, taken: set[str]) -> str:
    candidate, n = base, 2
    while candidate in taken:
        suffix = f"-{n}"
        candidate, n = base[:63 - len(suffix)] + suffix, n + 1
    taken.add(candidate)
    return candidate


def _existing_sources(refs: dict[str, Any]) -> dict[tuple, str]:
    out: dict[tuple, str] = {}
    for ref in refs.values():
        if ref.source.type == "git":
            out[("git", (ref.source.repository or "").rstrip("/").removesuffix(".git"), ref.source.path)] = ref.id
        for u in ref.source.urls:
            out[("http", u)] = ref.id
    return out


def _analyse(settings: Settings, cand: dict[str, Any], src: dict[str, Any]) -> None:
    """Preview of the file: format, options (record path of a JSON document, SQLite table), columns."""
    config = cand["config"]
    try:
        result = preview(settings, src, None, {}, None)
        options = dict(result.get("applied_options") or {})
        fmt = result.get("format")
        paths = result.get("records_path_candidates") or []
        if fmt in ("json", "xml") and paths and len(result.get("rows") or []) <= 1:
            # A document holding a collection of records: read the first collection
            options["records_path"] = paths[0]
            retry = preview(settings, src, fmt, options, None)
            if not retry.get("error"):
                result = {**retry, "applied_options": options}
        cand["analysis"] = {
            "format": fmt,
            "columns": [c["name"] for c in result.get("columns") or []][:50],
            "rows": (result.get("rows") or [])[:3],
            "total_rows": result.get("total_rows"),
            "records_path_candidates": paths,
            "error": result.get("error") or result.get("unanalysed"),
        }
        if fmt:
            config["format"] = fmt
        if options:
            config["options"] = options
        if result.get("error"):
            cand["selected"] = False
    except (PreviewError, ValueError, OSError) as e:
        cand["analysis"] = {"error": str(e)}
        cand["selected"] = False
    except Exception as e:  # noqa: BLE001
        cand["analysis"] = {"error": f"{type(e).__name__}: {e}"}
        cand["selected"] = False


def scan(settings: Settings, refs: dict[str, Any], source: dict[str, Any], analyse: bool = False,
         category: str | None = None, tags: list[str] | None = None, schedule: str | None = None) -> dict[str, Any]:
    stype = source.get("type")
    if stype not in ("git", "http"):
        raise DiscoveryError("source type 'git' (repository) or 'http' (folder) expected")
    _no_literal_credentials(source)
    taken = set(refs)
    existing = _existing_sources(refs)
    candidates: list[dict[str, Any]] = []
    skipped: list[str] = []
    explicit = bool(source.get("path" if stype == "git" else "pattern")) and source.get("path" if stype == "git" else "pattern") not in ("*", "**")

    if stype == "git":
        src, commit, paths = list_git(settings, source)
        origin = f"{(src.repository or '').removeprefix('https://').removesuffix('.git')}"
        items = [(p, None) for p in paths]
        info = {"repository": src.repository, "ref": src.ref, "commit": commit}
    else:
        folder, files = list_http(settings, source)
        files = [(u, rel) for u, rel in files if _matches(rel, source.get("pattern"))]
        origin = folder
        items = [(rel, u) for u, rel in files]
        info = {"folder": folder}

    for path, url in items[:MAX_FILES]:
        name = PurePosixPath(path).name
        fmt = guess_format(name)
        if fmt is None and not explicit:
            skipped.append(path)
            continue
        if stype == "git":
            source_cfg = {"type": "git", "repository": src.repository, "ref": src.ref, "path": path,
                          "token": source.get("token") or None, "username": source.get("username") or None}
            dup = existing.get(("git", (src.repository or "").rstrip("/").removesuffix(".git"), path))
        else:
            source_cfg = {"type": "http", "urls": [url], "headers": source.get("headers") or {},
                          "basic_auth": source.get("basic_auth") or None}
            dup = existing.get(("http", url))
        ref_id = _unique_id(slug(name), taken)
        config = {
            "id": ref_id,
            "name": title(name),
            "description": f"From {origin.rstrip('/')} ({path}).",
            "category": category or "Discovered",
            "tags": list(tags or []),
            "source": {k: v for k, v in source_cfg.items() if v not in (None, {}, "")},
            "format": fmt or "csv",
        }
        if schedule:
            config["schedule"] = schedule
        candidates.append({"path": path, "url": url, "config": config, "duplicate_of": dup,
                           "selected": dup is None, "analysis": None})

    if analyse and candidates:
        todo = [c for c in candidates if c["duplicate_of"] is None][:MAX_ANALYSED]
        if stype == "git":
            settings.tmp_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="refex-discovery-", dir=settings.tmp_dir) as tmp:
                from .gitsource import GitError, fetch

                try:
                    _, files = fetch(settings, src, Path(tmp), paths=[c["path"] for c in todo])
                except (GitError, ValueError) as e:
                    raise DiscoveryError(str(e).splitlines()[-1]) from e
                local = {str(f.relative_to(tmp)).replace("\\", "/"): f for f in files}
                with ThreadPoolExecutor(ANALYSE_THREADS) as pool:
                    for c in todo:
                        f = local.get(c["path"])
                        if f is None:
                            c["analysis"], c["selected"] = {"error": "file not fetched"}, False
                            continue
                        pool.submit(_analyse, settings, c, {"type": "_files", "paths": [str(f)], "base": tmp})
        else:
            with ThreadPoolExecutor(ANALYSE_THREADS) as pool:
                for c in todo:
                    pool.submit(_analyse, settings, c, {"type": "http", "urls": [c["url"]], "headers": source.get("headers") or {},
                                                        "basic_auth": source.get("basic_auth") or None})
    return {**info, "source": mask_source(source), "count": len(candidates), "candidates": candidates,
            "skipped": skipped[:200], "skipped_count": len(skipped), "analysed": analyse,
            "truncated": len(items) > MAX_FILES}
