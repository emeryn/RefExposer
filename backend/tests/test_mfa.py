"""Second factor (TOTP): enrolment, sign-in, replay protection, recovery codes, policy, administration."""

import json

from conftest import USER_PASSWORD, create_user, new_client

from app import mfa


def enrol(client):
    """Enable TOTP from the account page; returns (secret, recovery codes, step used)."""
    secret = client.post("/api/auth/mfa/enroll").json()["secret"]
    step = int(__import__("time").time() // 30)
    r = client.post("/api/auth/mfa/enable", json={"code": mfa._code(secret, step)})
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"], step


def login(env, username):
    return new_client(env).post("/api/auth/login", json={"username": username, "password": USER_PASSWORD})


def test_totp_algorithm():
    # RFC 6238 test vector (SHA-1, secret "12345678901234567890", T = 59 s): 94287082 -> 6 digits 287082
    import base64

    secret = base64.b32encode(b"12345678901234567890").decode()
    assert mfa.current_code(secret, at=59) == "287082"
    assert mfa.match(secret, "287082", None, at=59) == 1
    assert mfa.match(secret, "287082", 1, at=59) is None  # already used


def test_optional_second_factor(env):
    create_user(env, "mia")
    mia = new_client(env, "mia", USER_PASSWORD)
    status = mia.get("/api/auth/me").json()["mfa"]
    assert (status["available"], status["enabled"], status["required"], status["totp"], status["webauthn"]) == (True, False, False, False, [])
    enroll = mia.post("/api/auth/mfa/enroll").json()
    assert enroll["otpauth_uri"].startswith("otpauth://totp/") and enroll["qr_svg"].startswith("data:image/svg+xml")
    assert mia.post("/api/auth/mfa/enable", json={"code": "000000"}).status_code == 400
    secret, codes, step = enrol(mia)
    assert len(codes) == 10 and mia.get("/api/auth/mfa").json()["enabled"] is True

    # Sign-in in two steps: no session after the password
    r = login(env, "mia").json()
    assert r["mfa"] == "verify" and "id" not in r
    c = new_client(env)
    assert c.post("/api/auth/mfa/verify", json={"mfa_token": r["mfa_token"], "code": mfa._code(secret, step)}).status_code == 401  # replay
    ok = c.post("/api/auth/mfa/verify", json={"mfa_token": r["mfa_token"], "code": mfa._code(secret, step + 1)})
    assert ok.status_code == 200 and ok.json()["username"] == "mia" and c.get("/api/auth/me").status_code == 200

    # Recovery code: once only
    r = login(env, "mia").json()
    assert new_client(env).post("/api/auth/mfa/verify", json={"mfa_token": r["mfa_token"], "code": codes[0]}).status_code == 200
    r = login(env, "mia").json()
    assert new_client(env).post("/api/auth/mfa/verify", json={"mfa_token": r["mfa_token"], "code": codes[0]}).status_code == 401

    # A forged challenge (clear text) or another purpose is refused
    forged = json.dumps({"u": 1, "p": "verify", "exp": 9e12})
    assert new_client(env).post("/api/auth/mfa/verify", json={"mfa_token": forged, "code": "123456"}).status_code == 401
    setup_token = mfa.challenge(1, "setup")
    assert new_client(env).post("/api/auth/mfa/verify", json={"mfa_token": setup_token, "code": "123456"}).status_code == 401

    # Disabled with a code; repeated wrong codes lock the account
    assert mia.post("/api/auth/mfa/disable", json={"code": codes[1]}).json()["enabled"] is False  # a recovery code works too
    assert "mfa_token" not in login(env, "mia").json()


def test_wrong_codes_lock_the_account(env):
    create_user(env, "lou")
    lou = new_client(env, "lou", USER_PASSWORD)
    enrol(lou)
    token = login(env, "lou").json()["mfa_token"]
    c = new_client(env)
    statuses = [c.post("/api/auth/mfa/verify", json={"mfa_token": token, "code": "000000"}).status_code for _ in range(4)]
    assert statuses[:3] == [401, 401, 401] and statuses[3] == 429  # REFEX_LOGIN_MAX_ATTEMPTS=3 in the tests


def test_policy_and_administration(env):
    group = env.post("/api/admin/groups", json={"name": "Finance"}).json()
    create_user(env, "max", group_ids=[group["id"]])
    create_user(env, "ned")
    r = env.put("/api/admin/settings/mfa", json={"mode": "groups", "groups": ["Finance"]})
    assert r.status_code == 200, r.text
    assert env.get("/api/admin/settings").json()["mfa"]["mode"] == "groups"

    # Member of the group: enrolment required at sign-in
    r = login(env, "max").json()
    assert r["mfa"] == "setup"
    c = new_client(env)
    secret = c.post("/api/auth/mfa/setup", json={"mfa_token": r["mfa_token"]}).json()["secret"]
    done = c.post("/api/auth/mfa/setup/confirm", json={"mfa_token": r["mfa_token"], "code": mfa.current_code(secret)})
    assert done.status_code == 200 and len(done.json()["recovery_codes"]) == 10 and c.get("/api/auth/me").json()["mfa"]["required"]
    assert c.post("/api/auth/mfa/disable", json={"code": mfa._code(secret, int(__import__("time").time() // 30) + 1)}).status_code == 400

    # Not in the group: unchanged
    assert "mfa_token" not in login(env, "ned").json()

    # Lost phone: the administrator resets it, enrolment required again
    max_id = next(u["id"] for u in env.get("/api/admin/users").json() if u["username"] == "max")
    assert next(u for u in env.get("/api/admin/users").json() if u["username"] == "max")["mfa_enabled"] is True
    assert env.delete(f"/api/admin/users/{max_id}/mfa").json()["mfa_enabled"] is False
    assert login(env, "max").json()["mfa"] == "setup"

    # Everybody, except service accounts (API tokens) and single sign-on accounts
    env.put("/api/admin/settings/mfa", json={"mode": "all", "groups": []})
    assert login(env, "ned").json()["mfa"] == "setup"
    svc = env.post("/api/admin/service-accounts", json={"username": "robot"}).json()
    token = env.post(f"/api/admin/users/{svc['id']}/tokens", json={"name": "t"}).json()["token"]
    api = new_client(env)
    api.headers.pop("X-Requested-With")
    assert api.get("/api/referentials", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    # Single sign-on only: no second factor of RefExposer
    from app.settings import get_settings

    get_settings().local_login = False
    try:
        assert env.get("/api/auth/mfa", headers={"Authorization": f"Bearer {token}"}).json()["available"] is False
    finally:
        get_settings().local_login = True
    env.put("/api/admin/settings/mfa", json={"mode": "optional", "groups": []})
