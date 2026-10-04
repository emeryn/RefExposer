"""Service accounts: created by administrators, API tokens only, rights like a user."""

from conftest import new_client, refresh


def test_service_account_lifecycle(env):
    refresh(env, "countries")
    refresh(env, "words")
    group = env.post("/api/admin/groups", json={"name": "Robots"}).json()
    r = env.post("/api/admin/service-accounts", json={"username": "SIEM-Connector", "display_name": "SIEM enrichment", "group_ids": [group["id"]]})
    assert r.status_code == 201, r.text
    svc = r.json()
    assert svc["username"] == "siem-connector" and svc["is_service"] is True and svc["role"] == "user" and svc["status"] == "active"
    assert env.post("/api/admin/service-accounts", json={"username": "siem-connector"}).status_code == 409

    # Never signs in to the interface, whatever the password
    anon = new_client(env)
    for password in ["!service", "", "x"]:
        assert anon.post("/api/auth/login", json={"username": "siem-connector", "password": password}).status_code == 401

    # No password, no other role: rights are given on referentials
    assert env.patch(f"/api/admin/users/{svc['id']}", json={"role": "admin"}).status_code == 400
    assert env.patch(f"/api/admin/users/{svc['id']}", json={"password": "Some-Str0ng-Passw0rd!"}).status_code == 400

    # Tokens are created by an administrator, for service accounts only
    t = env.post(f"/api/admin/users/{svc['id']}/tokens", json={"name": "prod", "expires_in_days": 30})
    assert t.status_code == 201, t.text
    token = t.json()["token"]
    admin_id = env.get("/api/auth/me").json()["id"]
    assert env.post(f"/api/admin/users/{admin_id}/tokens", json={"name": "x"}).status_code == 400
    assert [x["name"] for x in env.get(f"/api/admin/users/{svc['id']}/tokens").json()] == ["prod"]

    api = new_client(env)
    api.headers.pop("X-Requested-With")
    auth = {"Authorization": f"Bearer {token}"}
    me = api.get("/api/auth/me", headers=auth).json()
    assert me["is_service"] is True and me["groups"] == ["Robots"] and me["can_use_sql"] is False

    # Rights through its group, like a user
    assert api.get("/api/referentials", headers=auth).json() == []
    env.post("/api/admin/grants", json={"referential_id": "countries", "group_id": group["id"], "level": "read"})
    assert [x["id"] for x in api.get("/api/referentials", headers=auth).json()] == ["countries"]
    assert api.get("/api/referentials/countries/lookup/FR", headers=auth).json()["label"] == "France"
    assert api.get("/api/referentials/countries/rows", params={"code": "DE"}, headers=auth).json()["total"] == 1
    assert api.get("/api/referentials/words/rows", headers=auth).status_code == 404
    assert api.post("/api/referentials/countries/refresh", headers=auth).status_code == 403
    assert api.post("/api/sql", json={"sql": "SELECT 1"}, headers=auth).status_code == 403

    # It cannot manage its own tokens nor its password, nor reach the administration
    assert api.post("/api/auth/tokens", json={"name": "more"}, headers=auth).status_code == 403
    assert api.delete(f"/api/auth/tokens/{t.json()['id']}", headers=auth).status_code == 403
    assert api.post("/api/auth/password", json={"current_password": "a", "new_password": "b"}, headers=auth).status_code == 400
    assert api.get("/api/admin/users", headers=auth).status_code == 403

    # Listed with its last token use; disabling it or revoking the token cuts the access
    listed = next(u for u in env.get("/api/admin/users").json() if u["username"] == "siem-connector")
    assert listed["token_count"] == 1 and listed["token_last_used_at"] is not None
    env.patch(f"/api/admin/users/{svc['id']}", json={"is_active": False})
    assert api.get("/api/referentials", headers=auth).status_code == 401
    env.patch(f"/api/admin/users/{svc['id']}", json={"is_active": True, "display_name": "SIEM"})
    assert api.get("/api/referentials", headers=auth).status_code == 200
    assert env.delete(f"/api/admin/users/{svc['id']}/tokens/{t.json()['id']}").status_code == 200
    assert api.get("/api/referentials", headers=auth).status_code == 401
