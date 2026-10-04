"""System tasks: configuration, manual runs, history, and each family (maintenance, integrity, security, backup)."""

import time
from datetime import datetime, timedelta, timezone

from conftest import ADMIN_PASSWORD, create_user, new_client, refresh


def run(env, task_id, timeout=30):
    """Run a task now and return its finished run."""
    before = env.get("/api/admin/tasks").json()
    previous = next(t for t in before if t["id"] == task_id)["last_run"]
    r = env.post(f"/api/admin/tasks/{task_id}/run")
    assert r.status_code == 200, r.text
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = next(t for t in env.get("/api/admin/tasks").json() if t["id"] == task_id)
        if not t["running"] and t["last_run"] and t["last_run"] != previous:
            return t["last_run"]
        time.sleep(0.05)
    raise TimeoutError(task_id)


def db_session():
    from app.db import session_factory

    return session_factory()()


def test_configuration_and_history(env):
    tasks = {t["id"]: t for t in env.get("/api/admin/tasks").json()}
    assert {"purge_sessions", "audit_retention", "disk_cleanup", "integrity_check", "stale_report", "source_check",
            "certificate_check", "inactive_accounts", "expired_tokens", "pending_requests", "ldap_resync", "config_backup"} <= set(tasks)
    assert tasks["audit_retention"]["enabled"] is False and tasks["config_backup"]["schedule"] == "0 2 * * *"
    assert tasks["ldap_resync"]["available"] is False  # LDAP not configured

    assert env.put("/api/admin/tasks/config_backup", json={"schedule": "not a cron"}).status_code == 422
    assert env.put("/api/admin/tasks/config_backup", json={"params": {"keep": 0}}).status_code == 422
    assert env.put("/api/admin/tasks/config_backup", json={"params": {"nope": 1}}).status_code == 422
    t = env.put("/api/admin/tasks/config_backup", json={"schedule": "0 1 * * 0", "enabled": False, "params": {"keep": 3}}).json()
    assert t["schedule"] == "0 1 * * 0" and t["enabled"] is False and t["params"]["keep"] == 3

    r = run(env, "purge_sessions")
    assert r["status"] == "success" and r["trigger"] == "manual" and r["user"] == "admin"
    assert env.get("/api/admin/tasks/purge_sessions/runs").json()[0]["id"] == r["id"]
    audit = env.get("/api/admin/audit", params={"action": "task.run"}).json()
    entries = audit["rows"]
    assert any(e["target"] == "task:purge_sessions" for e in entries)
    # Reserved to administrators
    create_user(env, "tom")
    assert new_client(env, "tom", "Us3r-Passw0rd!").get("/api/admin/tasks").status_code == 403


def test_maintenance_and_integrity(env):
    # Disk cleanup: folders of deleted referentials reported, then deleted
    ghost = env.data_dir / "ghost"
    (ghost / "raw").mkdir(parents=True)
    (ghost / "meta.json").write_text("{}", encoding="utf-8")
    r = run(env, "disk_cleanup")
    assert r["status"] == "warning" and "ghost" in r["message"] and ghost.exists()
    env.put("/api/admin/tasks/disk_cleanup", json={"params": {"orphans": "delete"}})
    assert run(env, "disk_cleanup")["status"] == "success" and not ghost.exists()

    # Integrity: consistent, then a damaged published file is detected
    refresh(env, "countries")
    r = run(env, "integrity_check")
    assert r["status"] == "success" and r["details"]["checked"] >= 1
    current = env.data_dir / "countries" / "current.parquet"
    current.write_bytes(current.read_bytes() + b"garbage")
    r = run(env, "integrity_check")
    assert r["status"] == "error" and "countries" in r["message"]

    assert run(env, "stale_report")["status"] in ("success", "warning")
    r = run(env, "source_check")
    assert r["details"]["checked"] >= 1 and not any(f["id"] == "remote" for f in r["details"].get("failures", []))
    assert run(env, "certificate_check")["message"].startswith("nothing to check")


def test_security_policy(env):
    from app.models import ApiToken, User

    create_user(env, "old")
    create_user(env, "recent")
    tok = new_client(env, "recent", "Us3r-Passw0rd!").post("/api/auth/tokens", json={"name": "x", "expires_in_days": 1}).json()
    long_ago = datetime.now(timezone.utc) - timedelta(days=400)
    with db_session() as db:
        old = db.query(User).filter_by(username="old").one()
        old.last_login_at = long_ago
        old.created_at = long_ago
        db.query(ApiToken).filter_by(id=tok["id"]).one().expires_at = long_ago
        db.add(User(username="newcomer", password_hash="!external", auth_source="oidc", status="pending", role="user"))
        db.commit()

    r = run(env, "inactive_accounts")
    assert r["status"] == "warning" and r["details"]["accounts"] == ["old"]
    env.put("/api/admin/tasks/inactive_accounts", json={"params": {"mode": "disable"}})
    assert run(env, "inactive_accounts")["details"]["accounts"] == ["old"]
    with db_session() as db:
        assert db.query(User).filter_by(username="old").one().is_active is False
        assert db.query(User).filter_by(username="admin").one().is_active is True

    assert run(env, "expired_tokens")["details"]["removed"] == 1
    r = run(env, "pending_requests")
    assert r["status"] == "warning" and r["details"]["pending"] == ["newcomer"]


def test_backup_and_restore(env):
    env.put("/api/admin/tasks/config_backup", json={"params": {"keep": 2}})
    group = env.post("/api/admin/groups", json={"name": "Before"}).json()
    r = run(env, "config_backup")
    assert r["status"] == "success" and "encrypted" in r["message"]
    backups = env.get("/api/admin/backups").json()
    name = backups[0]["name"]
    assert backups[0]["encrypted"] and name.endswith(".json.gz.enc")
    raw = env.get(f"/api/admin/backups/{name}").content
    assert b"Before" not in raw  # encrypted

    # Changes after the backup are undone by the restore
    env.post("/api/admin/groups", json={"name": "After"})
    env.delete(f"/api/admin/groups/{group['id']}")
    assert env.post(f"/api/admin/backups/{name}/restore", json={"confirm": "yes"}).status_code == 400
    r = env.post(f"/api/admin/backups/{name}/restore", json={"confirm": "RESTORE"})
    assert r.status_code == 200, r.text
    assert r.json()["restored"]["groups"] == 1 and r.json()["signed_out"]
    admin = new_client(env, "admin", ADMIN_PASSWORD)
    assert [g["name"] for g in admin.get("/api/admin/groups").json()] == ["Before"]
    assert admin.post("/api/admin/groups", json={"name": "Next"}).status_code == 201  # ids keep counting after the restore

    # An uploaded backup is restored too; a foreign file is refused
    files = {"file": (name, raw, "application/octet-stream")}
    assert admin.post("/api/admin/backups/restore-upload", files=files, data={"confirm": "RESTORE"}).status_code == 200
    admin = new_client(env, "admin", ADMIN_PASSWORD)
    bad = {"file": ("x.enc", b"not a backup", "application/octet-stream")}
    assert admin.post("/api/admin/backups/restore-upload", files=bad, data={"confirm": "RESTORE"}).status_code == 400

    # Retention
    for _ in range(3):
        time.sleep(1.1)  # names have a one-second resolution
        run(admin, "config_backup")
    assert len(admin.get("/api/admin/backups").json()) == 2
