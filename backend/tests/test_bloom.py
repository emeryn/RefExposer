import httpx

from conftest import refresh

HASHES = [f"{i:040X}" for i in range(1, 2001)]  # SHA-1-like, uppercase


def setup_bloom(env):
    from app.bloom import write_filter

    raw = env.data_dir / "hashes" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    write_filter(raw / "known.bloom", HASHES, p=0.0001)
    cfg = {
        "id": "hashes", "name": "Empreintes connues", "format": "bloom", "source": {"type": "local", "path": "raw/*.bloom"},
        "options": {"normalize": "upper", "pattern": "[0-9A-F]{40}", "enrich_bulk_url": "https://lookup.test/bulk/sha1", "enrich_key": "SHA-1"},
    }
    r = env.post("/api/admin/referentials", json={"config": cfg, "pull": False})
    assert r.status_code == 201, r.text
    return refresh(env, "hashes")


def test_bloom_filter_reader_roundtrip(tmp_path):
    from app.bloom import BloomFilter, fnv1_64, write_filter

    # FNV-1 64 reference values (Go hash/fnv New64)
    assert fnv1_64(b"") == 0xCBF29CE484222325
    assert fnv1_64(b"a") == 0xAF63BD4C8601B7BE
    path = tmp_path / "f.bloom"
    members = [f"member-{i}" for i in range(5000)]
    write_filter(path, members, p=0.001)
    bf = BloomFilter(path)
    assert all(bf.check(v) for v in members)  # no false negatives
    false_pos = sum(bf.check(f"other-{i}") for i in range(50000)) / 50000
    assert false_pos < 0.003, false_pos  # target 0.1 %
    assert bf.info()["elements"] == 5000 and abs(bf.estimated_fp_rate - 0.001) < 0.0005


def test_bloom_referential(env, monkeypatch):
    run = setup_bloom(env)
    assert run["status"] == "success" and run["rows"] == 2000, run
    detail = env.get("/api/referentials/hashes").json()
    assert detail["kind"] == "bloom" and detail["bloom"]["elements"] == 2000 and detail["has_data"]

    known = HASHES[42]
    r = env.get(f"/api/referentials/hashes/lookup/{known.lower()}")  # normalised to upper case
    assert r.status_code == 200 and r.json() == {"value": known, "present": True, "probabilistic": True}
    assert env.get(f"/api/referentials/hashes/lookup/{'F' * 40}").status_code == 404
    b = env.post("/api/referentials/hashes/lookup", json={"values": [known, "F" * 40, "not-a-hash"]}).json()
    assert b["found"] == 1 and sorted(b["missing"]) == sorted(["F" * 40, "NOT-A-HASH"])

    # Table-only operations are refused, the filter itself is downloadable
    assert env.get("/api/referentials/hashes/rows").status_code == 400
    assert env.get("/api/referentials/hashes/download/csv").status_code == 404
    assert env.get("/api/referentials/hashes/download/source").content[:8] == (1).to_bytes(8, "little")
    assert "hashes" not in [t["table"] for t in env.get("/api/sql/schema").json()]
    spec = env.get("/api/referentials/hashes/openapi.json").json()
    assert "/api/referentials/hashes/lookup/{value}" in spec["paths"] and "/api/referentials/hashes/rows" not in spec["paths"]
    res = env.get("/api/search", params={"q": known}).json()["results"]
    assert any(x["id"] == "hashes" and x["total"] == 1 for x in res)

    # Optional online enrichment (only for values present in the filter)
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        body = _json.loads(request.content)
        sent.extend(body["hashes"])
        return httpx.Response(200, json=[{"SHA-1": h, "FileName": "kernel32.dll"} for h in body["hashes"]])

    from app import network

    monkeypatch.setattr(network, "client", lambda *a, **k: httpx.Client(transport=httpx.MockTransport(handler)))
    b = env.post("/api/referentials/hashes/lookup", json={"values": [known, "F" * 40], "enrich": True}).json()
    assert b["results"][known]["confirmed"] is True and b["results"][known]["details"]["FileName"] == "kernel32.dll"
    assert sent == [known]  # absent values are never sent to the online service

    # Unchanged source -> no new version
    assert refresh(env, "hashes")["status"] == "unchanged"


def test_bloom_merge_and_add(tmp_path):
    import shutil

    from app.bloom import BloomError, BloomFilter, write_filter

    full, delta = tmp_path / "full.bloom", tmp_path / "delta.bloom"
    write_filter(full, [f"a{i}" for i in range(1000)], p=0.001)
    # Same size and hash functions: built for the same capacity, then filled with other values
    shutil.copyfile(full, delta)
    d = BloomFilter(delta, writable=True)
    for i in range(20):  # values of the delta only
        d.add(f"b{i}")
    d.flush()
    d.close()
    bf = BloomFilter(full, writable=True)
    assert not bf.add("a1") and bf.count == 1000  # already present: nothing changes
    assert bf.add("new-value") and bf.count == 1001
    other = BloomFilter(delta)
    bf.merge(other)
    other.close()
    assert all(bf.check(f"b{i}") for i in range(20)) and bf.check("a5")
    assert 1015 <= bf.count <= 1030, bf.count  # union estimated from the bits set
    bf.flush()
    bf.close()
    assert BloomFilter(full).info()["elements"] == bf.count

    small = tmp_path / "small.bloom"
    write_filter(small, ["x"], p=0.01)
    bf, s = BloomFilter(full, writable=True), BloomFilter(small)
    try:
        bf.merge(s)
        raise AssertionError("incompatible filters merged")
    except BloomError as e:
        assert "incompatible" in str(e)


def test_bloom_incremental(env, http_dir):
    import gzip
    import os
    import time as _time

    from app.bloom import write_filter

    www = env.www
    write_filter(www / "full.bloom", HASHES[:1000], p=0.0001)
    delta_path = www / "delta.txt.gz"

    def set_delta(values, step):
        with gzip.open(delta_path, "wt", encoding="utf-8") as f:
            f.write("# delta\n" + "\n".join(v.lower() for v in values) + "\nnot-a-hash\n")
        t = _time.time() + step * 10  # Last-Modified changes (second precision)
        os.utime(delta_path, (t, t))

    set_delta(HASHES[1000:1100], 1)
    base = f"http://127.0.0.1:{http_dir[1]}"
    cfg = {
        "id": "inc", "name": "Incremental", "format": "bloom", "source": {"urls": [f"{base}/full.bloom"]},
        "options": {"normalize": "upper", "pattern": "[0-9A-F]{40}"},
        "incremental": {"urls": [f"{base}/delta.txt.gz"], "full_every": "30d"},
    }
    r = env.post("/api/admin/referentials", json={"config": cfg, "pull": False})
    assert r.status_code == 201, r.text

    # 1. First run: full filter + delta on top
    run = refresh(env, "inc")
    assert run["status"] == "success" and run["full"] is True and run["added"] == 100, run
    assert env.get(f"/api/referentials/inc/lookup/{HASHES[1050]}").status_code == 200
    assert env.get(f"/api/referentials/inc/lookup/{HASHES[1500]}").status_code == 404
    assert any("1 line(s) ignored" in line["msg"] for line in env.get("/api/referentials/inc/runs", params={"limit": 1}).json()[0]["logs"])
    detail = env.get("/api/referentials/inc").json()
    assert detail["row_count"] == 1100 and detail["version"] == 1

    # 2. Nothing changed: no download, no new version
    assert refresh(env, "inc")["status"] == "unchanged"

    # 3. New delta: only the delta is downloaded (the full filter is not checked before 30 days)
    full_mtime = (www / "full.bloom").stat().st_mtime
    write_filter(www / "full.bloom", HASHES[:10], p=0.0001)  # would shrink the filter if it were downloaded
    os.utime(www / "full.bloom", (full_mtime + 100, full_mtime + 100))
    set_delta(HASHES[1100:1200], 2)
    run = refresh(env, "inc")
    assert run["status"] == "success" and run["full"] is False and run["added"] == 100, run
    assert env.get(f"/api/referentials/inc/lookup/{HASHES[1150]}").status_code == 200
    assert env.get(f"/api/referentials/inc/lookup/{HASHES[1050]}").status_code == 200  # earlier delta kept
    detail = env.get("/api/referentials/inc").json()
    assert detail["row_count"] == 1200 and detail["version"] == 2

    # 4. Forced run: new full filter, the current delta applied again on top
    write_filter(www / "full.bloom", HASHES[:1000] + HASHES[1300:1600], p=0.0001)
    os.utime(www / "full.bloom", (full_mtime + 200, full_mtime + 200))
    run = refresh(env, "inc", force=True)
    assert run["status"] == "success" and run["full"] is True and run["added"] == 100, run
    assert env.get(f"/api/referentials/inc/lookup/{HASHES[1450]}").status_code == 200
    assert env.get(f"/api/referentials/inc/lookup/{HASHES[1150]}").status_code == 200  # from the delta
    assert env.get(f"/api/referentials/inc/lookup/{HASHES[1050]}").status_code == 404  # older delta: replaced by the full

    # 5. Saturation: a delta far beyond the capacity of the filter is rejected, the published version is kept
    set_delta([f"{i:040X}" for i in range(10**6, 10**6 + 20000)], 3)
    run = refresh(env, "inc")
    assert run["status"] == "rejected" and "saturated" in run["message"], run
    assert env.get("/api/referentials/inc").json()["row_count"] == 1400


def test_incremental_config_validation():
    import pytest

    from app.config import validate_definition

    base = {"id": "x", "name": "x", "source": {"urls": ["https://a/full.bloom"]}, "incremental": {"urls": ["https://a/d.txt"]}}
    assert validate_definition({**base, "format": "bloom"}).incremental.format == "values"
    with pytest.raises(ValueError, match="only applies to a Bloom filter"):
        validate_definition({**base, "format": "csv"})
    with pytest.raises(ValueError, match="invalid duration"):
        validate_definition({**base, "format": "bloom", "incremental": {"urls": ["https://a/d"], "full_every": "1 month"}})


def test_preview_remote_bloom_reads_header_only(env, http_dir):
    from app.bloom import write_filter

    values = [f"{i:040X}" for i in range(1, 20001)]
    write_filter(env.www / "big.bloom", values, p=0.0001)
    size = (env.www / "big.bloom").stat().st_size
    assert size > 8192
    url = f"http://127.0.0.1:{http_dir[1]}/big.bloom"
    p = env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": [url]}}).json()
    assert p["error"] is None and p["detected_format"] == "bloom" and p["sampled"], p
    assert p["total_rows"] == 20000 and ["size", str(size)] in p["rows"], p["rows"]
    assert any("only its header is read" in line for line in p["logs"])
    assert p["files"][0]["size"] <= 4096  # the whole filter was not downloaded

    # The import downloads the whole filter
    cfg = {"id": "big", "name": "Big", "format": "bloom", "source": {"urls": [url]}, "options": {"normalize": "upper"}}
    assert env.post("/api/admin/referentials", json={"config": cfg, "pull": False}).status_code == 201
    run = refresh(env, "big")
    assert run["status"] == "success" and run["rows"] == 20000, run
    assert env.get(f"/api/referentials/big/lookup/{values[7]}").status_code == 200

    # A file that is not a Bloom filter is reported by the preview
    (env.www / "bad.bloom").write_bytes(b"not a bloom filter" * 10)
    p = env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": [url.replace("big", "bad")]}}).json()
    assert p["error"] and "Bloom" in p["error"], p
