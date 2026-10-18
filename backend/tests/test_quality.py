import time

import pytest

from conftest import refresh

CSV = """code,label,population,domain,sha256
FR,France,68000000,gouv.fr,{h}
DE,Allemagne,84000000,bund.de,{h}
IT,Italie,59000000,governo.it,{h}
ES,Espagne,48000000,not a domain,{h}
BE,Belgique,11700000,belgium.be,
""".format(h="a" * 64)


def define(env, rules, ref_id="geo", key="code", status=201, pull=False, method="post"):
    raw = env.data_dir / ref_id / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "c.csv").write_text(CSV, encoding="utf-8")
    cfg = {"id": ref_id, "name": "Geo", "format": "csv", "key": key, "source": {"type": "local", "path": "raw/*.csv"},
           "quality": {"rules": rules}}
    r = (env.post("/api/admin/referentials", json={"config": cfg, "pull": pull}) if method == "post"
         else env.put(f"/api/admin/referentials/{ref_id}", json={"config": cfg, "pull": pull}))
    assert r.status_code == (status if method == "post" else 200 if status == 201 else status), r.text
    return r


def checks(q):
    return {c["name"]: c for c in q["checks"]}


def test_rules_score_and_examples(env):
    define(env, [
        {"column": "code", "check": "format", "format": "country"},
        {"column": "domain", "check": "format", "format": "domain", "threshold": 75},
        {"column": "population", "check": "range", "min": 20000000, "name": "big countries"},
        {"column": "sha256", "check": "format", "format": "sha256"},
        {"column": "label", "check": "unique"},
        {"column": "label", "check": "length", "max_length": 7},
        {"check": "expression", "expression": "population > 0 AND code <> 'XX'"},
        {"column": "code", "check": "values", "values": ["FR", "DE", "IT", "ES"], "threshold": 50},
    ])
    run = refresh(env, "geo")
    assert run["status"] == "success" and run["quality"]["status"] == "warning", run
    q = env.get("/api/referentials/geo").json()["quality"]
    c = checks(q)
    assert c["code is a valid country"]["pass_pct"] == 100 and c["code is a valid country"]["ok"]
    assert c["domain is a valid domain"]["pass_pct"] == 80 and c["domain is a valid domain"]["ok"]  # threshold 75
    assert c["domain is a valid domain"]["examples"] == ["not a domain"]
    big = c["big countries"]
    assert big["failing"] == 1 and big["examples"] == ["11700000"] and not big["ok"]
    assert c["sha256 is a valid sha256"]["checked"] == 4 and c["sha256 is a valid sha256"]["ok"]  # empty values ignored
    assert c["label is unique"]["ok"]
    assert c["label length between 0 and 7"]["examples"] == ["Allemagne", "Belgique"] or \
        sorted(c["label length between 0 and 7"]["examples"]) == ["Allemagne", "Belgique"]
    assert c["population > 0 AND code <> 'XX'"]["ok"]
    assert c["code among 4 allowed value(s)"]["pass_pct"] == 80
    assert c["key code is filled"]["automatic"] is True and c["key code is filled"]["ok"]
    assert q["score"] == pytest.approx(sum(x["pass_pct"] for x in q["checks"]) / len(q["checks"]), abs=0.1)
    summary = env.get("/api/referentials").json()
    s = next(r for r in summary if r["id"] == "geo")["quality"]
    assert s["status"] == "warning" and "big countries" in s["failed"]
    hist = env.get("/api/referentials/geo/quality").json()
    assert hist["history"][0]["status"] == "warning" and hist["history"][0]["score"] == q["score"] and len(hist["rules"]) == 8


def test_error_rule_rejects_the_version(env):
    define(env, [{"column": "population", "check": "range", "min": 1}])
    assert refresh(env, "geo")["status"] == "success"
    define(env, [{"column": "domain", "check": "format", "format": "domain", "severity": "error"}], method="put")
    run = refresh(env, "geo", force=True)
    assert run["status"] == "rejected" and "quality rule(s) not met: domain is a valid domain" in run["message"]
    assert run["quality"] == {"score": pytest.approx(90.0, abs=0.1), "status": "failed"}
    detail = env.get("/api/referentials/geo").json()
    assert detail["has_data"] and detail["version"] == 1  # previous version kept


def test_rules_checked_again_when_they_change(env):
    define(env, [{"column": "code", "check": "not_null"}])
    assert refresh(env, "geo")["status"] == "success"
    first = env.get("/api/referentials/geo").json()["quality"]
    assert first["status"] == "ok" and len(first["checks"]) == 1  # the key rule is not repeated
    define(env, [{"column": "population", "check": "range", "max": 60000000}], method="put")
    deadline = time.time() + 10
    while time.time() < deadline:
        q = env.get("/api/referentials/geo").json()["quality"]
        if q and q.get("recheck"):
            break
        time.sleep(0.1)
    assert q["recheck"] and q["status"] == "warning" and checks(q)["population between … and 60000000"]["failing"] == 2
    assert env.get("/api/referentials/geo").json()["version"] == 1  # nothing downloaded or rebuilt
    # On demand
    res = env.post("/api/referentials/geo/quality/check").json()
    assert res["status"] == "warning"


def test_invalid_rules(env):
    r = define(env, [{"column": "code", "check": "format"}], status=422)
    assert "choose a format" in r.text
    r = define(env, [{"check": "pattern", "pattern": "["}], status=422)
    assert "needs a column" in r.text
    r = define(env, [{"column": "code", "check": "pattern", "pattern": "(["}], status=422)
    assert "invalid regular expression" in r.text
    define(env, [{"column": "nope", "check": "not_null"}, {"check": "expression", "expression": "nope > 1"}])
    run = refresh(env, "geo")
    assert run["status"] == "success"  # warnings: published
    q = env.get("/api/referentials/geo").json()["quality"]
    assert checks(q)["nope is filled"]["error"] == "unknown column 'nope'"
    assert "nope" in checks(q)["nope > 1"]["error"]


def test_quality_event():
    from app.notify import event_of

    assert event_of("ok", "ok", "success", "ok", "warning")[0] == ["quality", "published"]
    assert event_of("ok", "ok", "success", "warning", "warning")[0] == ["published"]
    assert event_of("error", "ok", "success", None, "warning")[0] == ["recovered", "quality", "published"]
