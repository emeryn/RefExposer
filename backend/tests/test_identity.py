import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from ldap3 import MOCK_SYNC, OFFLINE_SLAPD_2_4, Connection, Server
from ldap3.core.exceptions import LDAPBindError

from conftest import USER_PASSWORD, create_user, new_client, refresh

# --------------------------------------------------------------------------- settings & proxy


def test_settings_secrets_are_encrypted_and_masked(env):
    r = env.put("/api/admin/settings/proxy", json={"http_proxy": "http://proxy.corp:3128", "username": "svc", "password": "Pr0xy!", "no_proxy": "localhost"})
    assert r.status_code == 200, r.text
    assert r.json()["password"] == "" and r.json()["password_set"] is True
    s = env.get("/api/admin/settings").json()
    assert s["proxy"]["http_proxy"] == "http://proxy.corp:3128" and s["proxy"]["password"] == "" and s["proxy"]["password_set"]

    from sqlalchemy import select

    from app.db import session_factory
    from app.models import AppSetting

    with session_factory()() as db:
        raw = db.scalar(select(AppSetting).where(AppSetting.key == "proxy")).value
    assert raw["password"].startswith("enc:v1:") and "Pr0xy!" not in json.dumps(raw)

    # Empty secret keeps the stored one, *_clear removes it
    env.put("/api/admin/settings/proxy", json={**s["proxy"], "https_proxy": "http://proxy.corp:3129"})
    from app import appsettings

    appsettings.clear_cache()
    with session_factory()() as db:
        assert appsettings.load(db, "proxy").password == "Pr0xy!"
    env.put("/api/admin/settings/proxy", json={**s["proxy"], "password_clear": True})
    assert env.get("/api/admin/settings").json()["proxy"]["password_set"] is False

    assert env.put("/api/admin/settings/proxy", json={"http_proxy": "not a url"}).status_code == 422
    assert env.put("/api/admin/settings/ldap", json={"server_url": "ldap://x", "user_filter": "(uid=x)"}).status_code == 422
    create_user(env, "ursula")
    assert new_client(env, "ursula", USER_PASSWORD).get("/api/admin/settings").status_code == 403
    env.put("/api/admin/settings/proxy", json={"http_proxy": "", "https_proxy": ""})


def test_downloads_go_through_configured_proxy(env):
    port = env.get("/api/admin/referentials/remote").json()["config"]["source"]["urls"][0].split(":")[2].split("/")[0]
    # The test web server serves absolute-form requests, so it behaves as an HTTP proxy
    unreachable = "http://referentiel.invalid/remote.csv.gz"
    r = env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": [unreachable]}})
    assert r.status_code == 400  # no proxy: DNS failure
    env.put("/api/admin/settings/proxy", json={"http_proxy": f"http://127.0.0.1:{port}", "no_proxy": "localhost"})
    r = env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": [unreachable]}, "refresh": True})
    assert r.status_code == 200, r.text
    assert r.json()["total_rows"] == 5
    t = env.post("/api/admin/settings/proxy/test", json={"url": unreachable}).json()
    assert t["ok"] and t["status"] == 200 and "127.0.0.1" in t["route"]
    # no_proxy bypasses the proxy
    t = env.post("/api/admin/settings/proxy/test", json={"config": {"http_proxy": f"http://127.0.0.1:{port}", "no_proxy": "referentiel.invalid"}, "url": unreachable}).json()
    assert not t["ok"] and t["error"]
    env.put("/api/admin/settings/proxy", json={"http_proxy": ""})


# --------------------------------------------------------------------------- LDAP

LDAP_CFG = {
    "enabled": True,
    "label": "Test directory",
    "server_url": "ldap://ldap.test",
    "bind_dn": "cn=svc,dc=test",
    "bind_password": "Svc-Passw0rd",
    "user_base_dn": "ou=people,dc=test",
    "user_filter": "(&(objectClass=inetOrgPerson)(uid={username}))",
    "group_mode": "memberof",
    "admin_group": "refexposer-admins",
    "group_regex": "^(?!ignored)",
}


@pytest.fixture()
def ldap(env, monkeypatch):
    from app import ldapauth

    server = Server("ldap.test", get_info=OFFLINE_SLAPD_2_4)
    seed = Connection(server, user="cn=svc,dc=test", password="Svc-Passw0rd", client_strategy=MOCK_SYNC)
    seed.strategy.add_entry("cn=svc,dc=test", {"userPassword": "Svc-Passw0rd", "objectClass": "person", "sn": "svc"})
    people = [
        ("alice", "Alice Martin", ["cn=securite,ou=groups,dc=test", "cn=ignored-group,ou=groups,dc=test"]),
        ("bob", "Bob Admin", ["cn=refexposer-admins,ou=groups,dc=test"]),
        ("carl", "Carl", []),
    ]
    for uid, cn, groups in people:
        seed.strategy.add_entry(f"uid={uid},ou=people,dc=test", {
            "objectClass": ["inetOrgPerson", "person"], "uid": uid, "cn": cn, "sn": cn, "mail": f"{uid}@test.org",
            "userPassword": f"{uid.capitalize()}-Ldap-2026", "memberOf": groups,
        })

    def factory(cfg, user, password, ca_bundle=""):
        conn = Connection(server, user=user, password=password, client_strategy=MOCK_SYNC)
        if not conn.bind():
            raise LDAPBindError("invalidCredentials")
        return conn

    monkeypatch.setattr(ldapauth, "connection_factory", factory)
    r = env.put("/api/admin/settings/ldap", json=LDAP_CFG)
    assert r.status_code == 200, r.text
    return server


def test_ldap_login_requires_approval(env, ldap):
    refresh(env, "countries")
    assert env.get("/api/auth/providers").json()["ldap"] == {"enabled": True, "label": "Test directory"}
    anon = new_client(env)
    assert anon.post("/api/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
    assert anon.post("/api/auth/login", json={"username": "nobody", "password": "x"}).status_code == 401

    alice = new_client(env)
    r = alice.post("/api/auth/login", json={"username": "Alice", "password": "Alice-Ldap-2026"})
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["status"] == "pending" and me["auth_source"] == "ldap" and me["groups"] == ["securite"]
    assert alice.get("/api/referentials").status_code == 403  # waiting for approval
    assert alice.get("/api/auth/me").json()["status"] == "pending"
    assert env.get("/api/auth/me").json()["pending_requests"] == 1

    users = {u["username"]: u for u in env.get("/api/admin/users", params={"status": "pending"}).json()}
    assert users["alice"]["external_id"] == "uid=alice,ou=people,dc=test" and users["alice"]["email"] == "alice@test.org"
    groups = {g["name"]: g for g in env.get("/api/admin/groups").json()}
    assert groups["securite"]["source"] == "ldap"
    env.post("/api/admin/grants", json={"referential_id": "countries", "group_id": groups["securite"]["id"]})
    r = env.post(f"/api/admin/users/{users['alice']['id']}/approve", json={})
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert [x["id"] for x in alice.get("/api/referentials").json()] == ["countries"]
    # Synchronised groups cannot be edited by hand, the password is managed by the directory
    assert env.patch(f"/api/admin/groups/{groups['securite']['id']}", json={"name": "securite", "member_ids": []}).status_code == 400
    assert env.patch(f"/api/admin/users/{users['alice']['id']}", json={"password": "N3w-Passw0rd!x"}).status_code == 400
    assert alice.post("/api/auth/password", json={"current_password": "x", "new_password": "N3w-Passw0rd!x"}).status_code == 400

    # Admin group mapping: bob gets the admin role, but still needs an approval
    bob = new_client(env)
    me = bob.post("/api/auth/login", json={"username": "bob", "password": "Bob-Ldap-2026"}).json()
    assert me["role"] == "admin" and me["status"] == "pending"
    assert bob.get("/api/admin/users").status_code == 403

    # Rejection: login refused afterwards
    carl = new_client(env)
    carl.post("/api/auth/login", json={"username": "carl", "password": "Carl-Ldap-2026"})
    carl_id = next(u["id"] for u in env.get("/api/admin/users").json() if u["username"] == "carl")
    env.post(f"/api/admin/users/{carl_id}/reject", json={"reason": "pas concerné"})
    assert carl.get("/api/auth/me").status_code == 401
    assert new_client(env).post("/api/auth/login", json={"username": "carl", "password": "Carl-Ldap-2026"}).status_code == 403

    actions = {a["action"] for a in env.get("/api/admin/audit").json()["rows"]}
    assert {"auth.access_request", "user.approve", "user.reject", "settings.ldap"} <= actions


def test_ldap_diagnostic_and_local_precedence(env, ldap):
    t = env.post("/api/admin/settings/ldap/test", json={"username": "alice", "password": "Alice-Ldap-2026"}).json()
    assert t["ok"], t
    assert t["identity"]["groups"] == ["securite"] and t["identity"]["admin"] is False
    t = env.post("/api/admin/settings/ldap/test", json={"username": "alice", "password": "bad"}).json()
    assert not t["ok"]
    t = env.post("/api/admin/settings/ldap/test", json={"config": {**LDAP_CFG, "bind_password": "bad"}}).json()
    assert not t["ok"] and "service account" in t["steps"][0]["detail"]

    # A local account with the same identifier takes precedence over the directory
    create_user(env, "alice")
    r = new_client(env).post("/api/auth/login", json={"username": "alice", "password": "Alice-Ldap-2026"})
    assert r.status_code == 401
    assert new_client(env).post("/api/auth/login", json={"username": "alice", "password": USER_PASSWORD}).json()["auth_source"] == "local"


# --------------------------------------------------------------------------- OpenID Connect

ISSUER = "https://idp.test/realms/corp"


@pytest.fixture()
def idp(env, monkeypatch):
    from app import oidc

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "k1", "use": "sig", "alg": "RS256"})
    state = {"nonce": None, "claims": {}, "codes": []}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
                "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
                "userinfo_endpoint": f"{ISSUER}/protocol/openid-connect/userinfo",
                "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
            })
        if url.endswith("/certs"):
            return httpx.Response(200, json={"keys": [jwk]})
        if url.endswith("/token"):
            form = parse_qs(request.content.decode())
            assert request.headers["authorization"].startswith("Basic ")
            assert form["code_verifier"][0] and form["grant_type"] == ["authorization_code"]
            state["codes"].append(form["code"][0])
            now = int(time.time())
            claims = {"iss": ISSUER, "aud": "refexposer", "sub": "f81d4fae-7dec", "iat": now, "exp": now + 300,
                      "nonce": state["nonce"], "preferred_username": "Dana", "name": "Dana Scully", "email": "dana@corp.org",
                      **state["claims"]}
            token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})
            return httpx.Response(200, json={"id_token": token, "access_token": "at", "token_type": "Bearer"})
        if url.endswith("/userinfo"):
            return httpx.Response(200, json={"sub": "f81d4fae-7dec", "groups": ["/analystes", "/corp/securite"]})
        return httpx.Response(404)

    monkeypatch.setattr(oidc, "client", lambda *a, **k: httpx.Client(transport=httpx.MockTransport(handler)))
    oidc.clear_cache()
    r = env.put("/api/admin/settings/oidc", json={
        "enabled": True, "label": "Keycloak", "issuer": ISSUER, "client_id": "refexposer", "client_secret": "s3cret", "groups_claim": "groups",
    })
    assert r.status_code == 200, r.text
    return state


def _oidc_login(env, state, next_path="/r/countries"):
    c = new_client(env)
    r = c.get("/api/auth/oidc/login", params={"next": next_path}, follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = urlsplit(r.headers["location"])
    q = parse_qs(loc.query)
    assert loc.netloc == "idp.test" and q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == ["http://testserver/api/auth/oidc/callback"]
    state["nonce"] = q["nonce"][0]
    return c, q["state"][0]


def test_oidc_login_flow(env, idp):
    assert env.get("/api/auth/providers").json()["oidc"]["enabled"] is True
    c, st = _oidc_login(env, idp)
    r = c.get("/api/auth/oidc/callback", params={"code": "abc", "state": st}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/r/countries", r.headers.get("location")
    me = c.get("/api/auth/me").json()
    assert me["username"] == "dana" and me["status"] == "pending" and me["auth_source"] == "oidc"
    assert sorted(me["groups"]) == ["analystes", "securite"]  # paths stripped, groups from userinfo
    dana_id = me["id"]
    env.post(f"/api/admin/users/{dana_id}/approve", json={"role": "user"})
    assert c.get("/api/referentials").status_code == 200

    # Second login: same account (matched on the subject), groups refreshed
    idp["claims"] = {"groups": ["/analystes"]}
    c2, st2 = _oidc_login(env, idp)
    c2.get("/api/auth/oidc/callback", params={"code": "def", "state": st2}, follow_redirects=False)
    me2 = c2.get("/api/auth/me").json()
    assert me2["id"] == dana_id and me2["status"] == "active" and me2["groups"] == ["analystes"]

    # Discovery diagnostic
    t = env.post("/api/admin/settings/oidc/test", json={}).json()
    assert t["ok"] and t["redirect_uri"].endswith("/api/auth/oidc/callback")


def test_oidc_rejects_tampering(env, idp):
    c, st = _oidc_login(env, idp)
    r = c.get("/api/auth/oidc/callback", params={"code": "abc", "state": "forged"}, follow_redirects=False)
    assert r.status_code == 302 and "auth_error" in r.headers["location"] and "state" in r.headers["location"]
    assert c.get("/api/auth/me").status_code == 401

    c, st = _oidc_login(env, idp)
    idp["nonce"] = "other"
    r = c.get("/api/auth/oidc/callback", params={"code": "abc", "state": st}, follow_redirects=False)
    assert "auth_error" in r.headers["location"] and "nonce" in r.headers["location"]

    c, st = _oidc_login(env, idp)
    idp["claims"] = {"aud": "another-client"}
    r = c.get("/api/auth/oidc/callback", params={"code": "abc", "state": st}, follow_redirects=False)
    assert "auth_error" in r.headers["location"]

    c, _ = _oidc_login(env, idp, next_path="https://evil.example/")
    idp["claims"] = {}
    c2, st = _oidc_login(env, idp, next_path="//evil.example")
    r = c2.get("/api/auth/oidc/callback", params={"code": "x", "state": st}, follow_redirects=False)
    assert r.headers["location"] == "/"  # no open redirect

    r = c.get("/api/auth/oidc/callback", params={"error": "access_denied", "error_description": "refusé"}, follow_redirects=False)
    assert "auth_error" in r.headers["location"]


# --------------------------------------------------------------------------- search


def test_facets_and_list_search(env):
    refresh(env, "countries")
    f = env.get("/api/referentials/countries/facets").json()
    assert f["total"] == 5
    assert [x["column"] for x in f["facets"]] == ["label"]
    assert {r["column"] for r in f["ranges"]} == {"population", "created"}
    sel = json.dumps(["France", "Belgique, royaume"])
    r = env.get("/api/referentials/countries/rows", params={"label__in": sel}).json()
    assert r["total"] == 2
    f = env.get("/api/referentials/countries/facets", params={"label__in": sel, "population__gte": "60000000"}).json()
    assert f["total"] == 1
    label = f["facets"][0]
    # own filter ignored (both labels listed), other filters applied (population >= 60M: France, Allemagne)
    assert {v["value"] for v in label["values"]} == {"France", "Allemagne"}
    pop = next(x for x in f["ranges"] if x["column"] == "population")
    assert pop["min"] == "11700000"  # range ignores its own bound, keeps the label filter

    b = env.post("/api/referentials/countries/lookup", json={"values": ["France", "Belgique, royaume", "Atlantide"], "column": "label"}).json()
    assert b["found"] == 2 and b["missing"] == ["Atlantide"] and b["column"] == "label"
    assert env.post("/api/referentials/countries/lookup", json={"values": ["x"], "column": "nope"}).status_code == 400
