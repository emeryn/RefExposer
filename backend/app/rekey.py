"""Re-encrypting an instance with another master secret: what makes a restore onto a *second* instance work.

Everything this application keeps encrypted at rest hangs off one master secret (`REFEX_SECRET_KEY`, or
`data/.secret_key` when the variable is empty — see crypto.py). A full backup restored onto an instance whose
key is not the one the archive was made with therefore lands rows and files nobody can read: the database is
restored, and the secrets inside it are noise.

Given the old key, this module re-encrypts, in place, everything that was sealed with it:

- the secret fields of the settings (LDAP bind password, OIDC client secret, SMTP password, proxy password,
  syslog client key, assistant API key, the S3 key of the backups);
- the TOTP secrets of the accounts;
- the secrets of the source manager, and those of the LLM profiles;
- the rows of the confidential internal referentials — including their `row_key`, a keyed fingerprint that has
  to be computed again from the row itself, or lookups by key would no longer find anything;
- the column profile kept in `meta.json` of a confidential referential;
- the Parquet files of the confidential referentials (Parquet Modular Encryption): the published version, the
  previous one, the sorted copies and the kept versions are each rewritten with the new key.

What it deliberately leaves alone: the **archives** already on the server (configuration backups and full
backups) are sealed with the key of the instance that wrote them, and re-encrypting them would be rewriting
history; keep the old key as long as you want to restore them. Pseudonyms of masked columns (`pii.py`) are
derived from the master secret too, so they change with it — they are computed when the data is served, so
nothing has to be rewritten, but a pseudonym noted before a re-key no longer matches.

Re-keying is **idempotent**: a value that already reads with the new key is left as it is, so an interrupted
re-key can simply be run again.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import crypto
from .config import ReferentialConfig
from .crypto import PREFIX
from .models import AppSetting, InternalRecord, LlmProfile, SourceSecret, User
from .settings import Settings
from .storage import RefPaths, read_meta, write_meta

log = logging.getLogger(__name__)


class RekeyError(ValueError):
    """The key given cannot read what it was given for."""


@dataclass
class Rekeyed:
    """What a re-key changed. `failed` names what could not be read with the key given."""

    settings: int = 0
    totp: int = 0
    source_secrets: int = 0
    llm_profiles: int = 0
    records: int = 0
    profiles: int = 0
    parquet_files: int = 0
    skipped: int = 0  # already readable with the new key (a re-key run again)
    failed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (self.settings + self.totp + self.source_secrets + self.llm_profiles + self.records
                + self.profiles + self.parquet_files)

    def as_dict(self) -> dict[str, Any]:
        return {"settings": self.settings, "totp": self.totp, "source_secrets": self.source_secrets,
                "llm_profiles": self.llm_profiles, "records": self.records, "profiles": self.profiles,
                "parquet_files": self.parquet_files, "skipped": self.skipped, "total": self.total,
                "failed": self.failed[:50], "failed_count": len(self.failed), "notes": self.notes}


class _Old:
    """The keys of the other instance, derived once from its master secret."""

    def __init__(self, material: bytes):
        self.material = material
        self.settings = crypto.settings_key(material)
        self.records = crypto.record_key(material)
        self.blind = crypto.blind_key(material)
        self.fingerprint = crypto.fingerprint(material)


def differs(material: bytes) -> bool:
    """True when `material` is not the master secret of this instance."""
    return crypto.fingerprint(material) != crypto.key_fingerprint()


def registry(db: Session, settings: Settings) -> dict[str, ReferentialConfig]:
    """Every definition, as the service loads them (YAML files and definitions of the database)."""
    from .config import load_db_registry, load_registry

    refs, _ = load_registry(settings.config_dir)
    try:
        db_refs, _ = load_db_registry(db)
    except Exception:  # noqa: BLE001 - a database without the table is simply no definitions
        db_refs = {}
    return {**refs, **db_refs}


# --------------------------------------------------------------------------- the database

def _resettle(old_key: bytes, value: str, context: str | None = None) -> str:
    """Read a sealed value with the old key and seal it again with ours."""
    plain = crypto.open_with(old_key, value, context)
    return crypto.encrypt(plain) if context is None else crypto.encrypt_data(plain, context)


def _sealed(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def _try(done: Rekeyed, what: str, action: Callable[[], None]) -> bool:
    """Run one re-encryption, counting a value the key cannot read instead of stopping the whole restore."""
    try:
        action()
        return True
    except RekeyError:
        raise
    except Exception as e:  # noqa: BLE001 - an InvalidTag, or a value that is not what it claims to be
        log.warning("Cannot re-encrypt %s: %s", what, type(e).__name__)
        done.failed.append(what)
        return False


def _settings_rows(db: Session, old: _Old, done: Rekeyed) -> None:
    from .appsettings import SECRET_FIELDS

    for row in db.scalars(select(AppSetting)):
        data = dict(row.value or {})
        changed = False
        for f in SECRET_FIELDS.get(row.key, ()):
            if not _sealed(data.get(f)):
                continue
            if _already(data[f]):
                done.skipped += 1
                continue

            def rewrite(f: str = f, data: dict[str, Any] = data) -> None:
                data[f] = _resettle(old.settings, data[f])

            if _try(done, f"settings {row.key}.{f}", rewrite):
                done.settings += 1
                changed = True
        if changed:
            row.value = data
    db.flush()


def _already(value: str) -> bool:
    """A value that already reads with our key: an interrupted re-key run again leaves it alone."""
    try:
        crypto.decrypt(value)
        return True
    except Exception:  # noqa: BLE001
        return False


def _already_data(value: str, context: str) -> bool:
    try:
        crypto.decrypt_data(value, context)
        return True
    except Exception:  # noqa: BLE001
        return False


def _totp(db: Session, old: _Old, done: Rekeyed) -> None:
    for user in db.scalars(select(User).where(User.totp_secret.is_not(None))):
        if not _sealed(user.totp_secret):
            continue
        if _already(user.totp_secret):
            done.skipped += 1
            continue

        def rewrite(user: User = user) -> None:
            user.totp_secret = _resettle(old.settings, user.totp_secret or "")

        if _try(done, f"TOTP secret of '{user.username}'", rewrite):
            done.totp += 1
    db.flush()


def _source_secrets(db: Session, old: _Old, done: Rekeyed) -> None:
    for secret in db.scalars(select(SourceSecret)):
        context = f"source-secret:{secret.name}"
        if not _sealed(secret.value):
            continue
        if _already_data(secret.value, context):
            done.skipped += 1
            continue

        def rewrite(secret: SourceSecret = secret, context: str = context) -> None:
            secret.value = _resettle(old.records, secret.value, context)

        if _try(done, f"secret '{secret.name}'", rewrite):
            done.source_secrets += 1
    db.flush()


def _llm_profiles(db: Session, old: _Old, done: Rekeyed) -> None:
    for profile in db.scalars(select(LlmProfile).where(LlmProfile.secrets_enc.is_not(None))):
        context = f"llm-profile:{profile.id}"
        if not _sealed(profile.secrets_enc):
            continue
        if _already_data(profile.secrets_enc or "", context):
            done.skipped += 1
            continue

        def rewrite(profile: LlmProfile = profile, context: str = context) -> None:
            profile.secrets_enc = _resettle(old.records, profile.secrets_enc or "", context)

        if _try(done, f"secrets of the LLM profile '{profile.name}'", rewrite):
            done.llm_profiles += 1
    db.flush()


def _records(db: Session, old: _Old, refs: dict[str, ReferentialConfig], done: Rekeyed) -> None:
    """Rows of the confidential internal referentials: the blob **and** the keyed fingerprint of the key.

    The fingerprint cannot be converted: it is recomputed from the key column of the decrypted row. A row of a
    referential whose definition is gone is still re-encrypted, with its fingerprint left as it is — the rows
    are readable, and the definition coming back puts the lookups right (nothing else uses that column).
    """
    from .records_codec import ENC, is_encrypted

    unknown: set[str] = set()
    for row in db.scalars(select(InternalRecord)):
        data = row.data if isinstance(row.data, dict) else {}
        if not is_encrypted(data):
            continue
        ref_id = row.referential_id
        if _already_data(data[ENC], ref_id):
            done.skipped += 1
            continue

        def rewrite(row: InternalRecord = row, data: dict[str, Any] = data, ref_id: str = ref_id) -> None:
            plain = crypto.open_with(old.records, data[ENC], ref_id)
            row.data = {ENC: crypto.encrypt_data(plain, ref_id)}
            ref = refs.get(ref_id)
            if ref is None or not ref.key:
                unknown.add(ref_id)
                return
            key = str(json.loads(plain).get(ref.key))
            if row.row_key == crypto.blind_with(old.blind, ref_id, key):
                row.row_key = crypto.blind_index(ref_id, key)

        if _try(done, f"row of '{ref_id}'", rewrite):
            done.records += 1
    if unknown:
        done.notes.append("rows of " + ", ".join(sorted(unknown)) + ": no definition carries them any more, so "
                          "their key fingerprint was left as it is")
    db.flush()


# --------------------------------------------------------------------------- the files of data/

def _profile_enc(settings: Settings, ref_id: str, old: _Old, done: Rekeyed) -> None:
    paths = RefPaths(settings.data_dir, ref_id)
    meta = read_meta(paths)
    value = meta.get("profile_enc")
    if not _sealed(value):
        return
    if _already_data(value, ref_id):
        done.skipped += 1
        return

    def rewrite() -> None:
        meta["profile_enc"] = _resettle(old.records, value, ref_id)
        write_meta(paths, meta)

    if _try(done, f"column profile of '{ref_id}'", rewrite):
        done.profiles += 1


def encrypted_files(settings: Settings, ref: ReferentialConfig) -> list[Path]:
    """Every Parquet file of a referential a confidential version wrote: the published version, the previous
    one, the sorted copies, and the versions kept for time travel."""
    from .snapshots import folder as snapshot_folder

    paths = RefPaths(settings.data_dir, ref.id)
    files = [paths.current, paths.previous, *paths.index_files()]
    kept = snapshot_folder(settings.data_dir, ref.id)
    if kept.is_dir():
        files.extend(sorted(p for p in kept.iterdir() if p.is_file() and p.suffix == ".parquet"))
    return [p for p in files if p.is_file()]


def _reencrypt_parquet(settings: Settings, ref: ReferentialConfig, path: Path, old: _Old, done: Rekeyed) -> None:
    """Rewrite one Parquet file with our key. The old and the new key are registered under different names on
    the same connection, which is what lets a single COPY read with one and write with the other."""
    import duckdb

    from .ingest import _connect
    from .sqlbuild import lit, parquet_reader

    paths = RefPaths(settings.data_dir, ref.id)
    new_key = crypto.parquet_key(ref.id)
    old_key = crypto.parquet_key_with(old.material, ref.id, "_old")
    paths.work.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".rekey")
    con = _connect(settings, paths, "parquet", key=new_key)
    try:
        con.execute(f"PRAGMA add_parquet_key({lit(old_key[0])}, {lit(old_key[1])})")
        readable = f"SELECT * FROM {parquet_reader(str(path.resolve()), old_key[0])}"
        try:
            con.execute(f"{readable} LIMIT 0").fetchall()
        except duckdb.Error:
            # Not readable with the old key: either it is already ours (a re-key run again), or it is a file
            # this key cannot open at all
            try:
                con.execute(f"SELECT * FROM {parquet_reader(str(path.resolve()), new_key[0])} LIMIT 0").fetchall()
                done.skipped += 1
            except duckdb.Error:
                done.failed.append(f"{ref.id}/{path.name}")
                log.warning("[%s] %s reads with neither the old key nor ours", ref.id, path.name)
            return
        con.execute(f"COPY ({readable}) TO {lit(str(tmp))} (FORMAT parquet, COMPRESSION zstd, "
                    f"ROW_GROUP_SIZE {ref.storage.row_group_size}, "
                    f"ENCRYPTION_CONFIG {{footer_key: {lit(new_key[0])}}})")
        os.replace(tmp, path)
        done.parquet_files += 1
    except duckdb.Error as e:
        tmp.unlink(missing_ok=True)
        done.failed.append(f"{ref.id}/{path.name}")
        log.error("[%s] cannot re-encrypt %s: %s", ref.id, path.name, e)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        con.close()


# --------------------------------------------------------------------------- the whole instance

def rekey(db: Session, settings: Settings, material: bytes, refs: dict[str, ReferentialConfig] | None = None,
          log_line: Callable[[str], None] = lambda m: None) -> Rekeyed:
    """Re-encrypt with our key everything sealed with `material`. Returns what was changed.

    Raises RekeyError when nothing at all could be read: that is a wrong key, and saying so is better than
    leaving an instance whose secrets are all unreadable.
    """
    old = _Old(material)
    done = Rekeyed()
    if not differs(material):
        log_line("The backup was made with this instance's key: nothing to re-encrypt")
        return done
    if refs is None:
        refs = registry(db, settings)
    log_line(f"Re-encrypting with this instance's key (backup key {old.fingerprint}, "
             f"this instance {crypto.key_fingerprint()})")

    _settings_rows(db, old, done)
    _totp(db, old, done)
    _source_secrets(db, old, done)
    _llm_profiles(db, old, done)
    _records(db, old, refs, done)
    db.commit()

    confidential = sorted((r for r in refs.values() if r.confidential), key=lambda r: r.id)
    for ref in confidential:
        _profile_enc(settings, ref.id, old, done)
        for path in encrypted_files(settings, ref):
            _reencrypt_parquet(settings, ref, path, old, done)
    if confidential:
        log_line(f"{len(confidential)} confidential referential(s): {done.parquet_files} file(s) rewritten with "
                 "the new key")

    if done.failed and not done.total:
        raise RekeyError(
            f"this key does not read the backup: none of its {len(done.failed)} encrypted value(s) could be "
            "decrypted with it. Give the REFEX_SECRET_KEY of the instance the backup comes from, or the "
            "content of its data/.secret_key file."
        )
    archives = _archives(settings)
    if archives:
        done.notes.append(f"{archives} archive(s) already on the server (configuration and full backups) were "
                          "written with the previous key: keep it as long as you may want to restore them")
    log_line(f"Re-encrypted: {done.total} item(s)" + (f", {len(done.failed)} failure(s)" if done.failed else "")
             + (f", {done.skipped} already with the new key" if done.skipped else ""))
    for note in done.notes:
        log_line(note)
    return done


def _archives(settings: Settings) -> int:
    from .fullbackup import full_dir

    folders = [settings.data_dir / ".backups", full_dir(settings.data_dir)]
    return sum(1 for folder in folders if folder.is_dir() for p in folder.iterdir() if p.is_file())
