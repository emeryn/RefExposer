"""The example script of examples/scripts/ is run against the API, so the documented commands keep working."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "examples" / "scripts" / "refex-internal.py"
# The backend image may be built without the examples folder
pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason=f"{SCRIPT} not available")

SITES_CSV = """code,label,owner,people,opened,active,tags,comment
LYO,Lyon,network,120,2019-04-01,true,"prod,eu",main site
NTE,Nantes,secops,40,2021-09-15,false,"lab",
BDX,Bordeaux,network,,2023-01-05,true,,see ticket 1234
"""


@pytest.fixture()
def script(env):
    """The example script, with its HTTP calls routed to the test client."""
    spec = importlib.util.spec_from_file_location("refex_internal", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def request(method, path, body=None):
        r = env.request(method, path, json=body) if body is not None else env.request(method, path)
        if r.status_code >= 400:
            raise module.ApiError(f"HTTP {r.status_code} on {method} {path}: {r.text}")
        return r.json() if r.content else None

    module.request = request
    return module


def run(script, *argv):
    return script.main([str(a) for a in argv])


def test_create_and_feed_an_internal_referential(script, tmp_path, capsys):
    run(script, "create", "sites", "Sites", "--key", "code", "--search", "label",
        "--column", "code:text:required", "--column", "label:text", "--column", "owner:text",
        "--column", "people:integer", "--column", "opened:date", "--column", "active:boolean",
        "--column", "tags:list", "--column", "comment:text")
    assert "created 'sites'" in capsys.readouterr().out

    run(script, "columns", "sites")
    out = capsys.readouterr().out
    assert "key: code" in out and "people: integer" in out and "code: text (required)" in out

    # One row at a time
    run(script, "add", "sites", "code=LYO", "label=Lyon", "owner=network", "people=120", "active=true", "tags=prod,eu")
    row = json.loads(capsys.readouterr().out)
    assert row["people"] == 120 and row["active"] is True and row["tags"] == ["prod", "eu"]

    run(script, "patch", "sites", "LYO", "owner=secops")
    assert json.loads(capsys.readouterr().out)["owner"] == "secops"
    run(script, "set", "sites", "LYO", "label=Lyon Part-Dieu")
    row = json.loads(capsys.readouterr().out)
    assert row["label"] == "Lyon Part-Dieu" and row["owner"] is None  # PUT replaces the whole row

    # Bulk from a CSV: the key already there is updated, the others are created
    path = tmp_path / "sites.csv"
    path.write_text(SITES_CSV, encoding="utf-8")
    run(script, "load", "sites", str(path))
    assert "1 updated" in capsys.readouterr().out
    assert env_rows(script) == ["BDX", "LYO", "NTE"]

    # A file missing a row deletes it with sync
    smaller = tmp_path / "smaller.json"
    smaller.write_text(json.dumps([{"code": "LYO", "label": "Lyon"}, {"code": "BDX", "label": "Bordeaux"}]), encoding="utf-8")
    run(script, "sync", "sites", str(smaller))
    assert "1 deleted" in capsys.readouterr().out
    assert env_rows(script) == ["BDX", "LYO"]

    run(script, "delete", "sites", "BDX")
    assert "deleted BDX" in capsys.readouterr().out
    assert env_rows(script) == ["LYO"]


def env_rows(script):
    out = script.request("GET", "/api/referentials/sites/records?limit=100")
    return sorted(r["code"] for r in out["records"])


def test_unknown_columns_and_bad_values_are_reported(script, tmp_path, capsys):
    run(script, "create", "tiny", "Tiny", "--column", "code:text", "--column", "people:integer")
    capsys.readouterr()
    # An extra column of the file is left out instead of making the whole load fail
    path = tmp_path / "extra.csv"
    path.write_text("code,people,note\nA,1,hello\n", encoding="utf-8")
    run(script, "load", "tiny", str(path))
    captured = capsys.readouterr()
    assert "ignored column(s): note" in captured.err and "1 created" in captured.out

    # A value that does not fit the column type: the whole batch is refused, nothing is written
    bad = tmp_path / "bad.csv"
    bad.write_text("code,people\nB,many\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        run(script, "load", "tiny", str(bad))
    assert "not a valid integer" in str(exc.value)
    assert env_rows_of(script, "tiny") == ["A"]

    # Rows of a JSON Lines file, and the row listing
    lines = tmp_path / "more.jsonl"
    lines.write_text('{"code": "C", "people": 3}\n{"code": "D"}\n', encoding="utf-8")
    run(script, "load", "tiny", str(lines))
    assert "2 created" in capsys.readouterr().out
    run(script, "rows", "tiny", "--limit", "2")
    assert len(capsys.readouterr().out.strip().splitlines()) == 2


def env_rows_of(script, ref_id):
    out = script.request("GET", f"/api/referentials/{ref_id}/records?limit=100")
    return sorted(r["code"] for r in out["records"])
