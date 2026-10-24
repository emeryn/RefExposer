"""Excel sources: sheets offered by the analysis, and a column whose cells do not all share one type."""
import zipfile

from test_admin_referentials import wait_idle

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def _cell(ref, value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'


def _sheet(rows):
    body = ""
    for i, row in enumerate(rows, start=1):
        body += f'<row r="{i}">' + "".join(_cell(f"{chr(64 + j)}{i}", v) for j, v in enumerate(row, start=1)) + "</row>"
    return f'<?xml version="1.0"?><worksheet xmlns="{_NS}"><sheetData>{body}</sheetData></worksheet>'


def write_xlsx(path, sheets):
    """Minimal workbook: {sheet name: rows}, values written as numbers or inline strings as they come."""
    names = list(sheets)
    types = ['<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
             '<Default Extension="xml" ContentType="application/xml"/>',
             '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.'
             'spreadsheetml.sheet.main+xml"/>']
    tabs, rels = "", ""
    for i, name in enumerate(names, start=1):
        tabs += f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>'
        rels += f'<Relationship Id="rId{i}" Type="{_REL}/worksheet" Target="worksheets/sheet{i}.xml"/>'
        types.append(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-'
                     'officedocument.spreadsheetml.worksheet+xml"/>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", f'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                                          f'package/2006/content-types">{"".join(types)}</Types>')
        z.writestr("_rels/.rels", f'<?xml version="1.0"?><Relationships xmlns="{_PKG}">'
                                  f'<Relationship Id="rIdW" Type="{_REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", f'<?xml version="1.0"?><workbook xmlns="{_NS}" xmlns:r="{_REL}"><sheets>{tabs}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", f'<?xml version="1.0"?><Relationships xmlns="{_PKG}">{rels}</Relationships>')
        for i, name in enumerate(names, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet(sheets[name]))
    return path


MIXED = {
    "Comments": [["id", "score", "comment"], [1, 10, 1], [2, 20, 2], [3, 30, "see note"]],
    "Reference": [["code", "label"], ["FR", "France"], ["DE", "Germany"]],
}


def _url(http_dir, name, path):
    root, port = http_dir
    (root / name).write_bytes(path.read_bytes())
    return f"http://127.0.0.1:{port}/{name}"


def test_sheets_and_mixed_column(env, http_dir, tmp_path):
    url = _url(http_dir, "book.xlsx", write_xlsx(tmp_path / "book.xlsx", MIXED))
    p = env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": [url]}}).json()
    assert p["format"] == "xlsx"
    # The sheets are offered, so one can be chosen without opening the file
    assert p["records_path_candidates"] == ["Comments", "Reference"]
    # 'comment' holds numbers then text: read as text instead of failing, and the option is kept
    assert p["error"] is None, p["error"]
    assert p["applied_options"] == {"all_varchar": True}
    assert dict(zip([c["name"] for c in p["columns"]], p["rows"][2]))["comment"] == "see note"

    other = env.post("/api/admin/referentials/preview", json={
        "source": {"type": "http", "urls": [url]}, "options": {"sheet": "Reference"}}).json()
    assert other["error"] is None and [c["name"] for c in other["columns"]] == ["code", "label"]
    assert other["total_rows"] == 2 and other["applied_options"] == {}


def test_import_recovers_a_mixed_column(env, http_dir, tmp_path):
    # A definition written by hand (YAML, sync folder) never went through the analysis: the import recovers by itself
    url = _url(http_dir, "mixed.xlsx", write_xlsx(tmp_path / "mixed.xlsx", MIXED))
    r = env.post("/api/admin/referentials", json={"config": {
        "id": "book", "name": "Book", "format": "xlsx", "key": "id",
        "source": {"type": "http", "urls": [url]}}})
    assert r.status_code == 201, r.text
    run = wait_idle(env, "book")
    assert run["status"] == "success", run
    assert any("read as text" in line["msg"] for line in run["logs"]), run["logs"]
    assert env.get("/api/referentials/book/lookup/3").json()["comment"] == "see note"

    # Chosen sheet and cast back to numbers in the transformation
    r = env.post("/api/admin/referentials", json={"config": {
        "id": "scores", "name": "Scores", "format": "xlsx", "key": "id",
        "options": {"sheet": "Comments", "all_varchar": True},
        "transform": "SELECT * REPLACE (CAST(score AS BIGINT) AS score) FROM {source}",
        "source": {"type": "http", "urls": [url]}}})
    assert r.status_code == 201, r.text
    assert wait_idle(env, "scores")["status"] == "success"
    info = env.get("/api/referentials/scores").json()
    assert {c["name"]: c["type"] for c in info["columns"]}["score"] == "BIGINT"
    assert env.get("/api/referentials/scores/lookup/2").json()["score"] == 20


def test_import_rows_from_a_mixed_spreadsheet(env, tmp_path):
    r = env.post("/api/internal-referentials", json={
        "id": "notes", "name": "Notes", "key": "id",
        "columns": [{"name": "id", "type": "integer"}, {"name": "score", "type": "integer"}, {"name": "comment", "type": "text"}]})
    assert r.status_code == 201, r.text
    path = write_xlsx(tmp_path / "notes.xlsx", MIXED)
    with path.open("rb") as f:
        r = env.post("/api/referentials/notes/records/_import", files={"file": ("notes.xlsx", f)}, data={"mode": "replace"})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 3
    assert env.get("/api/referentials/notes/records/3").json()["comment"] == "see note"
