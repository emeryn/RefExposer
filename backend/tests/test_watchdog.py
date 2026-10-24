"""Scheduled watchdogs: the scope they are confined to, the structured verdict, and who gets told.

The inference server is scripted (as in test_assistant.py), so the agent loop, the session the investigation
runs with and the authorization of every tool call are the real ones.
"""
import json

import pytest

from conftest import USER_PASSWORD, create_user, new_client, refresh
from test_assistant import Scripted, configure, tool_call


@pytest.fixture()
def model(env, monkeypatch):
    """Configures the assistant and lets a test script what the watchdog's model answers."""
    import app.llm.provider as provider

    holder = {}

    def script(*replies):
        holder["provider"] = Scripted(list(replies))
        return holder["provider"]

    monkeypatch.setattr(provider, "build", lambda cfg, settings, proxy: holder["provider"])
    configure(env, agent_enabled=True, watchdogs_enabled=True)
    return script


def verdict(**fields) -> str:
    payload = {"finding_detected": False, "severity": "LOW", "summary": "nothing to report",
               "details_markdown": "", "affected_items_count": 0, **fields}
    return json.dumps(payload)


def create(env, **changes):
    body = {"name": "Shadow IT", "referential_ids": ["countries"], "trigger_type": "cron",
            "schedule": "0 7 * * *", "prompt": "Compare them.", **changes}
    return env.post("/api/watchdogs", json=body)


def run(env, task_id: int, username: str | None = None) -> dict:
    """Run one investigation synchronously (the API starts it in the background)."""
    from app import watchdog

    return watchdog.execute(env.app_.state.service, task_id, "manual", username)


# --------------------------------------------------------------------------- definition

def test_a_watchdog_needs_the_agent_mode(env):
    configure(env, agent_enabled=False, watchdogs_enabled=True)
    assert create(env).status_code == 403
    assert env.get("/api/watchdogs").status_code == 403


def test_the_templates_are_offered(env, model):
    templates = env.get("/api/watchdogs/templates").json()
    ids = [t["id"] for t in templates]
    assert ids == ["shadow_it", "easm_drift", "iam_orphans", "custom"]
    assert all(t["prompt"] for t in templates if t["id"] != "custom")
    assert next(t for t in templates if t["id"] == "custom")["prompt"] == ""


def test_a_watchdog_is_created_with_an_explicit_scope(env, model):
    task = create(env, template="shadow_it").json()
    assert task["referential_ids"] == ["countries"]
    assert task["username"] == "admin" and task["template"] == "shadow_it"
    assert task["last_run"] is None and task["running"] is False


def test_an_empty_scope_is_refused(env, model):
    assert create(env, referential_ids=[]).status_code == 422


def test_an_unknown_referential_is_refused(env, model):
    assert create(env, referential_ids=["nope"]).status_code == 400


def test_a_referential_the_author_cannot_read_is_not_there(env, model):
    create_user(env, "power", role="advanced")
    client = new_client(env, "power", USER_PASSWORD)
    refused = create(client, referential_ids=["countries"])
    assert refused.status_code == 404  # it does not exist for that account


def test_a_scheduled_watchdog_needs_a_valid_crontab(env, model):
    assert create(env, schedule="").status_code == 400
    assert create(env, schedule="not a cron").status_code == 400


def test_a_watchdog_on_synchronization_needs_no_schedule(env, model):
    task = create(env, trigger_type="on_sync", schedule=None).json()
    assert task["trigger_type"] == "on_sync" and task["next_run_at"] is None


def test_an_ordinary_user_does_not_see_watchdogs_at_all(env, model):
    """They read on a schedule with nobody watching: administrators and advanced users only."""
    create_user(env, "reader")
    env.post("/api/admin/grants", json={"referential_id": "countries",
                                        "user_id": _id(env, "reader"), "level": "read"})
    client = new_client(env, "reader", USER_PASSWORD)
    assert create(client).status_code == 403
    assert client.get("/api/watchdogs").status_code == 403
    assert client.get("/api/watchdogs/runs").status_code == 403
    assert client.get("/api/assistant").json()["watchdogs"] is False


def test_a_watchdog_is_private_to_its_owner(env, model):
    create_user(env, "power", role="advanced")
    env.post("/api/admin/grants", json={"referential_id": "countries",
                                        "user_id": _id(env, "power"), "level": "read"})
    mine = create(env).json()
    client = new_client(env, "power", USER_PASSWORD)
    assert client.get("/api/watchdogs").json() == []
    assert client.get(f"/api/watchdogs/{mine['id']}").status_code == 404
    assert client.delete(f"/api/watchdogs/{mine['id']}").status_code == 404


# --------------------------------------------------------------------------- one investigation

def test_an_investigation_with_nothing_to_report_stays_quiet(env, model):
    env.script = model(verdict())  # type: ignore[attr-defined]
    task = create(env).json()
    result = run(env, task["id"])
    assert result["status"] == "ok" and result["finding_detected"] is False
    assert result["notifications"] == [] and result["error"] is None


def test_a_finding_is_recorded_with_its_severity(env, model):
    model(verdict(finding_detected=True, severity="HIGH", summary="3 machines are not in the CMDB",
                  details_markdown="| host |\n| --- |\n| a |", affected_items_count=3))
    task = create(env).json()
    result = run(env, task["id"])
    assert result["status"] == "finding" and result["severity"] == "HIGH"
    assert result["affected_items_count"] == 3 and "| host |" in result["details_markdown"]


def test_an_answer_that_is_not_the_expected_json_is_an_error_not_an_all_clear(env, model):
    model("Everything looks fine to me!")
    task = create(env).json()
    result = run(env, task["id"])
    assert result["status"] == "error" and result["finding_detected"] is False
    assert "expected JSON" in result["error"]
    assert "Everything looks fine" in result["details_markdown"]  # the answer is kept


def test_a_verdict_wrapped_in_prose_is_still_read(env, model):
    model("Here is my report:\n```json\n" + verdict(finding_detected=True, severity="MEDIUM",
                                                    summary="one drift") + "\n```\nHope it helps.")
    task = create(env).json()
    result = run(env, task["id"])
    assert result["status"] == "finding" and result["summary"] == "one drift"


def test_the_investigation_only_sees_the_referentials_of_its_scope(env, model):
    provider = model(tool_call("query_rows", id="words", limit=5), verdict())
    task = create(env, referential_ids=["countries"]).json()
    result = run(env, task["id"])
    step = result["steps"][0]
    # 'words' is readable by the administrator in the interface, but not by this watchdog
    assert step["ok"] is False and "not found" in step["summary"]
    assert "alpha" not in json.dumps(provider.calls)


def test_the_investigation_reads_what_it_was_pointed_at(env, model):
    refresh(env, "countries")
    model(tool_call("query_rows", id="countries", limit=3), verdict(finding_detected=True, summary="found"))
    task = create(env).json()
    result = run(env, task["id"])
    assert result["steps"][0]["ok"] is True and result["status"] == "finding"


def test_a_watchdog_never_writes(env, model):
    configure(env, agent_enabled=True, watchdogs_enabled=True, allow_writes=True)
    provider = model(verdict())
    task = create(env).json()
    run(env, task["id"])
    offered = set(provider.tool_names)
    assert "query_rows" in offered
    assert not offered & {"create_referential", "delete_referential", "update_referential", "grant_access"}


def test_the_session_of_the_investigation_does_not_outlive_it(env, model):
    model(verdict())
    task = create(env).json()
    before = len(env.get("/api/admin/users").json())
    run(env, task["id"])
    # The temporary session is closed: the owner has no session left from the investigation
    from app.db import session_factory
    from app.models import AuthSession
    from sqlalchemy import select

    with session_factory()() as db:
        assert [s.ip for s in db.scalars(select(AuthSession)) if s.ip == "watchdog"] == []
    assert before == len(env.get("/api/admin/users").json())


def test_the_history_and_the_last_run_are_exposed(env, model):
    model(verdict(finding_detected=True, severity="CRITICAL", summary="breach"))
    task = create(env).json()
    run(env, task["id"], "admin")
    runs = env.get(f"/api/watchdogs/{task['id']}/runs").json()
    assert len(runs) == 1 and runs[0]["severity"] == "CRITICAL"
    again = env.get(f"/api/watchdogs/{task['id']}").json()
    assert again["last_run"]["summary"] == "breach" and "details_markdown" not in again["last_run"]
    recent = env.get("/api/watchdogs/runs").json()
    assert recent[0]["name"] == "Shadow IT"


def test_a_disabled_assistant_fails_the_investigation_without_pretending(env, model):
    model(verdict())
    task = create(env).json()
    configure(env, enabled=False, agent_enabled=True, watchdogs_enabled=True)
    result = run(env, task["id"])
    assert result["status"] == "error" and "disabled" in result["error"]
    assert result["finding_detected"] is False


# --------------------------------------------------------------------------- triggers

def test_a_synchronization_starts_the_watchdogs_waiting_for_it(env, model):
    model(verdict())
    task = create(env, trigger_type="on_sync", schedule=None, referential_ids=["countries"]).json()
    from app import watchdog

    started = []
    watchdog_submit = watchdog.submit
    try:
        watchdog.submit = lambda service, task_id, trigger, username=None: started.append((task_id, trigger))
        watchdog.on_sync(env.app_.state.service, "countries")
        watchdog.on_sync(env.app_.state.service, "words")
    finally:
        watchdog.submit = watchdog_submit
    assert started == [(task["id"], "on_sync:countries")]


def test_the_crontab_job_follows_the_watchdog(env, model):
    from app import watchdog

    task = create(env).json()
    service = env.app_.state.service
    assert service.scheduler.get_job(watchdog.job_id(task["id"])) is not None
    env.patch(f"/api/watchdogs/{task['id']}", json={"enabled": False})
    assert service.scheduler.get_job(watchdog.job_id(task["id"])) is None
    env.patch(f"/api/watchdogs/{task['id']}", json={"enabled": True})
    assert service.scheduler.get_job(watchdog.job_id(task["id"])) is not None
    env.delete(f"/api/watchdogs/{task['id']}")
    assert service.scheduler.get_job(watchdog.job_id(task["id"])) is None


def test_two_runs_of_the_same_watchdog_do_not_overlap(env, model):
    from app import watchdog

    model(verdict())
    task = create(env).json()
    with watchdog._lock:
        watchdog._running.add(task["id"])
    try:
        assert env.post(f"/api/watchdogs/{task['id']}/run").status_code == 409
        assert env.get(f"/api/watchdogs/{task['id']}").json()["running"] is True
    finally:
        with watchdog._lock:
            watchdog._running.discard(task["id"])
    assert env.post(f"/api/watchdogs/{task['id']}/run").status_code == 202


# --------------------------------------------------------------------------- notifications

def test_the_recipients_are_only_told_when_something_was_found(env, model, monkeypatch):
    sent = []
    import app.notify as notifier

    monkeypatch.setattr(notifier, "send_mail",
                        lambda smtp, to, subject, body, ca="": sent.append((to, subject, body)))
    task = create(env, notify_emails=["soc@example.com"]).json()

    model(verdict())
    assert run(env, task["id"])["notifications"] == []
    assert sent == []

    model(verdict(finding_detected=True, severity="HIGH", summary="3 orphan accounts", affected_items_count=3))
    result = run(env, task["id"])
    assert result["notifications"] == [{"target": "soc@example.com", "ok": True, "detail": "e-mail sent"}]
    to, subject, body = sent[0]
    assert to == ["soc@example.com"] and "HIGH" in subject and "3 orphan accounts" in subject
    assert "Items concerned: 3" in body


def test_a_finding_below_the_chosen_severity_stays_quiet(env, model, monkeypatch):
    sent = []
    import app.notify as notifier

    monkeypatch.setattr(notifier, "send_mail", lambda *a, **k: sent.append(a))
    task = create(env, notify_emails=["soc@example.com"], min_severity="HIGH").json()
    model(verdict(finding_detected=True, severity="MEDIUM", summary="a detail"))
    result = run(env, task["id"])
    assert result["status"] == "finding" and result["notifications"] == [] and sent == []


def test_a_notification_that_fails_is_recorded_not_hidden(env, model, monkeypatch):
    import app.notify as notifier

    def boom(*args, **kwargs):
        raise RuntimeError("no SMTP server configured")

    monkeypatch.setattr(notifier, "send_mail", boom)
    task = create(env, notify_emails=["soc@example.com"]).json()
    model(verdict(finding_detected=True, severity="CRITICAL", summary="breach"))
    result = run(env, task["id"])
    assert result["notifications"][0]["ok"] is False
    assert "no SMTP server" in result["notifications"][0]["detail"]


def test_an_invalid_recipient_is_refused_at_creation(env, model):
    assert create(env, notify_emails=["not an address"]).status_code == 422
    assert create(env, notify_channels=["unknown"]).status_code == 400


def _id(env, username: str) -> int:
    return next(u["id"] for u in env.get("/api/admin/users").json() if u["username"] == username)
