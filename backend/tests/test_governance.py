"""Governance features: visibility and ownership, masking of personal data, time travel, watchdogs.

The masking tests check the thing that matters: masked is what everyone gets, everywhere, and the real values
only come out for an account that is entitled **and** asks for them. So they go through the row API, the
export, the generated downloads, the SQL console and the assistant, not only one of them.
"""
import pytest
import yaml

from conftest import USER_PASSWORD, create_user, new_client, refresh
from test_assistant import llm  # noqa: F401 (fixture: the assistant tests script the model)

PEOPLE = """id,email,full_name,dept
1,alice@example.com,Alice Martin,IT
2,bob@example.com,Bob Durand,HR
3,carol@example.com,Carol Petit,IT
"""

PEOPLE_V2 = """id,email,full_name,dept
1,alice@example.com,Alice Martin,Security
2,bob@example.com,Bob Durand,HR
4,dave@example.com,Dave Leroy,IT
"""

DEFINITION = {
    "referentials": [{
        "id": "people",
        "name": "Staff directory",
        "category": "HR",
        "visibility": "internal",
        "owner": "dpo@example.com",
        "owner_team": "Security",
        "source": {"type": "local", "path": "raw/*.csv"},
        "format": "csv",
        "key": "id",
        "primary_key_columns": ["id"],
        "pii": {"columns": ["email", "full_name"], "strategy": "hash", "reveal_groups": ["dpo"],
                "note": "GDPR: staff directory, 3 years"},
        "storage": {"time_travel": True, "max_snapshots": 3},
    }]
}


def _write(env, csv: str, definition: dict = None) -> None:
    config = env.data_dir.parent / "config"
    raw = env.data_dir / "people" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "p.csv").write_text(csv, encoding="utf-8")
    (config / "people.yml").write_text(yaml.safe_dump(definition or DEFINITION, sort_keys=False), encoding="utf-8")
    assert env.post("/api/system/reload").status_code == 200


@pytest.fixture()
def people(env):
    """A referential with two personal data columns, masked for everyone by default."""
    _write(env, PEOPLE)
    refresh(env, "people")
    return "people"


def rows(client, ref="people", **params):
    r = client.get(f"/api/referentials/{ref}/rows", params=params)
    assert r.status_code == 200, r.text
    return r.json()["rows"]


# --------------------------------------------------------------------------- governance metadata

def test_visibility_and_owner_are_published(env, people):
    summary = next(r for r in env.get("/api/referentials").json() if r["id"] == "people")
    assert summary["visibility"] == "internal"
    assert summary["owner"] == "dpo@example.com"
    assert summary["owner_team"] == "Security"
    # A referential that says nothing keeps the safe default: company data, not open data
    other = next(r for r in env.get("/api/referentials").json() if r["id"] == "countries")
    assert other["visibility"] == "internal"


def test_public_visibility_is_kept(env):
    definition = {"referentials": [{**DEFINITION["referentials"][0], "visibility": "public", "pii": None}]}
    _write(env, PEOPLE, definition)
    assert env.get("/api/referentials/people").json()["visibility"] == "public"


# --------------------------------------------------------------------------- masking of personal data

def test_personal_data_is_masked_by_default_even_for_an_administrator(env, people):
    first = rows(env, limit=1)[0]
    assert first["id"] == 1 and first["dept"] == "IT"          # ordinary columns untouched
    assert "alice@example.com" not in str(first)
    assert "Alice Martin" not in str(first)
    assert len(first["email"]) == 16 and first["email"] != first["full_name"]  # stable pseudonyms
    state = env.get("/api/referentials/people").json()["pii"]
    assert state["columns"] == ["email", "full_name"] and state["strategy"] == "hash"
    assert state["can_reveal"] is True and state["missing"] == []


def test_the_pseudonym_is_stable_so_rows_can_still_be_grouped(env, people):
    everyone = rows(env, limit=10)
    assert len({r["email"] for r in everyone}) == 3  # three people, three distinct pseudonyms
    again = rows(env, limit=10)
    assert [r["email"] for r in everyone] == [r["email"] for r in again]


def test_an_entitled_account_sees_the_values_only_when_it_asks(env, people):
    masked = rows(env, limit=1)[0]
    assert "alice@example.com" not in str(masked)
    revealed = rows(env, limit=1, reveal="true")[0]
    assert revealed["email"] == "alice@example.com" and revealed["full_name"] == "Alice Martin"


def test_revealing_is_written_to_the_audit_log(env, people):
    rows(env, limit=1, reveal="true")
    entries = env.get("/api/admin/audit", params={"action": "pii.reveal"}).json()["rows"]
    assert entries and entries[0]["target"] == "ref:people"
    assert entries[0]["detail"]["columns"] == ["email", "full_name"]


def test_a_reader_without_the_right_cannot_reveal(env, people):
    create_user(env, "reader")
    env.post("/api/admin/grants", json={"referential_id": "people", "user_id": _id(env, "reader"), "level": "read"})
    client = new_client(env, "reader", USER_PASSWORD)
    assert "alice@example.com" not in str(rows(client, limit=3))
    refused = client.get("/api/referentials/people/rows", params={"reveal": "true"})
    assert refused.status_code == 403
    assert client.get("/api/referentials/people").json()["pii"]["can_reveal"] is False


def test_a_member_of_the_allowed_group_may_reveal(env, people):
    group = env.post("/api/admin/groups", json={"name": "dpo"}).json()
    create_user(env, "officer", group_ids=[group["id"]])
    env.post("/api/admin/grants", json={"referential_id": "people", "group_id": group["id"], "level": "read"})
    client = new_client(env, "officer", USER_PASSWORD)
    assert client.get("/api/referentials/people").json()["pii"]["can_reveal"] is True
    revealed = rows(client, limit=1, reveal="true")[0]
    assert revealed["full_name"] == "Alice Martin"


def test_a_named_account_may_reveal(env, people):
    create_user(env, "analyst")
    user_id = _id(env, "analyst")
    env.post("/api/admin/grants", json={"referential_id": "people", "user_id": user_id, "level": "read"})
    definition = {"referentials": [{**DEFINITION["referentials"][0],
                                    "pii": {**DEFINITION["referentials"][0]["pii"], "reveal_users": [user_id],
                                            "reveal_groups": []}}]}
    _write(env, PEOPLE, definition)
    client = new_client(env, "analyst", USER_PASSWORD)
    assert rows(client, limit=1, reveal="true")[0]["email"] == "alice@example.com"


def test_export_and_generated_downloads_are_masked(env, people):
    export = env.get("/api/referentials/people/export", params={"format": "csv"})
    assert export.status_code == 200 and "alice@example.com" not in export.text
    download = env.get("/api/referentials/people/download/csv")
    assert download.status_code == 200 and "alice@example.com" not in download.text
    # Entitled and asking for it: the export does serve the values
    clear = env.get("/api/referentials/people/export", params={"format": "csv", "reveal": "true"})
    assert "alice@example.com" in clear.text


def test_the_source_files_are_not_downloadable_without_the_right(env, people):
    create_user(env, "plain")
    env.post("/api/admin/grants", json={"referential_id": "people", "user_id": _id(env, "plain"), "level": "read"})
    client = new_client(env, "plain", USER_PASSWORD)
    refused = client.get("/api/referentials/people/download/source")
    assert refused.status_code == 403 and "masked personal data" in refused.text
    assert env.get("/api/referentials/people/download/source").status_code == 200  # administrator


def test_the_sql_console_is_masked_unless_asked(env, people):
    masked = env.post("/api/sql", json={"sql": "SELECT email FROM people ORDER BY id"}).json()
    assert "alice@example.com" not in str(masked["rows"])
    clear = env.post("/api/sql", json={"sql": "SELECT email FROM people ORDER BY id", "reveal": True}).json()
    assert clear["rows"][0][0] == "alice@example.com"
    schema = next(t for t in env.get("/api/sql/schema").json() if t["table"] == "people")
    assert schema["pii"]["columns"] == ["email", "full_name"]


def test_an_advanced_user_cannot_reveal_through_the_sql_console(env, people):
    create_user(env, "power", role="advanced")
    env.post("/api/admin/grants", json={"referential_id": "people", "user_id": _id(env, "power"), "level": "read"})
    client = new_client(env, "power", USER_PASSWORD)
    result = client.post("/api/sql", json={"sql": "SELECT email FROM people ORDER BY id", "reveal": True})
    assert result.status_code == 200 and "alice@example.com" not in str(result.json()["rows"])


def test_lookup_and_column_statistics_are_masked(env, people):
    found = env.get("/api/referentials/people/lookup/1").json()
    assert "alice@example.com" not in str(found)
    stats = env.get("/api/referentials/people/columns/email/stats").json()
    assert all("@" not in str(v["value"]) for v in stats["top"])
    clear = env.get("/api/referentials/people/columns/email/stats", params={"reveal": "true"}).json()
    assert any("@" in str(v["value"]) for v in clear["top"])


def test_the_batch_lookup_is_masked_unless_asked(env, people):
    masked = env.post("/api/referentials/people/lookup", json={"values": ["1", "2"]}).json()
    assert masked["found"] == 2 and "alice@example.com" not in str(masked["results"])
    clear = env.post("/api/referentials/people/lookup", json={"values": ["1"], "reveal": True}).json()
    assert clear["results"]["1"]["email"] == "alice@example.com"


def test_a_unified_referential_does_not_inherit_the_values(env, people):
    definition = {"referentials": [DEFINITION["referentials"][0], {
        "id": "people-it",
        "name": "IT staff",
        "source": {"type": "referential", "referentials": ["people"]},
        "transform": "SELECT * FROM {source0} WHERE dept = 'IT'",
        "key": "id",
    }]}
    _write(env, PEOPLE, definition)
    refresh(env, "people-it")
    # The unified referential is a publication of its own: it was built from masked rows, so the values are
    # not in its file at all and nobody can reveal them there
    built = rows(env, "people-it", limit=10, reveal="true")
    assert built and "alice@example.com" not in str(built)


def test_a_column_that_disappeared_is_reported_not_silently_unmasked(env, people):
    _write(env, "id,email,dept\n1,alice@example.com,IT\n")
    refresh(env, "people", force=True)
    detail = env.get("/api/referentials/people").json()
    assert detail["pii"]["missing"] == ["full_name"]
    assert "alice@example.com" not in str(rows(env, limit=5))  # the column still there is still masked


# --------------------------------------------------------------------------- time travel

def test_each_version_is_kept_and_can_be_compared(env, people):
    _write(env, PEOPLE_V2)
    refresh(env, "people", force=True)
    listing = env.get("/api/referentials/people/snapshots").json()
    assert listing["enabled"] is True and listing["keys"] == ["id"]
    assert [s["version"] for s in listing["snapshots"]] == [2, 1]
    assert all(s["available"] for s in listing["snapshots"])

    first = listing["snapshots"][-1]["id"]
    summary = env.get("/api/referentials/people/diff", params={"from": first, "to": "latest"}).json()
    assert summary["added"] == 1 and summary["removed"] == 1 and summary["modified"] == 1
    assert summary["comparable"] is True and summary["keys"] == ["id"]

    added = env.get("/api/referentials/people/diff", params={"from": first, "kind": "added"}).json()
    assert [r["id"] for r in added["rows"]] == [4]
    removed = env.get("/api/referentials/people/diff", params={"from": first, "kind": "removed"}).json()
    assert [r["id"] for r in removed["rows"]] == [3]
    modified = env.get("/api/referentials/people/diff", params={"from": first, "kind": "modified"}).json()
    assert modified["rows"][0]["key"] == "1"
    assert {c["column"]: (c["before"], c["after"]) for c in modified["rows"][0]["changes"]} == {
        "dept": ("IT", "Security")}


def test_a_comparison_masks_the_personal_data_columns(env, people):
    _write(env, PEOPLE_V2)
    refresh(env, "people", force=True)
    first = env.get("/api/referentials/people/snapshots").json()["snapshots"][-1]["id"]
    added = env.get("/api/referentials/people/diff", params={"from": first, "kind": "added"}).json()
    assert "dave@example.com" not in str(added["rows"])
    clear = env.get("/api/referentials/people/diff", params={"from": first, "kind": "added", "reveal": "true"}).json()
    assert clear["rows"][0]["email"] == "dave@example.com"


def test_the_retention_policy_drops_the_oldest_snapshots(env, people):
    for dept in ("A", "B", "C"):
        _write(env, PEOPLE.replace(",IT", f",{dept}"))
        refresh(env, "people", force=True)
    listing = env.get("/api/referentials/people/snapshots").json()
    assert len(listing["snapshots"]) == 3  # max_snapshots
    assert [s["version"] for s in listing["snapshots"]] == [4, 3, 2]
    folder = env.data_dir / "people" / "snapshots"
    assert len(list(folder.glob("*.parquet"))) == 3  # the files of the dropped ones are gone too


def test_time_travel_is_off_unless_asked(env):
    _write(env, PEOPLE, {"referentials": [{**DEFINITION["referentials"][0], "storage": {}}]})
    refresh(env, "people")
    listing = env.get("/api/referentials/people/snapshots").json()
    assert listing["enabled"] is False and listing["snapshots"] == []
    assert not (env.data_dir / "people" / "snapshots").exists()


def test_a_snapshot_can_be_deleted(env, people):
    _write(env, PEOPLE_V2)
    refresh(env, "people", force=True)
    oldest = env.get("/api/referentials/people/snapshots").json()["snapshots"][-1]
    assert env.delete(f"/api/referentials/people/snapshots/{oldest['id']}").status_code == 200
    assert [s["id"] for s in env.get("/api/referentials/people/snapshots").json()["snapshots"]] != [oldest["id"]]


def test_a_referential_a_user_cannot_read_has_no_history(env, people):
    create_user(env, "stranger")
    client = new_client(env, "stranger", USER_PASSWORD)
    assert client.get("/api/referentials/people/snapshots").status_code == 404
    assert client.get("/api/referentials/people/diff", params={"from": "1"}).status_code == 404


# --------------------------------------------------------------------------- leaving the instance

def test_an_export_does_not_carry_the_accounts_allowed_to_reveal(env, people):
    """Account ids belong to one instance: carried over, they would name whoever has that id elsewhere."""
    create_user(env, "analyst")
    definition = {"referentials": [{**DEFINITION["referentials"][0],
                                    "pii": {**DEFINITION["referentials"][0]["pii"],
                                            "reveal_users": [_id(env, "analyst")]}}]}
    _write(env, PEOPLE, definition)
    exported = env.get("/api/admin/config/export", params={"ids": "people", "format": "json"})
    assert exported.status_code == 200, exported.text
    entry = next(r for r in exported.json()["referentials"] if r["id"] == "people")
    assert entry["pii"]["columns"] == ["email", "full_name"]
    assert entry["pii"]["reveal_groups"] == ["dpo"]      # named: they travel
    assert entry["pii"].get("reveal_users", []) == []    # ids: they do not


def test_a_replica_says_the_data_arrived_masked(env, people):
    """A peer serves generated downloads from the masked view, so a replica never receives the real values."""
    from app.federation import Peer, catalog_entry, local_definition

    service = env.app_.state.service
    entry = catalog_entry(service, service.refs["people"])
    assert entry["pii_columns"] == ["email", "full_name"]
    assert entry["visibility"] == "internal" and entry["owner_team"] == "Security"

    peer = Peer(name="central", url="https://central.example/api", secret="peer-central")
    replica = local_definition(peer, entry)
    assert "received masked from central" in replica["description"]
    assert "email, full_name" in replica["description"]
    # No masking is applied again on the replica: that would mask a pseudonym
    assert "pii" not in replica
    assert replica["visibility"] == "internal" and replica["owner_team"] == "Security"


# --------------------------------------------------------------------------- the assistant

def test_the_assistant_never_receives_the_real_values(env, people, llm):
    """The model acts for a user but cannot give the consent, so `reveal` is simply not available to it."""
    import json

    from test_assistant import ask, configure, final, tool_call

    configure(env, agent_enabled=True)
    provider = llm(tool_call("query_rows", id="people", limit=5),
                   tool_call("run_sql", sql="SELECT email, full_name FROM people"),
                   "Those columns are masked, I cannot read them.")
    _, events = ask(env, "List the staff with their e-mail addresses")
    exchanged = json.dumps(provider.calls) + json.dumps(events)
    assert "alice@example.com" not in exchanged and "Alice Martin" not in exchanged
    assert all(step["ok"] for step in final(events)["steps"])  # the calls worked, they were just masked


def test_the_assistant_cannot_ask_to_reveal(env, people, llm):
    """Even spelling `reveal` out in the SQL it writes changes nothing: the sandbox it gets is the masked one."""
    import json

    from test_assistant import ask, configure, tool_call

    configure(env, agent_enabled=True)
    provider = llm(tool_call("query_rows", id="people", reveal=True, limit=5), "done")
    ask(env, "Show me the e-mail addresses in clear")
    assert provider.calls
    assert "alice@example.com" not in json.dumps(provider.calls)


def _id(env, username: str) -> int:
    return next(u["id"] for u in env.get("/api/admin/users").json() if u["username"] == username)


# --------------------------------------------------------------------------- saved states, restore, freeze

def saved(env, ref="people"):
    return [s for s in env.get(f"/api/referentials/{ref}/snapshots").json()["snapshots"] if s["manual"]]


def wait_run(env, ref="people"):
    from test_admin_referentials import wait_idle

    return wait_idle(env, ref)


def test_a_state_can_be_saved_by_hand_without_time_travel(env):
    """Saving a state is a decision, not a setting: it does not need time travel to be on."""
    _write(env, PEOPLE, {"referentials": [{**DEFINITION["referentials"][0], "storage": {}}]})
    refresh(env, "people")
    r = env.post("/api/referentials/people/snapshots", json={"label": "before the migration"})
    assert r.status_code == 201, r.text
    snap = r.json()
    assert snap["version"] == 1 and snap["label"] == "before the migration" and snap["manual"] is True

    listing = env.get("/api/referentials/people/snapshots").json()
    assert listing["enabled"] is False  # time travel is still off
    kept = listing["snapshots"][0]
    assert kept["manual"] is True and kept["created_by"] == "admin" and kept["available"] is True
    assert kept["row_count"] == 3


def test_a_saved_state_survives_the_retention(env, people):
    """The oldest automatic snapshots are dropped; a state someone saved on purpose is not."""
    env.post("/api/referentials/people/snapshots", json={"label": "keep me"})
    for dept in ("A", "B", "C"):
        _write(env, PEOPLE.replace(",IT", f",{dept}"))
        refresh(env, "people", force=True)
    listing = env.get("/api/referentials/people/snapshots").json()
    automatic = [s for s in listing["snapshots"] if not s["manual"]]
    assert len(automatic) == 3  # max_snapshots
    assert [s["label"] for s in listing["snapshots"] if s["manual"]] == ["keep me"]


def test_a_saved_state_can_be_deleted_one_by_one(env, people):
    one = env.post("/api/referentials/people/snapshots", json={"label": "first"}).json()
    two = env.post("/api/referentials/people/snapshots", json={"label": "second"}).json()
    assert len(saved(env)) == 2
    assert env.delete(f"/api/referentials/people/snapshots/{one['id']}").status_code == 200
    assert [s["label"] for s in saved(env)] == ["second"]
    assert env.delete(f"/api/referentials/people/snapshots/{two['id']}").status_code == 200
    assert saved(env) == []


def test_a_saved_state_is_compared_with_the_published_version(env, people):
    """What the metrics of the diff are for: seeing what moved since the state was saved."""
    snap = env.post("/api/referentials/people/snapshots", json={"label": "before"}).json()
    _write(env, PEOPLE_V2)
    refresh(env, "people", force=True)
    summary = env.get("/api/referentials/people/diff", params={"from": snap["id"], "to": "latest"}).json()
    assert summary["added"] == 1 and summary["removed"] == 1 and summary["modified"] == 1


def test_restoring_publishes_the_saved_data_again_as_a_new_version(env, people):
    snap = env.post("/api/referentials/people/snapshots", json={"label": "the good one"}).json()
    _write(env, PEOPLE_V2)
    refresh(env, "people", force=True)
    before = env.get("/api/referentials/people").json()
    assert before["version"] == 2 and {r["id"] for r in rows(env)} == {1, 2, 4}

    started = env.post(f"/api/referentials/people/snapshots/{snap['id']}/restore")
    assert started.status_code == 202, started.text
    run = wait_run(env)
    assert run["status"] == "success" and run["trigger"] == "restore"
    # Restoring is publishing, not rewinding: the data comes back, the version number moves forward
    assert run["version"] == 3 and run["restored_from"] == 1
    assert {r["id"] for r in rows(env)} == {1, 2, 3}
    after = env.get("/api/referentials/people").json()
    assert after["version"] == 3 and after["status"] == "ok"
    assert after["restored"]["version"] == 1 and after["restored"]["by"] == "admin"


def test_restoring_freezes_the_referential(env, people):
    """Without it the next scheduled update would read the source again and undo the restore."""
    snap = env.post("/api/referentials/people/snapshots", json={}).json()
    _write(env, PEOPLE_V2)
    refresh(env, "people", force=True)
    env.post(f"/api/referentials/people/snapshots/{snap['id']}/restore")
    wait_run(env)
    detail = env.get("/api/referentials/people").json()
    assert detail["pinned"] and "restored" in detail["pinned"]["reason"]
    assert env.delete("/api/referentials/people/pin").json() == {"pinned": False}
    assert env.get("/api/referentials/people").json()["pinned"] is None


def test_a_restore_can_itself_be_undone(env, people):
    """The state being replaced is saved first, so a restore made by mistake is one more restore away."""
    snap = env.post("/api/referentials/people/snapshots", json={"label": "v1"}).json()
    _write(env, PEOPLE_V2)
    refresh(env, "people", force=True)
    env.post(f"/api/referentials/people/snapshots/{snap['id']}/restore")
    wait_run(env)
    automatic = [s for s in saved(env) if s["label"].startswith("Before restoring")]
    assert len(automatic) == 1 and automatic[0]["row_count"] == 3
    env.post(f"/api/referentials/people/snapshots/{automatic[0]['id']}/restore")
    wait_run(env)
    assert {r["id"] for r in rows(env)} == {1, 2, 4}  # back to the version that was published before


def test_a_reader_can_neither_save_nor_restore_nor_freeze(env, people):
    create_user(env, "reader")
    env.post("/api/admin/grants", json={"referential_id": "people",
                                        "user_id": next(u["id"] for u in env.get("/api/admin/users").json()
                                                        if u["username"] == "reader"), "level": "read"})
    snap = env.post("/api/referentials/people/snapshots", json={}).json()
    client = new_client(env, "reader", USER_PASSWORD)
    assert client.post("/api/referentials/people/snapshots", json={}).status_code == 403
    assert client.post(f"/api/referentials/people/snapshots/{snap['id']}/restore").status_code == 403
    assert client.post("/api/referentials/people/pin", json={}).status_code == 403
    assert client.delete("/api/referentials/people/pin").status_code == 403


def test_a_referential_can_be_frozen_by_hand(env, people):
    frozen = env.post("/api/referentials/people/pin", json={"reason": "audit in progress"})
    assert frozen.status_code == 200, frozen.text
    pinned = env.get("/api/referentials/people").json()["pinned"]
    assert pinned["reason"] == "audit in progress" and pinned["by"] == "admin"

    # Automatic updates are skipped; an update started by hand is refused while it is frozen
    from app.service import Service

    service = env.app_.state.service
    assert service.submit("people", "schedule") is None
    assert service.submit("people", "sync") is None
    assert env.post("/api/referentials/people/refresh").status_code == 409

    env.delete("/api/referentials/people/pin")
    assert env.get("/api/referentials/people").json()["pinned"] is None
    assert env.post("/api/referentials/people/refresh").status_code == 200


def test_an_unknown_snapshot_is_not_restored(env, people):
    assert env.post("/api/referentials/people/snapshots/999999/restore").status_code == 404


def test_an_internal_referential_is_not_restored_from_its_file(env):
    """Its rows live in the database: a restored file would be undone by the next edit."""
    import time

    assert env.post("/api/internal-referentials", json={
        "id": "assets", "name": "Assets", "key": "host",
        "columns": [{"name": "host", "type": "text", "required": True}]}).status_code == 201
    env.post("/api/referentials/assets/records", json={"host": "srv-1"})
    deadline = time.time() + 20
    while time.time() < deadline and not env.get("/api/referentials/assets").json().get("version"):
        time.sleep(0.2)
    assert env.get("/api/referentials/assets").json()["version"], "the internal referential never published"

    snap = env.post("/api/referentials/assets/snapshots", json={"label": "one row"})
    assert snap.status_code == 201, snap.text
    refused = env.post(f"/api/referentials/assets/snapshots/{snap.json()['id']}/restore")
    assert refused.status_code == 400 and "Edit tab" in refused.text
