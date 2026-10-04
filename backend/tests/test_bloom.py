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
