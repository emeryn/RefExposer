"""Referentials synchronized from the file system, public URL and branding."""
import gzip
import json
import os
import time

from conftest import CSV, new_client
from test_admin_referentials import wait_idle


def _sync(env, tmp_path):
    service = env.app_.state.service
    root = tmp_path / "sync"
    root.mkdir(exist_ok=True)
    service.settings.sync_dir = root
    service.settings.sync_settle_seconds = 0
    return service, root


def _age(*paths, seconds=60):
    old = time.time() - seconds
    for p in paths:
        os.utime(p, (old, old))


def test_sync_folder_lifecycle(env, tmp_path):
    service, root = _sync(env, tmp_path)
    # A file, a folder with several files and overrides, a gzip file and a JSON document of records
    (root / "Countries List.csv").write_text(CSV, encoding="utf-8")
    vendors = root / "vendors"
    vendors.mkdir()
    (vendors / "part1.csv").write_text("code,name\nA,Alpha\nB,Beta\n", encoding="utf-8")
    (vendors / "part2.csv").write_text("code,name\nC,Gamma\n", encoding="utf-8")
    (vendors / ".part3.csv.Xa1b2c").write_text("code,name\nZ,rsync temporary file\n", encoding="utf-8")
    (vendors / "refexposer.yml").write_text("name: Approved vendors\ncategory: Purchasing\nkey: code\n", encoding="utf-8")
    with gzip.open(root / "terms.txt.gz", "wt", encoding="utf-8") as f:
        f.write("alpha\nbeta\n")
    (root / "kev.json").write_text(json.dumps({"title": "KEV", "vulnerabilities": [{"cveID": f"CVE-2026-{i}"} for i in range(12)]}), encoding="utf-8")
    _age(*root.rglob("*"))

    service.reload()
    refs = {r["id"]: r for r in env.get("/api/referentials").json()}
    assert {"countries-list", "vendors", "terms", "kev"} <= set(refs)
    assert refs["vendors"]["name"] == "Approved vendors" and refs["vendors"]["origin"] == "sync"
    assert refs["vendors"]["sync_path"].endswith("sync/vendors")

    # First scan notes the files, the second one (files unchanged and settled) imports them
    assert service.scan_sync() == []
    assert sorted(service.scan_sync()) == ["countries-list", "kev", "terms", "vendors"]
    for ref_id in ("countries-list", "kev", "vendors", "terms"):
        run = wait_idle(env, ref_id)
        assert run["status"] == "success" and run["trigger"] == "sync", (ref_id, run)
    assert env.get("/api/referentials/vendors").json()["row_count"] == 3  # temporary rsync file ignored
    assert env.get("/api/referentials/vendors/lookup/C").json()["name"] == "Gamma"
    assert env.get("/api/referentials/kev").json()["row_count"] == 12  # records path detected
    assert env.get("/api/referentials/terms").json()["row_count"] == 2
    assert service.scan_sync() == []  # nothing changed

    # A new version pushed: imported once it has settled
    (vendors / "part2.csv").write_text("code,name\nC,Gamma\nD,Delta\n", encoding="utf-8")
    service.settings.sync_settle_seconds = 3600
    service.scan_sync()
    assert service.scan_sync() == []  # still being written (recent modification)
    service.settings.sync_settle_seconds = 0
    assert service.scan_sync() == ["vendors"]
    assert wait_idle(env, "vendors")["status"] == "success"
    assert env.get("/api/referentials/vendors").json()["row_count"] == 4
    names = [s["name"] for s in env.get("/api/referentials/vendors/downloads").json()["sources"]]
    assert names == ["sync/vendors/part1.csv", "sync/vendors/part2.csv"]

    # A pushed error page never replaces the published version
    (root / "Countries List.csv").write_text("<!DOCTYPE html><html><body>502 Bad Gateway</body></html>", encoding="utf-8")
    _age(root / "Countries List.csv")
    service.scan_sync()
    assert service.scan_sync() == ["countries-list"]
    assert wait_idle(env, "countries-list")["status"] == "corrupted"
    assert env.get("/api/referentials/countries-list").json()["row_count"] == 5

    # Synchronized referentials are not edited from the interface
    assert env.delete("/api/admin/referentials/vendors").status_code == 409

    # Removed from the folder: removed from RefExposer at the next scan
    (root / "terms.txt.gz").unlink()
    service.scan_sync()
    assert "terms" not in {r["id"] for r in env.get("/api/referentials").json()}

    # Identifiers already defined elsewhere are reported, not overridden
    (root / "countries.csv").write_text(CSV, encoding="utf-8")
    service.scan_sync()
    errors = env.get("/api/system").json()["config_errors"]
    assert any(e["file"] == "sync/countries.csv" and "already defined" in e["error"] for e in errors)
    assert env.get("/api/referentials/countries").json()["origin"] == "file"

    # Broken overrides are reported too
    (vendors / "refexposer.yml").write_text("key: [unclosed", encoding="utf-8")
    service.scan_sync()
    assert any(e["file"] == "sync/vendors/refexposer.yml" for e in env.get("/api/system").json()["config_errors"])


def test_public_url(env):
    service = env.app_.state.service
    settings = env.get("/api/admin/settings").json()
    assert settings["oidc_redirect_uri"] == "http://testserver/api/auth/oidc/callback"  # derived from the request
    service.settings.public_url = "https://refexposer.example.com"
    try:
        assert env.get("/api/admin/settings").json()["oidc_redirect_uri"] == "https://refexposer.example.com/api/auth/oidc/callback"
        assert env.get("/api/catalog/openapi.json").json()["servers"] == [{"url": "https://refexposer.example.com"}]
        assert env.get("/api/branding").json()["public_url"] == "https://refexposer.example.com"
        assert service.settings.secure_cookies is True
    finally:
        service.settings.public_url = None


def test_branding(env):
    anonymous = new_client(env)
    b = anonymous.get("/api/branding").json()
    assert b["title"] == "RefExposer" and b["logo_url"] is None

    assert env.put("/api/admin/settings/branding", json={"title": "  ACME Referentials ", "subtitle": "Security team"}).status_code == 200
    assert env.put("/api/admin/settings/branding", json={"title": "", "subtitle": "x"}).status_code == 422
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    r = env.post("/api/admin/settings/branding/logo", files={"file": ("logo.png", png, "image/png")})
    assert r.status_code == 200, r.text
    b = anonymous.get("/api/branding").json()
    assert b["title"] == "ACME Referentials" and b["subtitle"] == "Security team" and b["logo_url"].startswith("/api/branding/logo?v=")
    logo = anonymous.get(b["logo_url"])
    assert logo.content == png and logo.headers["content-type"] == "image/png" and "sandbox" in logo.headers["content-security-policy"]

    # Wrong content, wrong type, too big, not an admin
    assert env.post("/api/admin/settings/branding/logo", files={"file": ("x.png", b"<html>", "image/png")}).status_code == 400
    assert env.post("/api/admin/settings/branding/logo", files={"file": ("x.gif", b"GIF89a", "image/gif")}).status_code == 400
    assert env.post("/api/admin/settings/branding/logo", files={"file": ("x.png", png + b"\x00" * 600_000, "image/png")}).status_code == 413
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
    assert env.post("/api/admin/settings/branding/logo", files={"file": ("l.svg", svg, "image/svg+xml")}).status_code == 200
    assert anonymous.post("/api/admin/settings/branding/logo", files={"file": ("l.svg", svg, "image/svg+xml")}).status_code == 401

    assert env.delete("/api/admin/settings/branding/logo").status_code == 200
    assert anonymous.get("/api/branding").json()["logo_url"] is None
    assert anonymous.get("/api/branding/logo").status_code == 404
