"""Rights given on a whole category: resolved at each request, so the set of referentials follows the category."""
from conftest import USER_PASSWORD, create_user, new_client, refresh
from test_admin_referentials import wait_idle

CSV = "code,label\nFR,France\n"


def source(env, ref_id, name, category, **config):
    folder = env.data_dir / ref_id / "raw"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "data.csv").write_text(CSV, encoding="utf-8")
    r = env.post("/api/admin/referentials", json={
        "config": {"id": ref_id, "name": name, "category": category, "format": "csv", "key": "code",
                   "source": {"type": "local", "path": "raw/*.csv"}, **config}})
    assert r.status_code == 201, r.text
    assert wait_idle(env, ref_id)["status"] == "success"


def grant(env, expect=201, **body):
    r = env.post("/api/admin/grants", json=body)
    assert r.status_code == expect, r.text
    return r.json()


def visible(client) -> list[str]:
    return sorted(r["id"] for r in client.get("/api/referentials").json())


def test_a_category_grant_covers_what_the_category_holds(env):
    source(env, "kev", "KEV", "Threat intelligence")
    source(env, "iocs", "IOCs", "Threat intelligence")
    source(env, "assets", "Assets", "Inventory")
    group = env.post("/api/admin/groups", json={"name": "soc"}).json()
    create_user(env, "analyst", role="user", group_ids=[group["id"]])
    analyst = new_client(env, "analyst", USER_PASSWORD)
    assert visible(analyst) == []

    created = grant(env, category="Threat intelligence", group_id=group["id"], level="read")
    assert created["scope"] == "category" and created["category"] == "Threat intelligence"
    assert created["covers"] == ["iocs", "kev"]
    assert created["referential_id"] == "category:Threat intelligence"

    # One grant, two referentials, and the third stays invisible
    assert visible(analyst) == ["iocs", "kev"]
    assert analyst.get("/api/referentials/kev/rows").status_code == 200
    assert analyst.get("/api/referentials/assets").status_code == 404
    assert analyst.get("/api/auth/me").json()["permissions"] == {"iocs": "read", "kev": "read"}


def test_a_referential_added_to_the_category_is_covered(env):
    source(env, "kev", "KEV", "Threat intelligence")
    group = env.post("/api/admin/groups", json={"name": "soc"}).json()
    create_user(env, "analyst", role="user", group_ids=[group["id"]])
    analyst = new_client(env, "analyst", USER_PASSWORD)
    grant(env, category="Threat intelligence", group_id=group["id"], level="read")
    assert visible(analyst) == ["kev"]

    # Nothing is touched on the rights side: the new referential is simply in the category
    source(env, "iocs", "IOCs", "Threat intelligence")
    assert visible(analyst) == ["iocs", "kev"]
    assert len(env.get("/api/admin/grants", params={"group_id": group["id"]}).json()) == 1


def test_moving_a_referential_out_of_the_category_withdraws_the_right(env):
    source(env, "kev", "KEV", "Threat intelligence")
    group = env.post("/api/admin/groups", json={"name": "soc"}).json()
    create_user(env, "analyst", role="user", group_ids=[group["id"]])
    analyst = new_client(env, "analyst", USER_PASSWORD)
    grant(env, category="Threat intelligence", group_id=group["id"], level="read")
    assert visible(analyst) == ["kev"]

    config = env.get("/api/admin/referentials/kev").json()["config"]
    r = env.put("/api/admin/referentials/kev", json={"config": {**config, "category": "Archive"}, "pull": False})
    assert r.status_code == 200, r.text
    assert visible(analyst) == []
    assert analyst.get("/api/referentials/kev").status_code == 404


def test_levels_and_direct_grants_combine(env):
    source(env, "kev", "KEV", "Threat intelligence")
    source(env, "iocs", "IOCs", "Threat intelligence")
    group = env.post("/api/admin/groups", json={"name": "soc"}).json()
    user = create_user(env, "analyst", role="user", group_ids=[group["id"]])
    analyst = new_client(env, "analyst", USER_PASSWORD)
    grant(env, category="Threat intelligence", group_id=group["id"], level="read")
    # A direct grant on one of them wins when it is higher
    grant(env, referential_id="kev", user_id=user["id"], level="manage")
    assert analyst.get("/api/auth/me").json()["permissions"] == {"kev": "manage", "iocs": "read"}
    assert analyst.post("/api/referentials/kev/refresh").status_code == 200
    assert analyst.post("/api/referentials/iocs/refresh").status_code == 403


def test_a_category_right_appears_on_the_page_of_a_referential(env):
    source(env, "kev", "KEV", "Threat intelligence")
    group = env.post("/api/admin/groups", json={"name": "soc"}).json()
    grant(env, category="Threat intelligence", group_id=group["id"], level="read")
    rows = env.get("/api/admin/grants", params={"referential_id": "kev"}).json()
    assert [(g["scope"], g["level"]) for g in rows] == [("category", "read")]
    # And not on the page of a referential of another category
    source(env, "assets", "Assets", "Inventory")
    assert env.get("/api/admin/grants", params={"referential_id": "assets"}).json() == []


def test_an_empty_category_grants_nothing_but_is_kept(env):
    """A category nothing is in yet: the right waits for its first referential instead of being refused."""
    group = env.post("/api/admin/groups", json={"name": "soc"}).json()
    create_user(env, "analyst", role="user", group_ids=[group["id"]])
    analyst = new_client(env, "analyst", USER_PASSWORD)
    created = grant(env, category="Later", group_id=group["id"], level="read")
    assert created["covers"] == []
    assert visible(analyst) == []
    source(env, "new", "New", "Later")
    assert visible(analyst) == ["new"]


def test_refusals(env):
    source(env, "kev", "KEV", "Threat intelligence")
    group = env.post("/api/admin/groups", json={"name": "soc"}).json()
    grant(env, expect=422, group_id=group["id"], level="read")  # neither scope
    grant(env, expect=422, referential_id="kev", category="Threat intelligence", group_id=group["id"])  # both
    grant(env, expect=422, category="   ", group_id=group["id"])
    grant(env, expect=422, category="x" * 101, group_id=group["id"])
    grant(env, expect=404, referential_id="nope", group_id=group["id"])
    grant(env, expect=400, category="Threat intelligence")  # no subject


def test_the_level_of_a_category_grant_can_be_changed_and_removed(env):
    source(env, "kev", "KEV", "Threat intelligence")
    group = env.post("/api/admin/groups", json={"name": "soc"}).json()
    create_user(env, "analyst", role="user", group_ids=[group["id"]])
    analyst = new_client(env, "analyst", USER_PASSWORD)
    created = grant(env, category="Threat intelligence", group_id=group["id"], level="read")
    # Granting the same category again changes the level instead of adding a row
    again = grant(env, category="Threat intelligence", group_id=group["id"], level="manage")
    assert again["id"] == created["id"] and again["level"] == "manage"
    assert analyst.get("/api/auth/me").json()["permissions"] == {"kev": "manage"}

    assert env.delete(f"/api/admin/grants/{created['id']}").status_code == 200
    assert visible(analyst) == []


def test_api_tokens_follow_the_category_grant(env):
    """A token carries the rights of its owner, category grants included."""
    source(env, "kev", "KEV", "Threat intelligence")
    source(env, "assets", "Assets", "Inventory")
    group = env.post("/api/admin/groups", json={"name": "soc"}).json()
    account = env.post("/api/admin/service-accounts", json={"username": "feeder", "group_ids": [group["id"]]}).json()
    grant(env, category="Threat intelligence", group_id=group["id"], level="read")
    token = env.post(f"/api/admin/users/{account['id']}/tokens", json={"name": "test"}).json()["token"]
    r = env.get("/api/referentials", headers={"Authorization": f"Bearer {token}"})
    assert sorted(x["id"] for x in r.json()) == ["kev"]


def test_the_sql_sandbox_follows_the_category_grant(env):
    source(env, "kev", "KEV", "Threat intelligence")
    source(env, "assets", "Assets", "Inventory")
    group = env.post("/api/admin/groups", json={"name": "soc"}).json()
    create_user(env, "analyst", role="advanced", group_ids=[group["id"]])
    analyst = new_client(env, "analyst", USER_PASSWORD)
    grant(env, category="Threat intelligence", group_id=group["id"], level="read")
    assert [t["ref_id"] for t in analyst.get("/api/sql/schema").json()] == ["kev"]
    assert analyst.post("/api/sql", json={"sql": "SELECT count(*) FROM kev"}).status_code == 200
    assert analyst.post("/api/sql", json={"sql": "SELECT count(*) FROM assets"}).status_code == 400


def test_the_configuration_export_keeps_a_category_grant(env):
    source(env, "kev", "KEV", "Threat intelligence")
    env.post("/api/admin/groups", json={"name": "soc"})
    group = env.get("/api/admin/groups").json()[-1]
    grant(env, category="Threat intelligence", group_id=group["id"], level="read")
    exported = env.get("/api/admin/config/export", params={"ids": "kev", "grants": True}).text
    assert "category:Threat intelligence" in exported
