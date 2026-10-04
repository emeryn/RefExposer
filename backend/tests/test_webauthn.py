"""Security keys / passkeys (WebAuthn) as second factor, checked with a software authenticator."""

import base64
import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from conftest import USER_PASSWORD, create_user, new_client

from app import mfa

ORIGIN, RP_ID = "http://testserver", "testserver"  # TestClient base URL


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class SoftKey:
    """Minimal FIDO2 authenticator: P-256 key, "none" attestation, signature counter."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(32)
        self.counter = 0

    def _client_data(self, kind: str, challenge: str) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": ORIGIN, "crossOrigin": False}).encode()

    def create(self, options: dict) -> dict:
        nums = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")})
        auth_data = (hashlib.sha256(RP_ID.encode()).digest() + bytes([0x45]) + struct.pack(">I", 0)
                     + bytes(16) + struct.pack(">H", len(self.cred_id)) + self.cred_id + cose)
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {"id": b64(self.cred_id), "rawId": b64(self.cred_id), "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": b64(self._client_data("webauthn.create", options["challenge"])),
                             "attestationObject": b64(att), "transports": ["usb"]}}

    def get(self, options: dict, counter: int | None = None) -> dict:
        self.counter = counter if counter is not None else self.counter + 1
        client_data = self._client_data("webauthn.get", options["challenge"])
        auth_data = hashlib.sha256(RP_ID.encode()).digest() + bytes([0x05]) + struct.pack(">I", self.counter)
        sig = self.key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64(self.cred_id), "rawId": b64(self.cred_id), "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": b64(client_data), "authenticatorData": b64(auth_data), "signature": b64(sig), "userHandle": None}}


def add_key(client, key: SoftKey, name="YubiKey"):
    o = client.post("/api/auth/mfa/webauthn/register/options").json()
    assert o["options"]["rp"]["id"] == RP_ID
    r = client.post("/api/auth/mfa/webauthn/register", json={"state": o["state"], "credential": key.create(o["options"]), "name": name})
    assert r.status_code == 200, r.text
    return r.json()


def password_step(env, username):
    return new_client(env).post("/api/auth/login", json={"username": username, "password": USER_PASSWORD}).json()


def key_step(env, challenge, key: SoftKey, counter=None):
    c = new_client(env)
    o = c.post("/api/auth/mfa/webauthn/options", json={"mfa_token": challenge["mfa_token"]}).json()
    r = c.post("/api/auth/mfa/webauthn/verify", json={"mfa_token": challenge["mfa_token"], "state": o["state"],
                                                     "credential": key.get(o["options"], counter)})
    return c, r


def test_security_key_sign_in(env):
    create_user(env, "kim")
    kim = new_client(env, "kim", USER_PASSWORD)
    key = SoftKey()
    first = add_key(kim, key)
    assert len(first["recovery_codes"]) == 10 and first["enabled"] and first["webauthn"][0]["name"] == "YubiKey"
    assert add_key(kim, SoftKey(), "Phone")["recovery_codes"] == []  # codes come with the first factor only

    ch = password_step(env, "kim")
    assert ch["mfa"] == "verify" and ch["methods"] == {"totp": False, "webauthn": True, "recovery_code": True}
    c, r = key_step(env, ch, key)
    assert r.status_code == 200 and c.get("/api/auth/me").json()["username"] == "kim"
    assert kim.get("/api/auth/mfa").json()["webauthn"][0]["last_used_at"]

    # Cloned key (counter going back) and unknown key are refused
    _, r = key_step(env, password_step(env, "kim"), key, counter=1)
    assert r.status_code == 401
    _, r = key_step(env, password_step(env, "kim"), SoftKey())
    assert r.status_code == 401

    # Recovery codes work for an account with security keys only
    ch = password_step(env, "kim")
    assert new_client(env).post("/api/auth/mfa/verify", json={"mfa_token": ch["mfa_token"], "code": first["recovery_codes"][0]}).status_code == 200

    # Removing every key turns the second factor off
    for k in kim.get("/api/auth/mfa").json()["webauthn"]:
        assert kim.delete(f"/api/auth/mfa/webauthn/{k['id']}").status_code == 200
    status = kim.get("/api/auth/mfa").json()
    assert status["enabled"] is False and status["recovery_codes_left"] == 0
    assert "mfa_token" not in password_step(env, "kim")


def test_required_factor_with_security_key(env):
    create_user(env, "lee")
    env.put("/api/admin/settings/mfa", json={"mode": "all", "groups": []})
    try:
        ch = password_step(env, "lee")
        assert ch["mfa"] == "setup"
        c = new_client(env)
        key = SoftKey()
        o = c.post("/api/auth/mfa/setup/webauthn/options", json={"mfa_token": ch["mfa_token"]}).json()
        r = c.post("/api/auth/mfa/setup/webauthn/confirm", json={"mfa_token": ch["mfa_token"], "state": o["state"],
                                                                  "credential": key.create(o["options"]), "name": "Passkey"})
        assert r.status_code == 200, r.text
        assert len(r.json()["recovery_codes"]) == 10 and c.get("/api/auth/me").status_code == 200

        # The last factor cannot be removed while it is required; with the authenticator app as well, it can
        key_id = c.get("/api/auth/mfa").json()["webauthn"][0]["id"]
        assert c.delete(f"/api/auth/mfa/webauthn/{key_id}").status_code == 400
        secret = c.post("/api/auth/mfa/enroll").json()["secret"]
        assert c.post("/api/auth/mfa/enable", json={"code": mfa.current_code(secret)}).json()["recovery_codes"] == []
        assert c.delete(f"/api/auth/mfa/webauthn/{key_id}").status_code == 200

        # A state of another user or purpose is refused
        o = c.post("/api/auth/mfa/webauthn/register/options").json()
        other = password_step(env, "lee")
        bad = new_client(env).post("/api/auth/mfa/webauthn/verify", json={"mfa_token": other["mfa_token"], "state": o["state"],
                                                                         "credential": key.get(o["options"])})
        assert bad.status_code in (400, 401)

        # Administrator reset: every factor removed
        lee_id = next(u["id"] for u in env.get("/api/admin/users").json() if u["username"] == "lee")
        assert env.delete(f"/api/admin/users/{lee_id}/mfa").json()["mfa_enabled"] is False
        assert password_step(env, "lee")["mfa"] == "setup"
    finally:
        env.put("/api/admin/settings/mfa", json={"mode": "optional", "groups": []})
