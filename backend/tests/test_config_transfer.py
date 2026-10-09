import json

import yaml

from conftest import refresh

SECRET = "sk-transfer-1234"


def setup_source_env(env, http_dir):
    url = f"http://127.0.0.1:{http_dir[1]}/remote.csv.gz"
    assert env.post("/api/admin/secrets", json={"name": "vendor", "value": SECRET, "description": "Vendor key",
                                                "hosts": ["127.0.0.1"]}).status_code == 201
    cfg = {"id": "vendor-feed", "name": "Vendor feed", "category": "Intel", "format": "csv", "key": "code",
           "source": {"type": "http", "urls": [url], "headers": {"X-Api-Key": "${secret:vendor}"}}}
    assert env.post("/api/admin/referentials", json={"config": cfg, "pull": False}).status_code == 201
    cfg2 = {"id": "literal-feed", "name": "Literal feed", "format": "csv",
            "source": {"type": "http", "urls": [url], "headers": {"X-Token": "literal-value"}}}
    assert env.post("/api/admin/referentials", json={"config": cfg2, "pull": False}).status_code == 201
    r = env.post("/api/internal-referentials", json={"id": "assets", "name": "Assets", "key": "host",
                                                      "columns": [{"name": "host", "type": "text", "required": True}]})
    assert r.status_code == 201, r.text
    gid = env.post("/api/admin/groups", json={"name": "SOC", "description": "Security team"}).json()["id"]
    for ref in ("vendor-feed", "assets"):
        assert env.post("/api/admin/grants", json={"referential_id": ref, "group_id": gid, "level": "read"}).status_code == 201
    return gid


def export(env, **params):
    r = env.get("/api/admin/config/export", params=params)
    assert r.status_code == 200, r.text
    return r


def test_export(env, http_dir):
    setup_source_env(env, http_dir)
    r = export(env)
    assert "attachment" in r.headers["content-disposition"] and r.text.startswith("# RefExposer configuration export")
    doc = yaml.safe_load(r.text)
    assert doc["refexposer_export"]["format"] == "refexposer-config" and doc["refexposer_export"]["credentials"] == "masked"
    refs = {d["id"]: d for d in doc["referentials"]}
    assert {"vendor-feed", "literal-feed", "assets", "countries"} <= set(refs)  # interface, internal and YAML definitions
    assert refs["vendor-feed"]["source"]["headers"] == {"X-Api-Key": "${secret:vendor}"}
    assert refs["literal-feed"]["source"]["headers"] == {"X-Token": "********"}
    assert "origin" not in refs["vendor-feed"] and refs["assets"]["source"]["type"] == "internal"
    assert doc["secrets"] == [{"name": "vendor", "description": "Vendor key", "hosts": ["127.0.0.1"], "used_by": ["vendor-feed"]}]
    assert SECRET not in r.text and "literal-value" not in r.text
    assert {"referential": "vendor-feed", "level": "read", "group": "SOC"} in doc["grants"]
    assert doc["groups"] == [{"name": "SOC", "description": "Security team"}]

    # Selection, JSON, credentials included
    doc = export(env, ids="literal-feed", format="json", credentials="included", grants=False).json()
    assert [d["id"] for d in doc["referentials"]] == ["literal-feed"] and "grants" not in doc
    assert doc["referentials"][0]["source"]["headers"] == {"X-Token": "literal-value"}
    assert env.get("/api/admin/config/export", params={"ids": "nope"}).status_code == 404

    # The file is also a valid file of config/
    from app.config import load_registry

    folder = env.data_dir.parent / "drop"
    folder.mkdir()
    (folder / "export.yaml").write_text(export(env, ids="vendor-feed").text, encoding="utf-8")
    refs, errors = load_registry(folder)
    assert list(refs) == ["vendor-feed"] and not errors


def test_import_on_another_environment(env, http_dir):
    setup_source_env(env, http_dir)
    content = export(env).text
    included = export(env, credentials="included", ids="literal-feed").text
    # "Another environment": the definitions, the group and the secret do not exist there
    for ref in ("vendor-feed", "literal-feed", "assets"):
        assert env.delete(f"/api/admin/referentials/{ref}").status_code == 200
    gid = next(g["id"] for g in env.get("/api/admin/groups").json() if g["name"] == "SOC")
    assert env.delete(f"/api/admin/groups/{gid}").status_code == 200
    assert env.delete("/api/admin/secrets/vendor", params={"force": True}).status_code == 200

    plan = env.post("/api/admin/config/import", json={"content": content}).json()
    actions = {p["id"]: p for p in plan["plan"]}
    assert plan["applied"] is False
    assert actions["countries"]["action"] == "conflict"  # defined in YAML on this instance: kept
    assert actions["vendor-feed"]["action"] == "error" and actions["vendor-feed"]["missing_secrets"] == ["vendor"]
    assert actions["literal-feed"]["action"] == "error" and "masked credential" in actions["literal-feed"]["reason"]
    assert actions["assets"]["action"] == "create"
    assert plan["missing_secrets"] == [{"name": "vendor", "description": "Vendor key", "hosts": ["127.0.0.1"], "used_by": ["vendor-feed"]}]
    assert plan["missing_groups"] == ["SOC"]

    # Secret created on the target, then the import is applied
    assert env.post("/api/admin/secrets", json={"name": "vendor", "value": SECRET, "hosts": ["127.0.0.1"]}).status_code == 201
    res = env.post("/api/admin/config/import", json={"content": content, "apply": True}).json()
    assert sorted(res["created"]) == ["assets", "vendor-feed"] and res["groups_created"] == ["SOC"]
    assert res["grants_changed"] == 2 and res["applied"] is True
    grants = env.get("/api/admin/grants", params={"referential_id": "vendor-feed"}).json()
    assert [(g["group"]["name"], g["level"]) for g in grants] == [("SOC", "read")]
    assert refresh(env, "vendor-feed")["status"] == "success"
    assert env.get("/api/referentials/assets").json()["kind"] == "internal"

    # The literal credential: with the export that includes it
    res = env.post("/api/admin/config/import", json={"content": included, "apply": True}).json()
    assert res["created"] == ["literal-feed"]

    # Imported again: unchanged; changed and on_existing=update: updated (masked values keep the target ones)
    plan = env.post("/api/admin/config/import", json={"content": content, "on_existing": "update"}).json()
    actions = {p["id"]: p["action"] for p in plan["plan"]}
    assert actions["vendor-feed"] == "unchanged" and actions["literal-feed"] == "unchanged"
    assert actions["countries"] == "conflict"
    doc = yaml.safe_load(content)
    for d in doc["referentials"]:
        if d["id"] == "literal-feed":
            d["description"] = "Changed in test"
    res = env.post("/api/admin/config/import", json={"content": json.dumps(doc), "on_existing": "update", "apply": True,
                                                     "ids": ["literal-feed"]}).json()
    assert res["updated"] == ["literal-feed"], res
    stored = env.get("/api/admin/referentials/literal-feed").json()["config"]
    assert stored["description"] == "Changed in test" and stored["source"]["headers"] == {"X-Token": "********"}
    exported = export(env, ids="literal-feed", credentials="included", format="json").json()
    assert exported["referentials"][0]["source"]["headers"] == {"X-Token": "literal-value"}


def test_import_errors(env):
    assert env.post("/api/admin/config/import", json={"content": "a: ["}).status_code == 400
    assert env.post("/api/admin/config/import", json={"content": "foo: 1"}).status_code == 400
    bad = yaml.safe_dump({"refexposer_export": {"format": "refexposer-config", "version": 99}, "referentials": []})
    assert "unsupported export" in env.post("/api/admin/config/import", json={"content": bad}).text
    # A plain list of definitions (a file of config/) is accepted
    content = yaml.safe_dump({"referentials": [{"id": "x1", "name": "X", "format": "csv", "source": {"urls": ["http://e.example/x.csv"]}},
                                               {"id": "x1", "name": "dup"}, {"id": "bad", "format": "nope"}]})
    plan = env.post("/api/admin/config/import", json={"content": content}).json()["plan"]
    assert [p["action"] for p in plan] == ["create", "error", "error"]
