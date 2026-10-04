from conftest import ADMIN_PASSWORD, USER_PASSWORD, create_user, new_client, refresh


def test_anonymous_access_is_refused(env):
    anon = new_client(env)
    assert anon.get("/api/health").status_code == 200  # public probe
    for path in ["/api/referentials", "/api/sql/schema", "/api/activity", "/api/system", "/api/admin/users", "/api/auth/me"]:
        assert anon.get(path).status_code == 401, path
    assert anon.post("/api/sql", json={"sql": "SELECT 1"}).status_code == 401


def test_password_hash_is_argon2id(env):
    from sqlalchemy import select

    from app.db import session_factory
    from app.models import User

    with session_factory()() as db:
        h = db.scalar(select(User.password_hash).where(User.username == "admin"))
    assert h.startswith("$argon2id$")
    assert ADMIN_PASSWORD not in h


def test_login_logout_and_lockout(env):
    anon = new_client(env)
    assert anon.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
    assert anon.post("/api/auth/login", json={"username": "ghost", "password": "nope"}).status_code == 401
    # CSRF header is mandatory
    assert anon.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}, headers={"X-Requested-With": ""}).status_code == 403

    create_user(env, "bob")
    bob = new_client(env, "bob", USER_PASSWORD)
    me = bob.get("/api/auth/me").json()
    assert me["username"] == "bob" and me["is_admin"] is False and me["permissions"] == {}
    assert bob.post("/api/auth/logout").status_code == 200
    assert bob.get("/api/auth/me").status_code == 401

    # 3 failures (REFEX_LOGIN_MAX_ATTEMPTS) lock the account, even with the right password
    for _ in range(3):
        assert anon.post("/api/auth/login", json={"username": "bob", "password": "wrong"}).status_code == 401
    assert anon.post("/api/auth/login", json={"username": "bob", "password": USER_PASSWORD}).status_code == 429
    users = {u["username"]: u for u in env.get("/api/admin/users").json()}
    assert users["bob"]["locked"] is True
    env.patch(f"/api/admin/users/{users['bob']['id']}", json={"unlock": True})
    assert anon.post("/api/auth/login", json={"username": "bob", "password": USER_PASSWORD}).status_code == 200

    audit = env.get("/api/admin/audit", params={"action": "auth.login", "success": "false"}).json()
    assert audit["total"] >= 5


def test_csrf_required_for_cookie_mutations(env):
    r = env.post("/api/system/reload", headers={"X-Requested-With": ""})
    assert r.status_code == 403
    assert env.post("/api/system/reload").status_code == 200


def test_forced_password_change(env):
    create_user(env, "carol", must_change=True)
    carol = new_client(env, "carol", USER_PASSWORD)
    assert carol.get("/api/auth/me").json()["must_change_password"] is True
    assert carol.get("/api/referentials").status_code == 403
    weak = carol.post("/api/auth/password", json={"current_password": USER_PASSWORD, "new_password": "short"})
    assert weak.status_code == 400
    assert carol.post("/api/auth/password", json={"current_password": "bad", "new_password": "N3w-Passw0rd!x"}).status_code == 400
    assert carol.post("/api/auth/password", json={"current_password": USER_PASSWORD, "new_password": "N3w-Passw0rd!x"}).status_code == 200
    assert carol.get("/api/referentials").status_code == 200


def test_roles_and_admin_endpoints(env):
    create_user(env, "dave")
    dave = new_client(env, "dave", USER_PASSWORD)
    for method, path in [("get", "/api/admin/users"), ("get", "/api/system"), ("post", "/api/system/reload"), ("get", "/api/admin/audit")]:
        assert getattr(dave, method)(path).status_code == 403, path

    # The last active admin can be neither demoted, disabled nor deleted
    admin_id = next(u["id"] for u in env.get("/api/admin/users").json() if u["username"] == "admin")
    assert env.patch(f"/api/admin/users/{admin_id}", json={"role": "user"}).status_code == 400
    assert env.patch(f"/api/admin/users/{admin_id}", json={"is_active": False}).status_code == 400
    assert env.delete(f"/api/admin/users/{admin_id}").status_code == 400
    assert env.post("/api/admin/users", json={"username": "x", "password": "weak"}).status_code == 422
    assert env.post("/api/admin/users", json={"username": "eve", "password": "weakpassword"}).status_code == 400

    # Disabling a user closes their sessions
    dave_id = next(u["id"] for u in env.get("/api/admin/users").json() if u["username"] == "dave")
    env.patch(f"/api/admin/users/{dave_id}", json={"is_active": False})
    assert dave.get("/api/auth/me").status_code == 401


def test_per_referential_grants(env):
    refresh(env, "countries")
    refresh(env, "words")
    create_user(env, "frank")
    frank = new_client(env, "frank", USER_PASSWORD)
    frank_id = frank.get("/api/auth/me").json()["id"]

    # No grant: nothing visible, unknown referentials and forbidden ones look the same
    assert frank.get("/api/referentials").json() == []
    assert frank.get("/api/referentials/countries").status_code == 404
    assert frank.get("/api/referentials/countries/rows").status_code == 404
    # The SQL console is reserved to administrators and advanced users
    assert frank.get("/api/sql/schema").status_code == 403
    assert frank.post("/api/sql", json={"sql": "SELECT 1"}).status_code == 403
    assert frank.get("/api/auth/me").json()["can_use_sql"] is False

    # Read grant on one referential
    grant = env.post("/api/admin/grants", json={"referential_id": "countries", "user_id": frank_id, "level": "read"}).json()
    refs = frank.get("/api/referentials").json()
    assert [r["id"] for r in refs] == ["countries"] and refs[0]["access"] == "read"
    assert frank.get("/api/referentials/countries/rows").json()["total"] == 5
    assert frank.get("/api/referentials/countries/lookup/FR").status_code == 200
    assert frank.get("/api/referentials/countries/export?format=csv").status_code == 200
    assert frank.get("/api/referentials/words/rows").status_code == 404
    assert frank.post("/api/referentials/countries/refresh").status_code == 403
    assert frank.get("/api/auth/me").json()["permissions"] == {"countries": "read"}
    assert [r["id"] for r in frank.get("/api/search", params={"q": "alpha"}).json()["results"]] == ["countries"]

    # SQL console (advanced user) only sees readable tables, including through read_parquet()
    create_user(env, "ivan", role="advanced")
    ivan = new_client(env, "ivan", USER_PASSWORD)
    assert ivan.get("/api/auth/me").json()["can_use_sql"] is True
    assert ivan.get("/api/sql/schema").json() == []
    env.post("/api/admin/grants", json={"referential_id": "countries", "user_id": ivan.get("/api/auth/me").json()["id"], "level": "read"})
    assert [t["table"] for t in ivan.get("/api/sql/schema").json()] == ["countries"]
    assert ivan.post("/api/sql", json={"sql": "SELECT count(*) FROM countries"}).json()["rows"] == [[5]]
    assert ivan.post("/api/sql", json={"sql": "SELECT * FROM words"}).status_code == 400
    words_parquet = (env.data_dir / "words" / "current.parquet").resolve().as_posix()
    assert ivan.post("/api/sql", json={"sql": f"SELECT * FROM read_parquet('{words_parquet}')"}).status_code == 400
    raw = (env.data_dir / "countries" / "raw" / "a.csv").resolve().as_posix()
    assert ivan.post("/api/sql", json={"sql": f"SELECT * FROM read_csv('{raw}')"}).status_code == 400

    # Upgrading the grant to manage allows updates
    env.post("/api/admin/grants", json={"referential_id": "countries", "user_id": frank_id, "level": "manage"})
    assert len(env.get("/api/admin/grants", params={"user_id": frank_id}).json()) == 1  # upsert, not duplicate
    assert frank.post("/api/referentials/countries/refresh").status_code == 200
    runs = env.get("/api/referentials/countries/runs").json()
    assert runs[0]["user"] == "frank"

    env.delete(f"/api/admin/grants/{grant['id']}")
    assert frank.get("/api/referentials").json() == []


def test_group_and_wildcard_grants(env):
    refresh(env, "countries")
    refresh(env, "words")
    group = env.post("/api/admin/groups", json={"name": "Analystes", "description": "Lecture seule"}).json()
    user = create_user(env, "gina", group_ids=[group["id"]])
    gina = new_client(env, "gina", USER_PASSWORD)
    assert gina.get("/api/referentials").json() == []

    env.post("/api/admin/grants", json={"referential_id": "*", "group_id": group["id"], "level": "read"})
    ids = {r["id"] for r in gina.get("/api/referentials").json()}
    assert ids == {"countries", "words", "nested", "remote", "pdns"}
    assert gina.post("/api/referentials/words/refresh").status_code == 403
    assert gina.get("/api/sql/schema").status_code == 403

    # Grants listed for one referential include the '*' ones
    grants = env.get("/api/admin/grants", params={"referential_id": "words"}).json()
    assert grants[0]["referential_id"] == "*" and grants[0]["group"]["name"] == "Analystes"

    # Removing the user from the group removes the access
    env.patch(f"/api/admin/users/{user['id']}", json={"group_ids": []})
    assert gina.get("/api/referentials").json() == []


def test_api_tokens(env):
    refresh(env, "countries")
    create_user(env, "hugo")
    hugo = new_client(env, "hugo", USER_PASSWORD)
    hugo_id = hugo.get("/api/auth/me").json()["id"]
    env.post("/api/admin/grants", json={"referential_id": "countries", "user_id": hugo_id})

    created = hugo.post("/api/auth/tokens", json={"name": "script", "expires_in_days": 30}).json()
    token = created["token"]
    assert token.startswith("rfx_") and created["prefix"] == token[:12]
    listed = hugo.get("/api/auth/tokens").json()
    assert len(listed) == 1 and "token" not in listed[0]

    script = new_client(env)
    script.headers.pop("X-Requested-With")  # no CSRF header needed with a bearer token
    auth = {"Authorization": f"Bearer {token}"}
    assert script.get("/api/referentials/countries/lookup/FR", headers=auth).json()["label"] == "France"
    assert script.post("/api/sql", json={"sql": "SELECT 1 AS x"}, headers=auth).status_code == 403  # standard user
    assert script.get("/api/referentials", headers={"Authorization": "Bearer rfx_invalid"}).status_code == 401

    hugo.delete(f"/api/auth/tokens/{created['id']}")
    assert script.get("/api/referentials", headers=auth).status_code == 401


def test_bootstrap_admin_reset(env, monkeypatch):
    from app.auth import bootstrap_admin
    from app.db import session_factory
    from app.settings import get_settings

    monkeypatch.setenv("REFEX_ADMIN_RESET_PASSWORD", "true")
    monkeypatch.setenv("REFEX_ADMIN_PASSWORD", "R3set-Passw0rd!")
    get_settings.cache_clear()
    with session_factory()() as db:
        bootstrap_admin(db, get_settings())
    assert env.get("/api/auth/me").status_code == 401  # sessions revoked
    c = new_client(env, "admin", "R3set-Passw0rd!")
    assert c.get("/api/auth/me").json()["must_change_password"] is False


def test_local_login_can_be_disabled(env):
    """REFEX_LOCAL_LOGIN=false: single sign-on only in the interface; API tokens keep working."""
    from app.settings import get_settings

    create_user(env, "lena")
    lena = new_client(env, "lena", USER_PASSWORD)
    token = lena.post("/api/auth/tokens", json={"name": "script"}).json()["token"]
    settings = get_settings()
    settings.local_login = False
    try:
        assert env.get("/api/auth/providers").json()["local"] is False
        r = new_client(env).post("/api/auth/login", json={"username": "lena", "password": USER_PASSWORD})
        assert r.status_code == 403 and "single sign-on" in r.json()["detail"]
        assert lena.get("/api/auth/me").status_code == 401  # session opened before: no longer accepted
        api = new_client(env)
        api.headers.pop("X-Requested-With")
        assert api.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["username"] == "lena"
        assert env.get("/api/auth/me").status_code == 401  # the local administrator too
    finally:
        settings.local_login = True
    assert new_client(env, "lena", USER_PASSWORD).get("/api/auth/me").status_code == 200
