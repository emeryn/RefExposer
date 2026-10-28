"""Saved connectors to an inference server: switching between them, and what happens to their secrets.

The point of the secret tests is narrow and worth stating: an API key goes in, is never returned by any
endpoint, is not readable in the database, and survives an edit that does not mention it.
"""
import json

import pytest

from conftest import USER_PASSWORD, create_user, new_client
from test_assistant import configure


def profile(env, **changes) -> dict:
    body = {"name": "Workstation", "provider": "ollama", "base_url": "http://ollama.internal:11434",
            "model": "qwen2.5:7b", **changes}
    r = env.post("/api/admin/llm-profiles", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def rows(env) -> list[dict]:
    r = env.get("/api/admin/llm-profiles")
    assert r.status_code == 200, r.text
    return r.json()


def stored(env, profile_id: int) -> dict:
    """The row as it really is in the database."""
    from app.db import session_factory
    from app.models import LlmProfile

    with session_factory()() as db:
        p = db.get(LlmProfile, profile_id)
        return {"secrets_enc": p.secrets_enc, "header_names": p.header_names, "name": p.name}


# --------------------------------------------------------------------------- the profiles

def test_a_profile_is_saved_and_becomes_the_one_in_use(env):
    p = profile(env)
    assert p["active"] is True  # the first one saved: nothing would change otherwise
    assert p["configured"] is True and p["small_model"] is False
    assert [r["name"] for r in rows(env)] == ["Workstation"]


def test_a_second_profile_does_not_steal_the_active_one(env):
    first = profile(env)
    second = profile(env, name="GPU server", model="llama3.3:70b")
    assert second["active"] is False
    assert [r["id"] for r in rows(env) if r["active"]] == [first["id"]]


def test_switching_is_one_call(env):
    first = profile(env)
    second = profile(env, name="GPU server", model="llama3.3:70b")
    assert env.post(f"/api/admin/llm-profiles/{second['id']}/activate").status_code == 200
    active = [r for r in rows(env) if r["active"]]
    assert [r["id"] for r in active] == [second["id"]]
    # Exactly one, always
    assert len(active) == 1
    assert env.get(f"/api/admin/llm-profiles/{first['id']}").json()["active"] is False


def test_two_profiles_cannot_share_a_name(env):
    profile(env)
    assert env.post("/api/admin/llm-profiles", json={"name": "Workstation"}).status_code == 409


def test_a_profile_is_copied_to_try_a_variant(env):
    source = profile(env, api_key="sk-secret-value", small_model=False)
    copy = env.post(f"/api/admin/llm-profiles/{source['id']}/duplicate")
    assert copy.status_code == 201, copy.text
    copy = copy.json()
    assert copy["name"] == "Workstation (copy)" and copy["active"] is False
    assert copy["base_url"] == source["base_url"] and copy["model"] == source["model"]
    # The key comes with it: trying another model on the same gateway must not mean typing it again
    assert copy["api_key_set"] is True


def test_deleting_the_active_profile_hands_over_to_another(env):
    first = profile(env)
    second = profile(env, name="GPU server")
    r = env.delete(f"/api/admin/llm-profiles/{first['id']}")
    assert r.status_code == 200 and r.json()["was_active"] is True
    assert [r["id"] for r in rows(env) if r["active"]] == [second["id"]]


def test_the_address_of_the_server_is_checked(env):
    """The same rule as everywhere else: never a metadata service, never credentials in the address."""
    refused = env.post("/api/admin/llm-profiles",
                       json={"name": "Bad", "base_url": "http://169.254.169.254/latest/meta-data/"})
    assert refused.status_code == 422 and "link-local or metadata" in refused.text
    assert env.post("/api/admin/llm-profiles",
                    json={"name": "Bad2", "base_url": "http://user:pw@ollama.internal:11434"}).status_code == 422


def test_an_ordinary_user_cannot_see_the_profiles(env):
    profile(env)
    create_user(env, "reader")
    client = new_client(env, "reader", USER_PASSWORD)
    assert client.get("/api/admin/llm-profiles").status_code == 403


# --------------------------------------------------------------------------- the secrets

def test_the_api_key_never_comes_back(env):
    p = profile(env, api_key="sk-super-secret", headers={"X-Tenant": "acme", "X-Gateway-Key": "gw-secret"})
    assert p["api_key_set"] is True
    assert "sk-super-secret" not in json.dumps(p)
    assert "gw-secret" not in json.dumps(p)
    # The names are there, so the interface can show what is configured
    assert p["header_names"] == ["X-Gateway-Key", "X-Tenant"]
    assert "sk-super-secret" not in json.dumps(rows(env))
    assert "sk-super-secret" not in env.get(f"/api/admin/llm-profiles/{p['id']}").text


def test_the_secrets_are_encrypted_in_the_database(env):
    p = profile(env, api_key="sk-super-secret", headers={"X-Gateway-Key": "gw-secret"})
    row = stored(env, p["id"])
    assert row["secrets_enc"] and "sk-super-secret" not in row["secrets_enc"]
    assert "gw-secret" not in row["secrets_enc"]
    # The names are in clear on purpose: the interface lists them without the values
    assert row["header_names"] == ["X-Gateway-Key"]


def test_the_secrets_are_readable_by_the_application(env):
    from app.db import session_factory
    from app.llmprofiles import secrets_of
    from app.models import LlmProfile

    p = profile(env, api_key="sk-super-secret", headers={"X-Gateway-Key": "gw-secret"})
    with session_factory()() as db:
        secrets = secrets_of(db.get(LlmProfile, p["id"]))
    assert secrets == {"api_key": "sk-super-secret", "headers": {"X-Gateway-Key": "gw-secret"}}


def test_an_edit_that_says_nothing_about_the_key_keeps_it(env):
    p = profile(env, api_key="sk-super-secret")
    env.patch(f"/api/admin/llm-profiles/{p['id']}", json={"model": "qwen2.5:14b"})
    after = env.get(f"/api/admin/llm-profiles/{p['id']}").json()
    assert after["model"] == "qwen2.5:14b" and after["api_key_set"] is True


def test_an_empty_key_keeps_it_and_the_clear_flag_removes_it(env):
    p = profile(env, api_key="sk-super-secret")
    env.patch(f"/api/admin/llm-profiles/{p['id']}", json={"api_key": ""})
    assert env.get(f"/api/admin/llm-profiles/{p['id']}").json()["api_key_set"] is True
    env.patch(f"/api/admin/llm-profiles/{p['id']}", json={"api_key_clear": True})
    assert env.get(f"/api/admin/llm-profiles/{p['id']}").json()["api_key_set"] is False


def test_a_masked_header_value_keeps_the_stored_one(env):
    from app.secretstore import MASK

    p = profile(env, api_key="sk-k", headers={"X-Gateway-Key": "gw-secret", "X-Tenant": "acme"})
    # What the interface sends back: it only ever received the names
    env.patch(f"/api/admin/llm-profiles/{p['id']}",
              json={"headers": {"X-Gateway-Key": MASK, "X-Tenant": "other"}})
    from app.db import session_factory
    from app.llmprofiles import secrets_of
    from app.models import LlmProfile

    with session_factory()() as db:
        secrets = secrets_of(db.get(LlmProfile, p["id"]))
    assert secrets["headers"] == {"X-Gateway-Key": "gw-secret", "X-Tenant": "other"}


def test_the_key_is_not_written_to_the_audit_log(env):
    profile(env, api_key="sk-super-secret", headers={"X-Gateway-Key": "gw-secret"})
    entries = env.get("/api/admin/audit", params={"action": "llm_profile.create"}).json()["rows"]
    assert entries and "sk-super-secret" not in json.dumps(entries)
    assert "gw-secret" not in json.dumps(entries)
    assert entries[0]["detail"]["api_key"] is True  # that there is one, not what it is


# --------------------------------------------------------------------------- what the assistant uses

def test_the_active_profile_is_what_the_assistant_connects_with(env):
    from app.appsettings import load
    from app.db import session_factory

    configure(env, agent_enabled=True)
    small = profile(env, name="Tiny", base_url="http://tiny.internal:11434", model="llama3.2:1b",
                    api_key="sk-tiny", small_model=True, temperature=0.0)
    with session_factory()() as db:
        cfg = load(db, "assistant")
    assert cfg.base_url == "http://tiny.internal:11434" and cfg.model == "llama3.2:1b"
    assert cfg.api_key == "sk-tiny" and cfg.small_model is True and cfg.temperature == 0.0
    # The policy does not move with the model
    assert cfg.agent_enabled is True
    assert cfg.profile_id == small["id"] and cfg.profile_name == "Tiny"


def test_switching_profile_switches_the_model_at_once(env):
    from app.appsettings import load
    from app.db import session_factory

    configure(env, agent_enabled=True)
    profile(env, name="Tiny", model="llama3.2:1b", small_model=True)
    big = profile(env, name="Big", model="llama3.3:70b", small_model=False)
    env.post(f"/api/admin/llm-profiles/{big['id']}/activate")
    with session_factory()() as db:
        cfg = load(db, "assistant")
    assert cfg.model == "llama3.3:70b" and cfg.small_model is False


def test_without_any_profile_the_stored_settings_are_used(env):
    """An instance configured before the profiles existed keeps working."""
    from app.appsettings import load
    from app.db import session_factory

    configure(env, agent_enabled=True)
    assert rows(env) == []
    with session_factory()() as db:
        cfg = load(db, "assistant")
    assert cfg.base_url == "http://ollama.internal:11434" and cfg.model == "test-model"
    assert cfg.profile_id is None


def test_the_connection_is_bootstrapped_into_a_profile(env):
    from app.db import session_factory
    from app.llmprofiles import DEFAULT_NAME, bootstrap, secrets_of

    configure(env, api_key="sk-existing")
    with session_factory()() as db:
        created = bootstrap(db)
        assert created is not None and created.name == DEFAULT_NAME and created.active is True
        assert created.base_url == "http://ollama.internal:11434" and created.model == "test-model"
        assert secrets_of(created)["api_key"] == "sk-existing"
        # Only once
        assert bootstrap(db) is None
