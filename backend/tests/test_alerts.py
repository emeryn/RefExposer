"""The alert feed, and the switch that turns scheduled watchdogs on.

The feed is built from the state of the referentials and the recorded watchdog runs, so these tests put the
application in the state that should raise an alert and check what comes out — including who sees it.
"""
import json

import pytest

from conftest import USER_PASSWORD, create_user, new_client, refresh
from test_assistant import Scripted, configure
from test_watchdog import model, verdict  # noqa: F401 (fixture)


def feed(client, **params) -> dict:
    r = client.get("/api/alerts", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def kinds(answer: dict) -> set[str]:
    return {a["kind"] for a in answer["alerts"]}


def _id(env, username: str) -> int:
    return next(u["id"] for u in env.get("/api/admin/users").json() if u["username"] == username)


def break_remote(env) -> None:
    """Make the next update of the 'remote' referential fail: its source is gone from the test server."""
    (env.www / "remote.csv.gz").unlink(missing_ok=True)
    refresh(env, "remote", force=True)


# --------------------------------------------------------------------------- the switch

def test_watchdogs_are_off_until_an_administrator_turns_them_on(env):
    configure(env, agent_enabled=True)  # agent mode on, watchdogs still off
    assert env.get("/api/assistant").json()["watchdogs"] is False
    assert env.get("/api/watchdogs").status_code == 403
    body = {"name": "W", "referential_ids": ["countries"], "trigger_type": "cron", "schedule": "0 7 * * *",
            "prompt": "Look."}
    assert env.post("/api/watchdogs", json=body).status_code == 403

    configure(env, agent_enabled=True, watchdogs_enabled=True)
    assert env.get("/api/assistant").json()["watchdogs"] is True
    assert env.get("/api/watchdogs").status_code == 200


def test_watchdogs_need_the_agent_mode(env):
    configure(env, agent_enabled=False, watchdogs_enabled=True)
    assert env.get("/api/assistant").json()["watchdogs"] is False
    assert env.get("/api/watchdogs").status_code == 403


def test_only_administrators_and_advanced_users_see_watchdogs(env):
    configure(env, agent_enabled=True, watchdogs_enabled=True)
    create_user(env, "reader")
    create_user(env, "power", role="advanced")
    for ref in ("countries",):
        env.post("/api/admin/grants", json={"referential_id": ref, "user_id": _id(env, "reader"), "level": "read"})
        env.post("/api/admin/grants", json={"referential_id": ref, "user_id": _id(env, "power"), "level": "read"})

    reader = new_client(env, "reader", USER_PASSWORD)
    assert reader.get("/api/assistant").json()["watchdogs"] is False
    assert reader.get("/api/watchdogs").status_code == 403
    assert reader.get("/api/watchdogs/templates").status_code == 403

    power = new_client(env, "power", USER_PASSWORD)
    assert power.get("/api/assistant").json()["watchdogs"] is True
    assert power.get("/api/watchdogs").status_code == 200


def test_turning_them_off_removes_the_schedules(env, model):
    from app import watchdog

    service = env.app_.state.service
    configure(env, agent_enabled=True, watchdogs_enabled=True)
    task = env.post("/api/watchdogs", json={"name": "W", "referential_ids": ["countries"],
                                            "trigger_type": "cron", "schedule": "0 7 * * *",
                                            "prompt": "Look."}).json()
    assert service.scheduler.get_job(watchdog.job_id(task["id"])) is not None
    configure(env, agent_enabled=True, watchdogs_enabled=False)
    assert service.scheduler.get_job(watchdog.job_id(task["id"])) is None
    # And a trigger that slips through is refused rather than run
    assert watchdog.execute(service, task["id"], "manual")["error"].startswith("scheduled watchdogs are disabled")


# --------------------------------------------------------------------------- the feed

def test_an_update_that_failed_shows_up(env):
    break_remote(env)
    answer = feed(env)
    alert = next(a for a in answer["alerts"] if a["referential_id"] == "remote")
    assert alert["kind"] == "failure" and alert["severity"] == "HIGH"
    assert alert["link"] == "/r/remote/history" and alert["summary"]
    assert answer["counts"]["failure"] >= 1


def test_the_detail_of_a_failed_update_carries_its_log(env):
    break_remote(env)
    alert = next(a for a in feed(env)["alerts"] if a["referential_id"] == "remote")
    detail = env.get(f"/api/alerts/{alert['id']}").json()
    assert detail["alert"]["id"] == alert["id"]
    assert detail["referential"]["id"] == "remote"
    assert detail["run"]["status"] in ("error", "rejected", "corrupted")
    assert detail["run"]["logs"]


def test_a_volume_that_moved_a_lot_is_reported_as_a_drift(env):
    refresh(env, "pdns")  # 3002 rows
    (env.data_dir / "pdns" / "raw" / "p.csv").write_text(
        "rrname,rrtype,rdata,count\n" + "".join(f"h{i}.example.com,A,10.0.0.{i % 250},{i}\n" for i in range(200)),
        encoding="utf-8")
    refresh(env, "pdns", force=True)
    alert = next(a for a in feed(env)["alerts"] if a["id"] == "ref:pdns:drift")
    assert alert["kind"] == "drift" and alert["severity"] == "MEDIUM"  # a drop is worth more than growth
    assert "lost" in alert["title"]
    detail = env.get(f"/api/alerts/{alert['id']}").json()
    assert detail["figures"]["row_count"] == 200 and detail["figures"]["previous_row_count"] == 3002


def test_a_small_referential_does_not_raise_a_drift(env):
    refresh(env, "countries")  # 5 rows: a ratio over so few rows means nothing
    assert "ref:countries:drift" not in {a["id"] for a in feed(env)["alerts"]}


def test_a_user_only_sees_the_referentials_it_may_read(env):
    break_remote(env)
    create_user(env, "reader")
    env.post("/api/admin/grants", json={"referential_id": "countries",
                                        "user_id": _id(env, "reader"), "level": "read"})
    client = new_client(env, "reader", USER_PASSWORD)
    assert "remote" not in {a["referential_id"] for a in feed(client)["alerts"]}
    # And the alert of a referential it cannot read is not readable by its identifier either
    assert client.get("/api/alerts/ref:remote:failure").status_code == 404


def test_the_feed_can_be_narrowed_to_a_kind(env):
    break_remote(env)
    assert kinds(feed(env, kind="failure")) <= {"failure"}
    assert feed(env, kind="watchdog")["alerts"] == []
    assert env.get("/api/alerts", params={"kind": "nope"}).status_code == 400


def test_a_watchdog_finding_appears_with_its_report(env, model):
    configure(env, agent_enabled=True, watchdogs_enabled=True)
    model(verdict(finding_detected=True, severity="CRITICAL", summary="3 orphan accounts",
                  details_markdown="| account |\n| --- |\n| jdoe |", affected_items_count=3))
    task = env.post("/api/watchdogs", json={"name": "IAM audit", "referential_ids": ["countries"],
                                            "trigger_type": "cron", "schedule": "0 7 * * *",
                                            "prompt": "Correlate."}).json()
    from app import watchdog

    watchdog.execute(env.app_.state.service, task["id"], "manual")

    alert = next(a for a in feed(env)["alerts"] if a["kind"] == "watchdog")
    assert alert["severity"] == "CRITICAL" and alert["has_report"] is True
    assert alert["watchdog_name"] == "IAM audit" and "3 orphan accounts" in alert["summary"]
    detail = env.get(f"/api/alerts/{alert['id']}").json()
    assert "| jdoe |" in detail["run"]["details_markdown"]
    assert detail["watchdog"]["referential_ids"] == ["countries"]
    assert detail["run"]["affected_items_count"] == 3


def test_an_investigation_that_found_nothing_raises_no_alert(env, model):
    configure(env, agent_enabled=True, watchdogs_enabled=True)
    model(verdict())
    task = env.post("/api/watchdogs", json={"name": "Quiet", "referential_ids": ["countries"],
                                            "trigger_type": "cron", "schedule": "0 7 * * *",
                                            "prompt": "Look."}).json()
    from app import watchdog

    watchdog.execute(env.app_.state.service, task["id"], "manual")
    assert [a for a in feed(env)["alerts"] if a["kind"] == "watchdog"] == []


def test_an_investigation_that_failed_is_an_alert_of_its_own(env, model):
    configure(env, agent_enabled=True, watchdogs_enabled=True)
    model("I am not answering in JSON.")
    task = env.post("/api/watchdogs", json={"name": "Broken", "referential_ids": ["countries"],
                                            "trigger_type": "cron", "schedule": "0 7 * * *",
                                            "prompt": "Look."}).json()
    from app import watchdog

    watchdog.execute(env.app_.state.service, task["id"], "manual")
    alert = next(a for a in feed(env)["alerts"] if a["kind"] == "watchdog")
    assert alert["severity"] == "MEDIUM" and "failed" in alert["title"]
    assert alert["has_report"] is False


def test_the_watchdog_of_someone_else_is_not_in_your_feed(env, model):
    configure(env, agent_enabled=True, watchdogs_enabled=True)
    create_user(env, "power", role="advanced")
    env.post("/api/admin/grants", json={"referential_id": "countries",
                                        "user_id": _id(env, "power"), "level": "read"})
    model(verdict(finding_detected=True, severity="HIGH", summary="mine"))
    task = env.post("/api/watchdogs", json={"name": "Admin only", "referential_ids": ["countries"],
                                            "trigger_type": "cron", "schedule": "0 7 * * *",
                                            "prompt": "Look."}).json()
    from app import watchdog

    watchdog.execute(env.app_.state.service, task["id"], "manual")
    assert [a for a in feed(env)["alerts"] if a["kind"] == "watchdog"]  # the owner sees it
    power = new_client(env, "power", USER_PASSWORD)
    assert [a for a in feed(power)["alerts"] if a["kind"] == "watchdog"] == []


def test_a_reader_never_gets_watchdog_alerts(env, model):
    configure(env, agent_enabled=True, watchdogs_enabled=True)
    create_user(env, "reader")
    env.post("/api/admin/grants", json={"referential_id": "countries",
                                        "user_id": _id(env, "reader"), "level": "read"})
    model(verdict(finding_detected=True, severity="HIGH", summary="mine"))
    task = env.post("/api/watchdogs", json={"name": "Admin only", "referential_ids": ["countries"],
                                            "trigger_type": "cron", "schedule": "0 7 * * *",
                                            "prompt": "Look."}).json()
    from app import watchdog

    watchdog.execute(env.app_.state.service, task["id"], "manual")
    reader = new_client(env, "reader", USER_PASSWORD)
    answer = feed(reader)
    assert answer["counts"]["watchdog"] == 0
    assert reader.get(f"/api/alerts/watchdog:{json.loads('1')}").status_code == 404


def test_an_alert_goes_away_once_the_referential_is_fixed(env):
    import gzip

    from conftest import CSV

    break_remote(env)
    assert "ref:remote:failure" in {a["id"] for a in feed(env)["alerts"]}
    # The feed is built from the current state: the source coming back is enough, nothing to acknowledge
    with gzip.open(env.www / "remote.csv.gz", "wt", encoding="utf-8") as f:
        f.write(CSV)
    refresh(env, "remote", force=True)
    assert "ref:remote:failure" not in {a["id"] for a in feed(env)["alerts"]}


def test_the_feed_is_newest_first(env):
    break_remote(env)
    refresh(env, "pdns")
    dates = [a["at"] for a in feed(env)["alerts"] if a["at"]]
    assert dates == sorted(dates, reverse=True)


# --------------------------------------------------------------------------- what counts as an alert

def settings(env, **changes):
    r = env.put("/api/admin/settings/alerts", json=changes)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_kind_an_administrator_hid_is_out_of_the_feed(env):
    break_remote(env)
    assert "failure" in kinds(feed(env))
    settings(env, hidden_kinds=["failure"])
    answer = feed(env)
    assert "failure" not in kinds(answer)
    assert "failure" not in answer["kinds"] and "stale" in answer["kinds"]
    # And the alert itself is no longer readable: what is not reported does not exist
    assert env.get("/api/alerts/ref:remote:failure").status_code == 404
    settings(env, hidden_kinds=[])
    assert "failure" in kinds(feed(env))


def test_an_unknown_kind_is_refused(env):
    assert env.put("/api/admin/settings/alerts", json={"hidden_kinds": ["nope"]}).status_code == 422


def test_nothing_below_the_chosen_severity_is_reported(env):
    break_remote(env)      # HIGH
    refresh(env, "pdns")
    settings(env, min_severity="CRITICAL")
    assert feed(env)["alerts"] == []
    settings(env, min_severity="HIGH")
    assert kinds(feed(env)) == {"failure"}


def test_the_drift_threshold_is_configurable(env):
    refresh(env, "pdns")  # 3002 rows
    (env.data_dir / "pdns" / "raw" / "p.csv").write_text(
        "rrname,rrtype,rdata,count\n" + "".join(f"h{i}.example.com,A,10.0.0.{i % 250},{i}\n" for i in range(2800)),
        encoding="utf-8")
    refresh(env, "pdns", force=True)  # about 7% lost: under the default threshold
    assert "ref:pdns:drift" not in {a["id"] for a in feed(env)["alerts"]}
    settings(env, drift_ratio=0.05)
    assert "ref:pdns:drift" in {a["id"] for a in feed(env)["alerts"]}
    settings(env, drift_ratio=0.05, drift_min_rows=1_000_000)  # too small a referential to mean anything
    assert "ref:pdns:drift" not in {a["id"] for a in feed(env)["alerts"]}


# --------------------------------------------------------------------------- read and unread

def test_an_alert_can_be_marked_read(env):
    break_remote(env)
    answer = feed(env)
    alert = next(a for a in answer["alerts"] if a["id"] == "ref:remote:failure")
    assert alert["read"] is False and answer["counts"]["unread"] == answer["counts"]["total"]

    marked = env.post("/api/alerts/read", json={"ids": ["ref:remote:failure"]})
    assert marked.status_code == 200, marked.text
    assert marked.json()["changed"] == 1
    again = feed(env)
    assert next(a for a in again["alerts"] if a["id"] == "ref:remote:failure")["read"] is True
    assert again["counts"]["unread"] == again["counts"]["total"] - 1
    # The detail says so too, and marking it read again changes nothing
    assert env.get("/api/alerts/ref:remote:failure").json()["alert"]["read"] is True
    assert env.post("/api/alerts/read", json={"ids": ["ref:remote:failure"]}).json()["changed"] == 0

    assert env.post("/api/alerts/read", json={"ids": ["ref:remote:failure"], "read": False}).json()["changed"] == 1
    assert next(a for a in feed(env)["alerts"] if a["id"] == "ref:remote:failure")["read"] is False


def test_everything_can_be_marked_read_at_once(env):
    break_remote(env)
    refresh(env, "pdns")
    answer = env.post("/api/alerts/read", json={})
    assert answer.status_code == 200, answer.text
    assert answer.json()["unread"] == 0
    assert all(a["read"] for a in feed(env)["alerts"])


def test_an_alert_comes_back_unread_when_it_happens_again(env):
    """A marker remembers the date of what was acknowledged: the same referential failing again is news."""
    break_remote(env)
    env.post("/api/alerts/read", json={})
    assert feed(env)["counts"]["unread"] == 0
    refresh(env, "remote", force=True)  # it fails again, later
    assert next(a for a in feed(env)["alerts"] if a["id"] == "ref:remote:failure")["read"] is False


def test_read_belongs_to_the_account_that_marked_it(env):
    break_remote(env)
    create_user(env, "other")
    env.post("/api/admin/grants", json={"referential_id": "remote",
                                        "user_id": _id(env, "other"), "level": "read"})
    env.post("/api/alerts/read", json={})
    client = new_client(env, "other", USER_PASSWORD)
    assert next(a for a in feed(client)["alerts"] if a["id"] == "ref:remote:failure")["read"] is False


def test_an_alert_this_account_cannot_see_cannot_be_marked(env):
    break_remote(env)
    create_user(env, "reader")
    env.post("/api/admin/grants", json={"referential_id": "countries",
                                        "user_id": _id(env, "reader"), "level": "read"})
    client = new_client(env, "reader", USER_PASSWORD)
    assert client.post("/api/alerts/read", json={"ids": ["ref:remote:failure"]}).json()["changed"] == 0


# --------------------------------------------------------------------------- deleted from a feed

def test_an_alert_can_be_deleted_from_the_feed_and_put_back(env):
    break_remote(env)
    assert any(a["id"] == "ref:remote:failure" for a in feed(env)["alerts"])

    gone = env.post("/api/alerts/dismiss", json={"ids": ["ref:remote:failure"]})
    assert gone.status_code == 200, gone.text
    assert gone.json()["changed"] == 1 and gone.json()["deleted"] == 1
    answer = feed(env)
    assert not any(a["id"] == "ref:remote:failure" for a in answer["alerts"])
    assert answer["counts"]["deleted"] == 1
    # Out of the list, but not out of reach: it is listed on its own and its detail still opens
    deleted = feed(env, dismissed=True)["alerts"]
    assert [a["id"] for a in deleted] == ["ref:remote:failure"]
    assert deleted[0]["dismissed"] is True and deleted[0]["read"] is True
    assert env.get("/api/alerts/ref:remote:failure").status_code == 200

    back = env.post("/api/alerts/dismiss", json={"ids": ["ref:remote:failure"], "dismissed": False})
    assert back.json()["changed"] == 1 and back.json()["deleted"] == 0
    assert any(a["id"] == "ref:remote:failure" for a in feed(env)["alerts"])


def test_deleting_one_is_also_a_delete_on_its_own_route(env):
    break_remote(env)
    assert env.delete("/api/alerts/ref:remote:failure").status_code == 200
    assert not any(a["id"] == "ref:remote:failure" for a in feed(env)["alerts"])
    # Already gone, and an alert this account cannot see: both unknown as far as this account is concerned
    assert env.delete("/api/alerts/ref:remote:failure").status_code == 404
    assert env.delete("/api/alerts/ref:nope:failure").status_code == 404


def test_a_deleted_alert_is_out_of_the_counts(env):
    break_remote(env)
    before = feed(env)["counts"]
    env.post("/api/alerts/dismiss", json={"ids": ["ref:remote:failure"]})
    after = feed(env)["counts"]
    assert after["total"] == before["total"] - 1
    assert after["failure"] == before["failure"] - 1
    assert after["unread"] == before["unread"] - 1


def test_everything_shown_can_be_deleted_at_once_and_restored_at_once(env):
    break_remote(env)
    refresh(env, "pdns")
    total = feed(env)["counts"]["total"]
    assert total > 0
    assert env.post("/api/alerts/dismiss", json={}).json()["changed"] == total
    assert feed(env)["alerts"] == [] and feed(env)["counts"]["deleted"] == total
    assert env.post("/api/alerts/dismiss", json={"dismissed": False}).json()["changed"] == total
    assert feed(env)["counts"]["total"] == total


def test_a_deleted_alert_comes_back_when_it_happens_again(env):
    """Deleting remembers the date of what was dismissed, like marking it read does."""
    break_remote(env)
    env.post("/api/alerts/dismiss", json={})
    assert feed(env)["alerts"] == []
    refresh(env, "remote", force=True)  # it fails again, later
    alert = next(a for a in feed(env)["alerts"] if a["id"] == "ref:remote:failure")
    assert alert["read"] is False and alert["dismissed"] is False


def test_deleting_belongs_to_the_account_that_did_it(env):
    break_remote(env)
    create_user(env, "other")
    env.post("/api/admin/grants", json={"referential_id": "remote",
                                        "user_id": _id(env, "other"), "level": "read"})
    env.post("/api/alerts/dismiss", json={})
    client = new_client(env, "other", USER_PASSWORD)
    assert any(a["id"] == "ref:remote:failure" for a in feed(client)["alerts"])


def test_an_alert_this_account_cannot_see_cannot_be_deleted(env):
    break_remote(env)
    create_user(env, "reader")
    env.post("/api/admin/grants", json={"referential_id": "countries",
                                        "user_id": _id(env, "reader"), "level": "read"})
    client = new_client(env, "reader", USER_PASSWORD)
    assert client.post("/api/alerts/dismiss", json={"ids": ["ref:remote:failure"]}).json()["changed"] == 0


def test_marking_a_deleted_alert_unread_puts_it_back(env):
    break_remote(env)
    env.post("/api/alerts/dismiss", json={"ids": ["ref:remote:failure"]})
    assert env.post("/api/alerts/read", json={"ids": ["ref:remote:failure"], "read": False}).json()["changed"] == 1
    alert = next(a for a in feed(env)["alerts"] if a["id"] == "ref:remote:failure")
    assert alert["read"] is False and alert["dismissed"] is False
