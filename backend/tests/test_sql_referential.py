"""A query of the SQL console kept as a referential: the query is stored, not its rows."""
from conftest import USER_PASSWORD, create_user, new_client, refresh
from test_admin_referentials import wait_idle

OWNERS = """code,owner,criticality
FR,team-paris,high
DE,team-berlin,medium
PT,team-lisbon,low
"""


def _source(env, ref_id, name, content, **config):
    folder = env.data_dir / ref_id / "raw"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "data.csv").write_text(content, encoding="utf-8")
    r = env.post("/api/admin/referentials", json={
        "config": {"id": ref_id, "name": name, "format": "csv",
                   "source": {"type": "local", "path": "raw/*.csv"}, **config}})
    assert r.status_code == 201, r.text
    assert wait_idle(env, ref_id)["status"] == "success"


def _create(env, expect=201, **body):
    r = env.post("/api/sql/referential", json=body)
    assert r.status_code == expect, r.text
    return r.json()


def test_join_of_two_referentials_becomes_a_referential(env):
    refresh(env, "countries")  # 5 rows, key: code
    _source(env, "owners", "Owners", OWNERS, key="code")

    created = _create(
        env,
        id="countries-owners", name="Countries and their owner", key="code",
        description="Built from the SQL console.",
        sql="SELECT c.code, c.label, o.owner, o.criticality\n"
            "FROM countries c JOIN owners o ON o.code = c.code\nORDER BY c.code",
    )
    # The sources are detected from the table names of the query
    assert created["sources"] == ["countries", "owners"]
    assert [c["name"] for c in created["columns"]] == ["code", "label", "owner", "criticality"]
    assert created["run"]
    assert "countries" in created["transform"] and "{source0}" in created["transform"]

    run = wait_idle(env, "countries-owners")
    assert run["status"] == "success", run
    assert run["rows"] == 2  # FR and DE are in both; PT has no country, IT, ES and BE no owner

    info = env.get("/api/referentials/countries-owners").json()
    assert info["kind"] == "combined"
    assert info["combine"]["referentials"] == ["countries", "owners"]
    rows = env.get("/api/referentials/countries-owners/rows").json()["rows"]
    assert {r["code"]: r["owner"] for r in rows} == {"DE": "team-berlin", "FR": "team-paris"}
    # The key works, so the referential answers lookups like any other
    assert env.get("/api/referentials/countries-owners/lookup/FR").json()["owner"] == "team-paris"


def test_rebuilt_when_a_source_publishes_a_new_version(env):
    refresh(env, "countries")
    _source(env, "owners", "Owners", OWNERS, key="code")
    _create(env, id="owned", name="Owned countries", key="code",
            sql="SELECT o.code, o.owner, c.label FROM owners o JOIN countries c ON c.code = o.code")
    assert wait_idle(env, "owned")["rows"] == 2  # FR and DE

    (env.data_dir / "owners" / "raw" / "data.csv").write_text(
        OWNERS + "ES,team-madrid,low\n", encoding="utf-8")
    assert refresh(env, "owners")["status"] == "success"
    # The new version of the source rebuilds the referential made from it
    assert wait_idle(env, "owned")["rows"] == 3
    rows = env.get("/api/referentials/owned/rows").json()["rows"]
    assert sorted(r["code"] for r in rows) == ["DE", "ES", "FR"]


def test_query_with_its_own_cte(env):
    refresh(env, "countries")
    created = _create(env, id="big", name="Populated countries", key="code",
                      sql="WITH ranked AS (SELECT * FROM countries ORDER BY population DESC)\n"
                          "SELECT code, label, population FROM ranked LIMIT 3")
    assert created["sources"] == ["countries"]
    # Our CTE is put in front of theirs, in the same WITH
    assert created["transform"].startswith("WITH ")
    assert created["transform"].count("WITH ") == 1
    assert wait_idle(env, "big")["rows"] == 3


def test_sorted_copy_of_a_table_is_bound_too(env):
    """A query may read the sorted copy of an indexed column; the rebuild must find that name."""
    refresh(env, "pdns")  # indexes: [rdata]
    views = env.get("/api/sql/schema").json()
    pdns = next(t for t in views if t["table"] == "pdns")
    view = next(iter(pdns["indexes"].values()))
    created = _create(env, id="pdns-view", name="From the sorted copy",
                      sql=f"SELECT rrname, rdata FROM {view}")
    assert created["sources"] == ["pdns"]
    assert view in created["transform"]
    assert wait_idle(env, "pdns-view")["status"] == "success"


def test_refusals(env):
    refresh(env, "countries")
    _create(env, expect=400, id="nothing", name="No table", sql="SELECT 1 AS x")
    _create(env, expect=400, id="unknown-col", name="Bad key", key="missing",
            sql="SELECT code FROM countries")
    _create(env, expect=400, id="broken-sql", name="Broken", sql="SELECT FROM countries WHERE")
    _create(env, expect=400, id="other-table", name="Unreadable", sql="SELECT * FROM does_not_exist")
    _create(env, expect=400, id="with-placeholder", name="Placeholder",
            sql="SELECT * FROM countries WHERE label = '{source}'")
    _create(env, expect=400, id="named-source", name="Named source", sources=["nope"],
            sql="SELECT * FROM countries")
    _create(env, expect=422, id="Bad Id", name="Invalid identifier", sql="SELECT * FROM countries")
    # A write is refused by the same guard as the console
    _create(env, expect=400, id="write", name="Write", sql="CREATE TABLE x AS SELECT 1")

    _create(env, id="taken", name="First", sql="SELECT * FROM countries")
    _create(env, expect=409, id="taken", name="Second", sql="SELECT * FROM countries")


def test_reserved_to_sql_users(env):
    refresh(env, "countries")
    create_user(env, "reader", role="user")
    reader = new_client(env, "reader", USER_PASSWORD)
    r = reader.post("/api/sql/referential",
                    json={"id": "nope", "name": "Nope", "sql": "SELECT * FROM countries"})
    assert r.status_code == 403


def test_limited_to_the_referentials_the_account_may_read(env):
    """An advanced user sees only what it is granted: a query on another referential is refused."""
    refresh(env, "countries")
    create_user(env, "analyst", role="advanced")
    analyst = new_client(env, "analyst", USER_PASSWORD)
    r = analyst.post("/api/sql/referential",
                     json={"id": "hidden", "name": "Hidden", "sql": "SELECT * FROM countries"})
    assert r.status_code == 400, r.text
    assert "names no referential" in r.json()["detail"]


def test_the_creator_manages_what_it_creates(env):
    """An advanced user granted a referential may build one from it, and keeps the right on the result."""
    refresh(env, "countries")
    analyst = create_user(env, "analyst", role="advanced")
    r = env.post("/api/admin/grants", json={"referential_id": "countries", "user_id": analyst["id"],
                                            "level": "read"})
    assert r.status_code in (200, 201), r.text
    client = new_client(env, "analyst", USER_PASSWORD)

    created = _create(client, id="mine", name="Mine", key="code",
                      sql="SELECT code, label FROM countries")
    assert created["sources"] == ["countries"]
    assert wait_idle(client, "mine")["status"] == "success"
    # The referential it created is its own: it reads it and may update it
    assert client.get("/api/referentials/mine").json()["access"] == "manage"
    assert len(client.get("/api/referentials/mine/rows").json()["rows"]) == 5
