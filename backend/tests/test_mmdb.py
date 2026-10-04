"""MaxMind DB referentials (GeoIP): IP lookups, raw download for tools, safe updates."""

import base64
import io
import os
import tarfile
import tempfile
import time
from email.utils import formatdate

import pytest
from conftest import create_user, new_client, refresh
from mmdb_writer import MMDBWriter
from netaddr import IPSet

from app.config import SourceConfig
from app.geoip import MARKER


def build_mmdb(database_type="GeoLite2-City", city="London"):
    w = MMDBWriter(ip_version=6, ipv4_compatible=True, database_type=database_type, languages=["en"], description={"en": "Test database"})
    w.insert_network(IPSet(["81.2.69.0/24"]), {
        "country": {"iso_code": "GB", "names": {"en": "United Kingdom", "fr": "Royaume-Uni"}},
        "city": {"names": {"en": city}},
        "subdivisions": [{"iso_code": "ENG", "names": {"en": "England"}}],
    })
    w.insert_network(IPSet(["2001:db8::/32"]), {"country": {"iso_code": "FR", "names": {"en": "France"}}})
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "db.mmdb")
        w.to_db_file(path)
        with open(path, "rb") as f:
            return f.read()


def publish_tar(www, data: bytes, edition="GeoLite2-City", date="20261001"):
    """Same layout as the MaxMind downloads: <edition>_<date>/ with the database, COPYRIGHT.txt and LICENSE.txt."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in [(f"{edition}.mmdb", data), ("COPYRIGHT.txt", b"(c) test"), ("LICENSE.txt", b"license")]:
            info = tarfile.TarInfo(f"{edition}_{date}/{name}")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    target = www / f"{edition}.tar.gz"
    previous = target.stat().st_mtime if target.exists() else 0
    target.write_bytes(buf.getvalue())
    stamp = max(time.time(), previous + 10)  # a later date than the previous file: no HTTP 304 within the same second
    os.utime(target, (stamp, stamp))


def wait_run(env, ref_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = env.get(f"/api/referentials/{ref_id}").json()
        if not d["current_run"]:
            return env.get(f"/api/referentials/{ref_id}/runs", params={"limit": 1}).json()[0]
        time.sleep(0.1)
    raise TimeoutError(ref_id)


@pytest.fixture()
def geo(env):
    publish_tar(env.www, build_mmdb())
    port = env.get("/api/admin/referentials/remote").json()["config"]["source"]["urls"][0].split(":")[2].split("/")[0]
    url = f"http://127.0.0.1:{port}/GeoLite2-City.tar.gz"
    preview = env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": [url]}}).json()
    assert preview["detected_format"] == "mmdb" and preview["error"] is None, preview
    assert ["database_type", "GeoLite2-City"] in preview["rows"]
    r = env.post("/api/admin/referentials", json={"config": {"id": "geoip-city", "name": "GeoIP City", "format": "mmdb",
                                                              "source": {"type": "http", "urls": [url]}}})
    assert r.status_code == 201, r.text
    run = wait_run(env, "geoip-city")
    assert run["status"] == "success", run
    return url


def test_lookup_and_search(env, geo):
    d = env.get("/api/referentials/geoip-city").json()
    assert d["kind"] == "mmdb" and d["mmdb"]["database_type"] == "GeoLite2-City" and d["mmdb"]["file_name"] == "GeoLite2-City.mmdb"

    rec = env.get("/api/referentials/geoip-city/lookup/81.2.69.142").json()
    assert rec["network"] == "81.2.69.0/24" and rec["ip"] == "81.2.69.142"
    assert rec["country"]["names"]["fr"] == "Royaume-Uni" and rec["city"]["names"]["en"] == "London"
    flat = env.get("/api/referentials/geoip-city/lookup/81.2.69.142", params={"flat": True}).json()
    assert flat["country.iso_code"] == "GB" and flat["city.name"] == "London" and flat["subdivisions.0.name"] == "England"
    assert "country.names.fr" not in flat
    assert env.get("/api/referentials/geoip-city/lookup/2001:db8::1").json()["network"] == "2001:db8::/32"
    assert env.get("/api/referentials/geoip-city/lookup/8.8.8.8").status_code == 404
    assert env.get("/api/referentials/geoip-city/lookup/not-an-ip").status_code == 400

    batch = env.post("/api/referentials/geoip-city/lookup", json={"values": ["81.2.69.1", "8.8.8.8", "nope"], "flat": True}).json()
    assert batch["found"] == 1 and batch["missing"] == ["8.8.8.8", "nope"] and batch["results"]["81.2.69.1"]["country.name"] == "United Kingdom"

    res = {r["id"]: r for r in env.get("/api/search", params={"q": "81.2.69.200"}).json()["results"]}["geoip-city"]
    assert res["total"] == 1 and res["rows"][0]["country.iso_code"] == "GB"
    assert {r["id"]: r for r in env.get("/api/search", params={"q": "london"}).json()["results"]}["geoip-city"]["total"] == 0

    # No table: rows, SQL and exports are refused, the documentation describes the lookups and the raw file
    assert env.get("/api/referentials/geoip-city/rows").status_code == 400
    assert "geoip_city" not in [t["table"] for t in env.get("/api/sql/schema").json()]
    spec = env.get("/api/referentials/geoip-city/openapi.json").json()
    assert "/api/referentials/geoip-city/raw" in spec["paths"] and "/api/referentials/geoip-city/lookup/{ip}" in spec["paths"]


def test_raw_download_for_tools(env, geo):
    raw = env.get("/api/referentials/geoip-city/raw")
    assert raw.status_code == 200 and MARKER in raw.content
    assert 'filename="GeoLite2-City.mmdb"' in raw.headers["content-disposition"]
    etag, modified = raw.headers["etag"], raw.headers["last-modified"]
    assert raw.headers["x-checksum-sha256"] in etag
    assert env.get("/api/referentials/geoip-city/download/mmdb").content == raw.content

    # Conditional requests: nothing transferred when the file did not change
    assert env.get("/api/referentials/geoip-city/raw", headers={"If-None-Match": etag}).status_code == 304
    assert env.get("/api/referentials/geoip-city/raw", headers={"If-Modified-Since": modified}).status_code == 304
    old = formatdate(time.time() - 86400 * 365, usegmt=True)
    assert env.get("/api/referentials/geoip-city/raw", headers={"If-Modified-Since": old}).status_code == 200
    head = env.head("/api/referentials/geoip-city/raw")
    assert head.status_code == 200 and head.content == b"" and int(head.headers["content-length"]) == len(raw.content)

    # Tools: Bearer token, or HTTP Basic with the token as password (wget/curl -u), after a Basic challenge
    create_user(env, "logstash")
    tool = new_client(env, "logstash", "Us3r-Passw0rd!")
    token = tool.post("/api/auth/tokens", json={"name": "geoip"}).json()["token"]
    env.post("/api/admin/grants", json={"referential_id": "geoip-city", "user_id": tool.get("/api/auth/me").json()["id"]})
    anon = new_client(env)
    anon.headers.pop("X-Requested-With")
    challenge = anon.get("/api/referentials/geoip-city/raw")
    assert challenge.status_code == 401 and "Basic" in challenge.headers["www-authenticate"]
    basic = "Basic " + base64.b64encode(f"logstash:{token}".encode()).decode()
    assert anon.get("/api/referentials/geoip-city/raw", headers={"Authorization": basic}).content == raw.content
    assert anon.get("/api/referentials/geoip-city/raw", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    bad = "Basic " + base64.b64encode(b"logstash:Us3r-Passw0rd!").decode()  # passwords are not accepted, tokens only
    assert anon.get("/api/referentials/geoip-city/raw", headers={"Authorization": bad}).status_code == 401
    # The web interface never gets a Basic challenge (no browser sign-in dialog)
    ui = new_client(env)
    assert "Basic" not in ui.get("/api/referentials/geoip-city/raw").headers["www-authenticate"]


def test_updates_are_checked(env, geo):
    publish_tar(env.www, build_mmdb(city="Londres"))
    run = refresh(env, "geoip-city", force=False)
    assert run["status"] == "success"
    assert env.get("/api/referentials/geoip-city/lookup/81.2.69.1").json()["city"]["names"]["en"] == "Londres"
    assert env.get("/api/referentials/geoip-city/download/previous").status_code == 200

    # Another database type behind the same URL is refused unless forced
    publish_tar(env.www, build_mmdb(database_type="GeoLite2-ASN"))
    run = refresh(env, "geoip-city")
    assert run["status"] == "rejected" and "database type changed" in run["message"]
    assert env.get("/api/referentials/geoip-city").json()["mmdb"]["database_type"] == "GeoLite2-City"
    assert refresh(env, "geoip-city", force=True)["status"] == "success"

    # A broken file never replaces the published database
    (env.www / "GeoLite2-City.tar.gz").write_bytes(b"<html>Error</html>")
    assert refresh(env, "geoip-city", force=True)["status"] in ("corrupted", "error")
    assert env.get("/api/referentials/geoip-city/lookup/81.2.69.1").status_code == 200


def test_source_credentials(monkeypatch):
    monkeypatch.setenv("REFEX_SOURCE_MAXMIND_KEY", "s3cret")
    src = SourceConfig(urls=["https://download.maxmind.com/geoip/databases/GeoLite2-City/download?suffix=tar.gz"],
                       basic_auth="123456:${REFEX_SOURCE_MAXMIND_KEY}", headers={"X-Key": "${REFEX_SOURCE_MAXMIND_KEY}"})
    headers = src.request_headers()
    assert headers["Authorization"] == "Basic " + base64.b64encode(b"123456:s3cret").decode() and headers["X-Key"] == "s3cret"
    # Only REFEX_SOURCE_* variables are expanded: other secrets can never be sent to a source
    assert SourceConfig(urls=["x"], headers={"X": "${REFEX_SECRET_KEY}"}).request_headers()["X"] == "${REFEX_SECRET_KEY}"
    with pytest.raises(ValueError):
        SourceConfig(urls=["x"], basic_auth="a:${REFEX_SOURCE_MISSING}").request_headers()


def test_invalid_definitions(env):
    base = {"id": "geo2", "name": "Geo", "format": "mmdb", "source": {"type": "http", "urls": ["http://x/y.mmdb"]}}
    assert env.post("/api/admin/referentials", json={"config": {**base, "transform": "SELECT 1"}}).status_code == 422
    assert env.post("/api/admin/referentials", json={"config": {**base, "downloads": ["csv"]}}).status_code == 422
