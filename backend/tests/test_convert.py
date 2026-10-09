"""Sources DuckDB cannot read directly: objects of records, XML, nested objects with duplicate keys."""
import json

from test_admin_referentials import wait_idle

CWE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Weakness_Catalog xmlns="http://cwe.mitre.org/cwe-7" Name="CWE" Version="4.18">
  <Weaknesses>
    <Weakness ID="79" Name="Cross-site Scripting" Abstraction="Base" Status="Stable">
      <Description>The product does not neutralize user-controllable input.</Description>
      <Extended_Description><xhtml:p xmlns:xhtml="http://www.w3.org/1999/xhtml">Mixed <xhtml:b>content</xhtml:b> here.</xhtml:p></Extended_Description>
      <Related_Weaknesses>
        <Related_Weakness Nature="ChildOf" CWE_ID="74"/>
        <Related_Weakness Nature="CanPrecede" CWE_ID="494"/>
      </Related_Weaknesses>
    </Weakness>
    <Weakness ID="89" Name="SQL Injection" Abstraction="Base" Status="Stable">
      <Description>Improper neutralization of special elements in an SQL command.</Description>
      <Related_Weaknesses>
        <Related_Weakness Nature="ChildOf" CWE_ID="943"/>
      </Related_Weaknesses>
    </Weakness>
    <Weakness ID="787" Name="Out-of-bounds Write" Abstraction="Base" Status="Draft">
      <Description>The product writes data past the end of the intended buffer.</Description>
    </Weakness>
  </Weaknesses>
  <Categories>
    <Category ID="1" Name="One"/>
    <Category ID="2" Name="Two"/>
  </Categories>
  <External_References>
    <External_Reference Reference_ID="REF-1"><Title>One</Title></External_Reference>
    <External_Reference Reference_ID="REF-2"><Title>Two</Title></External_Reference>
    <External_Reference Reference_ID="REF-3"><Title>Three</Title></External_Reference>
    <External_Reference Reference_ID="REF-4"><Title>Four</Title></External_Reference>
    <External_Reference Reference_ID="REF-5"><Title>Five</Title></External_Reference>
  </External_References>
</Weakness_Catalog>
"""


def _url(http_dir, name, content):
    root, port = http_dir
    (root / name).write_text(content, encoding="utf-8")
    return f"http://127.0.0.1:{port}/{name}"


def _preview(env, url, **kw):
    r = env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": [url]}, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_object_of_records(env, http_dir):
    # {id: [record]} (ThreatFox export) and {id: record} (usb.ids), at the root or nested
    iocs = {str(1000 + i): [{"ioc_value": f"evil{i}.example", "ioc_type": "domain", "confidence_level": 50 + i}] for i in range(30)}
    url = _url(http_dir, "threatfox.json", json.dumps(iocs))
    p = _preview(env, url)
    assert p["format"] == "json" and p["records_path_candidates"][0] == "*"
    p = _preview(env, url, options={"records_path": "*"})
    assert p["error"] is None and p["total_rows"] == 30
    names = [c["name"] for c in p["columns"]]
    assert names[0] == "_key" and "ioc_value" in names and p["rows"][0][0] == "1000"

    tools = {"functions": {"shell": {}}, "executables": {f"tool{i}": {"functions": {"shell": [{"code": "x"}]}} for i in range(12)}}
    url = _url(http_dir, "gtfobins.json", json.dumps(tools))
    assert "executables.*" in _preview(env, url)["records_path_candidates"]
    p = _preview(env, url, options={"records_path": "executables.*", "records_key": "binary"})
    assert p["error"] is None and p["total_rows"] == 12 and p["columns"][0]["name"] == "binary"

    # Same reading in the import pipeline
    r = env.post("/api/admin/referentials", json={"config": {
        "id": "iocs", "name": "IOCs", "format": "json", "options": {"records_path": "*", "records_key": "ioc_id"},
        "key": "ioc_id", "source": {"type": "http", "urls": [_url(http_dir, "threatfox.json", json.dumps(iocs))]}}})
    assert r.status_code == 201, r.text
    assert wait_idle(env, "iocs")["status"] == "success"
    assert env.get("/api/referentials/iocs/lookup/1005").json()["ioc_value"] == "evil5.example"


def test_xml_source(env, http_dir):
    url = _url(http_dir, "cwec_latest.xml", CWE_XML)
    p = _preview(env, url)
    # Weakness (3 rich records) is preferred to the more frequent but thin External_Reference (5)
    assert p["detected_format"] == "xml" and p["records_path_candidates"][:2] == ["Weakness", "External_Reference"]
    assert p["error"] is None and p["total_rows"] == 3
    cols = {c["name"]: c["type"] for c in p["columns"]}
    assert {"ID", "Name", "Description", "Extended_Description", "Related_Weaknesses"} <= set(cols)
    row = dict(zip(cols, p["rows"][0]))
    assert row["Extended_Description"] == "Mixed content here."

    assert _preview(env, url, options={"records_path": "Category"})["total_rows"] == 2

    r = env.post("/api/admin/referentials", json={"config": {
        "id": "cwe-xml", "name": "CWE (XML)", "format": "xml", "key": "ID", "source": {"type": "http", "urls": [url]},
        "transform": "SELECT 'CWE-' || ID AS cwe_id, * FROM {source}"}})
    assert r.status_code == 201, r.text
    assert wait_idle(env, "cwe-xml")["status"] == "success"
    rec = env.get("/api/referentials/cwe-xml/lookup/89").json()
    assert rec["cwe_id"] == "CWE-89" and rec["Name"] == "SQL Injection"

    # An HTML page served instead of the XML document is a corrupted source
    bad = _url(http_dir, "broken.xml", "<!DOCTYPE html><html><body>Maintenance</body></html>")
    assert "error page" in env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": [bad]}}).json()["detail"]


def test_duplicate_nested_keys(env, http_dir):
    # Keys differing only by case inside nested objects (LOLRMM) make DuckDB's schema detection fail
    tools = [{"Name": f"tool{i}", "Artifacts": {"Event": [{"EventId": 1, "EventID": "1", "Channel": "Security"}]}} for i in range(5)]
    p = _preview(env, _url(http_dir, "lolrmm.json", json.dumps(tools)))
    assert p["error"] is None and p["total_rows"] == 5
    assert p["applied_options"].get("maximum_depth") in (1, 2, 3)


def test_large_multi_document_json_is_split(env, http_dir, monkeypatch):
    # Several big JSON documents (e.g. every yearly NVD feed) are read one document at a time
    from app import convert

    monkeypatch.setattr(convert, "SPLIT_JSON_BYTES", 0)
    urls = []
    for year in (2024, 2025):
        doc = {"format": "NVD_CVE", "vulnerabilities": [{"cve": {"id": f"CVE-{year}-{i:04d}", "published": f"{year}-01-0{i + 1}"}} for i in range(4)]}
        if year == 2025:
            doc["vulnerabilities"][0]["cve"]["cvssV4"] = 9.8  # a field only present in recent files
        urls.append(_url(http_dir, f"nvd-{year}.json", json.dumps(doc)))
    p = _preview(env, urls[0], options={"records_path": "vulnerabilities"})
    assert p["total_rows"] == 4
    r = env.post("/api/admin/referentials", json={"config": {
        "id": "nvd-all", "name": "NVD", "format": "json", "options": {"records_path": "vulnerabilities"}, "key": "id",
        "source": {"type": "http", "urls": urls}}})
    assert r.status_code == 201, r.text
    assert wait_idle(env, "nvd-all")["status"] == "success"
    info = env.get("/api/referentials/nvd-all").json()
    assert info["row_count"] == 8 and "cvssV4" in [c["name"] for c in info["columns"]]
    assert env.get("/api/referentials/nvd-all/lookup/CVE-2025-0000").json()["cvssV4"] == 9.8


def test_large_line_source_is_sampled_and_kept_compressed(env, http_dir, monkeypatch):
    # Passive DNS style: a big .csv.gz is previewed from its beginning and imported without being decompressed on disk
    import gzip

    from app import preview

    root, port = http_dir
    lines = "".join(f"host{i}.example.com,A,192.0.2.{i % 250},{i}\n" for i in range(60_000))
    with gzip.open(root / "rdns-aug.csv.gz", "wt", encoding="utf-8") as f:
        f.write("rrname,rrtype,rdata,count\n" + lines)
    url = f"http://127.0.0.1:{port}/rdns-aug.csv.gz"
    monkeypatch.setattr(preview, "SAMPLE_BYTES", 64 * 1024)
    p = _preview(env, url)
    assert p["sampled"] is True and p["total_rows"] is None and p["error"] is None
    assert p["format"] == "csv" and [c["name"] for c in p["columns"]] == ["rrname", "rrtype", "rdata", "count"]
    assert any("Preview on a sample" in line for line in p["logs"])

    r = env.post("/api/admin/referentials", json={"config": {"id": "rdns", "name": "rDNS", "format": "csv", "source": {"type": "http", "urls": [url]}}})
    assert r.status_code == 201, r.text
    assert wait_idle(env, "rdns")["status"] == "success"
    assert env.get("/api/referentials/rdns").json()["row_count"] == 60_000
    raw = env.data_dir / "rdns" / "raw"
    assert [f.name for f in raw.iterdir() if f.is_file()] == ["rdns-aug.csv.gz"]  # kept compressed

    # Formats read as a whole are not downloaded above the size limit: the format and options are set by hand
    monkeypatch.setattr(preview, "FULL_MAX_BYTES", 10)
    (root / "big.json").write_text(json.dumps({"items": [{"a": i} for i in range(100)]}), encoding="utf-8")
    r = env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": [f"http://127.0.0.1:{port}/big.json"]}})
    assert r.status_code == 200 and "too large to be analysed" in r.json()["unanalysed"], r.text
    assert r.json()["format"] == "json" and r.json()["rows"] == []
