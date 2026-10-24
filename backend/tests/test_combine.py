"""Unified referentials: several referentials put together (union) or joined (enrichment)."""
from conftest import refresh
from test_admin_referentials import wait_idle

TEAM2 = """code,label,population,created
PT,Portugal,10300000,1976-04-02
NL,Pays-Bas,17800000,1815-03-16
FR,France,68100000,1958-10-04
"""

OWNERS = """code,owner,criticality
FR,team-paris,high
DE,team-berlin,medium
PT,team-lisbon,low
"""


def _create(env, config, expect=201):
    r = env.post("/api/admin/referentials", json={"config": config})
    assert r.status_code == expect, r.text
    return r.json()


def _source(env, ref_id, name, content, **config):
    """Referential fed by a file dropped in its own folder (local source)."""
    folder = env.data_dir / ref_id / "raw"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "data.csv").write_text(content, encoding="utf-8")
    _create(env, {"id": ref_id, "name": name, "format": "csv", "source": {"type": "local", "path": "raw/*.csv"}, **config})
    assert wait_idle(env, ref_id)["status"] == "success"


def test_union_of_two_referentials(env):
    refresh(env, "countries")  # 5 rows, key: code
    _source(env, "team2", "Team 2", TEAM2, key="code")

    _create(env, {
        "id": "countries-all", "name": "Countries (every team)", "key": "code",
        "source": {"type": "referential", "referentials": ["countries", "team2"]},
        "combine": {"mode": "union", "source_column": "_team"},
    })
    run = wait_idle(env, "countries-all")
    assert run["status"] == "success", run
    assert run["rows"] == 8  # 5 + 3, FR appears in both
    info = env.get("/api/referentials/countries-all").json()
    assert info["kind"] == "combined" and info["combine"]["referentials"] == ["countries", "team2"]
    assert [s["referential"] for s in info["sources"]] == ["countries", "team2"]
    assert all(s["version"] for s in info["sources"])
    rows = env.get("/api/referentials/countries-all/rows", params={"code": "FR"}).json()["rows"]
    assert sorted(r["_team"] for r in rows) == ["countries", "team2"]

    # A new version of a source rebuilds the unified referential by itself
    (env.data_dir / "team2" / "raw" / "data.csv").write_text(TEAM2 + "LU,Luxembourg,660000,1839-04-19\n", encoding="utf-8")
    assert refresh(env, "team2")["status"] == "success"
    assert wait_idle(env, "countries-all")["rows"] == 9
    assert env.get("/api/referentials/team2").json()["dependents"] == ["countries-all"]

    # Nothing changed: the unified referential is not rebuilt for nothing
    assert refresh(env, "countries-all")["status"] == "unchanged"


def test_enrichment_adds_columns(env):
    refresh(env, "countries")
    _source(env, "owners", "Owners", OWNERS, key="code")

    _create(env, {
        "id": "countries-owned", "name": "Countries with their owner", "key": "code",
        "source": {"type": "referential", "referentials": ["countries", "owners"]},
        "combine": {"mode": "enrich", "on": "code", "columns": {"owners": ["owner"]}},
    })
    assert wait_idle(env, "countries-owned")["status"] == "success"
    info = env.get("/api/referentials/countries-owned").json()
    assert [c["name"] for c in info["columns"]] == ["code", "label", "population", "created", "owners_owner"]
    assert info["row_count"] == 5  # left join: the rows of the first referential
    assert env.get("/api/referentials/countries-owned/lookup/FR").json()["owners_owner"] == "team-paris"
    assert env.get("/api/referentials/countries-owned/lookup/ES").json()["owners_owner"] is None

    # Without prefix the original names are kept; a collision is refused
    _create(env, {
        "id": "countries-plain", "name": "Plain", "key": "code",
        "source": {"type": "referential", "referentials": ["countries", "owners"]},
        "combine": {"mode": "enrich", "on": "code", "prefix": False},
    })
    assert wait_idle(env, "countries-plain")["status"] == "success"
    assert env.get("/api/referentials/countries-plain/lookup/DE").json()["criticality"] == "medium"

    r = env.post("/api/admin/referentials/preview", json={
        "source": {"type": "referential", "referentials": ["countries", "countries"]}})
    assert r.status_code == 422 and "twice" in r.json()["detail"]


def test_combine_refusals(env):
    refresh(env, "countries")
    # Unknown referential, and a referential reading itself
    assert "unknown referential" in _create(env, {
        "id": "ghost", "name": "Ghost", "source": {"type": "referential", "referentials": ["nope"]}}, 422)["detail"]
    assert "read itself" in _create(env, {
        "id": "countries", "name": "Self", "source": {"type": "referential", "referentials": ["countries"]}}, 422)["detail"]
    # An enrichment needs two referentials, and union options do not apply to it
    assert "at least two" in _create(env, {
        "id": "one", "name": "One", "source": {"type": "referential", "referentials": ["countries"]},
        "combine": {"mode": "enrich"}}, 422)["detail"]
    assert "only apply to an enrichment" in _create(env, {
        "id": "two", "name": "Two", "source": {"type": "referential", "referentials": ["countries", "words"]},
        "combine": {"mode": "union", "on": "code"}}, 422)["detail"]

    # A confidential source may only be combined into a confidential referential
    _source(env, "secret-ranges", "Secret", OWNERS, key="code", confidential=True)
    assert "confidential" in _create(env, {
        "id": "leak", "name": "Leak", "key": "code",
        "source": {"type": "referential", "referentials": ["secret-ranges", "countries"]},
        "combine": {"mode": "enrich", "on": "code"}}, 422)["detail"]
    _create(env, {
        "id": "sealed", "name": "Sealed", "key": "code", "confidential": True,
        "source": {"type": "referential", "referentials": ["secret-ranges", "countries"]},
        "combine": {"mode": "enrich", "on": "code"}})
    assert wait_idle(env, "sealed")["status"] == "success"
    assert env.get("/api/referentials/sealed/lookup/FR").json()["countries_label"] == "France"
    assert env.get("/api/referentials/sealed").json()["encrypted"] is True


def test_the_payload_of_the_editor_is_accepted(env):
    # Exactly what the referential editor sends (every 'combine' field, empty ones included): the API forbids
    # unknown fields, so this pins the contract between the form and the definition
    refresh(env, "countries")
    _source(env, "owners", "Owners", OWNERS, key="code")
    for combine in (
        {"mode": "union", "columns": {}, "source_column": None},
        {"mode": "union", "columns": {"owners": ["code", "owner"]}, "source_column": "_src"},
        {"mode": "enrich", "on": None, "join_on": {}, "columns": {}, "prefix": True},
        {"mode": "enrich", "on": "code", "join_on": {"owners": "code"}, "columns": {"owners": ["owner"]}, "prefix": False},
    ):
        body = {"config": {
            "id": f"draft-{combine['mode']}-{len(combine.get('columns') or {})}-{int(bool(combine.get('on')))}",
            "name": "Draft", "description": "", "category": "General", "tags": [], "homepage": None, "license": None,
            "owner": None, "format": "parquet", "options": {}, "transform": None, "key": "code",
            "search_columns": [], "schedule": "0 6 * * *", "max_age": None,
            "validation": {"min_rows": 1, "max_drop_pct": None, "unique_key": False}, "quality": {"rules": []},
            "enabled": True, "downloads": [],
            "storage": {"sort_by": [], "indexes": [], "row_group_size": 122880, "keep_raw": True,
                        "keep_previous": True, "profile": "full", "track_changes": True, "check_key": True},
            "incremental": None, "merge": None, "combine": combine,
            "source": {"type": "referential", "referentials": ["countries", "owners"], "urls": [], "headers": {}, "extract": None},
        }, "pull": False}
        r = env.post("/api/admin/referentials", json=body)
        assert r.status_code == 201, r.text
        assert r.json()["kind"] == "combined"
    # And the preview takes the same shape
    p = env.post("/api/admin/referentials/preview", json={
        "source": {"type": "referential", "referentials": ["countries", "owners"], "urls": [], "headers": {}, "extract": None},
        "format": None, "options": {}, "transform": None, "referential_id": None, "refresh": False,
        "combine": {"mode": "enrich", "on": "code", "join_on": {}, "columns": {}, "prefix": True},
        "key": "code", "confidential": False,
    })
    assert p.status_code == 200 and p.json()["error"] is None, p.text


def test_a_source_cannot_be_deleted_while_used(env):
    refresh(env, "countries")
    _source(env, "team2", "Team 2", TEAM2, key="code")
    _create(env, {"id": "all", "name": "All", "key": "code",
                  "source": {"type": "referential", "referentials": ["countries", "team2"]}})
    assert wait_idle(env, "all")["status"] == "success"
    r = env.delete("/api/admin/referentials/team2")
    assert r.status_code == 409 and "all" in r.json()["detail"]
    assert env.delete("/api/admin/referentials/all").status_code == 200
    assert env.delete("/api/admin/referentials/team2").status_code == 200


def test_a_unified_referential_takes_no_file(env):
    refresh(env, "countries")
    _create(env, {"id": "copy", "name": "Copy", "key": "code",
                  "source": {"type": "referential", "referentials": ["countries"]}})
    assert wait_idle(env, "copy")["status"] == "success"
    r = env.post("/api/referentials/copy/import", files={"files": ("a.csv", "code\nFR\n")})
    assert r.status_code == 400 and "rebuilt from the referentials" in r.json()["detail"]
    assert env.get("/api/referentials/copy").json()["import_folder"] is None


def test_preview_of_a_unified_referential(env):
    refresh(env, "countries")
    _source(env, "owners", "Owners", OWNERS, key="code")
    r = env.post("/api/admin/referentials/preview", json={
        "source": {"type": "referential", "referentials": ["countries", "owners"]},
        "combine": {"mode": "enrich", "on": "code"}, "key": "code"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["error"] is None and p["total_rows"] == 5 and p["format"] == "parquet"
    assert "owners_criticality" in [c["name"] for c in p["columns"]]
    assert len(p["files"]) == 2 and "Enrichment of" in p["logs"][0]

    # A referential never published yet cannot be combined
    _create(env, {"id": "later", "name": "Later", "format": "csv", "enabled": False,
                  "source": {"type": "local", "path": "raw/*.csv"}})
    p = env.post("/api/admin/referentials/preview", json={
        "source": {"type": "referential", "referentials": ["later", "countries"]}}).json()
    assert "no published version" in p["error"]
