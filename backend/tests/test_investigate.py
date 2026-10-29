"""Relational search: typing a value, finding it wherever it lives, and the graph that comes out.

The catalogue built here is shaped like the real thing on purpose: the same CVE is `cveID` in one source, `cve`
in the next and `id` in the third, so these tests fail if anything starts matching columns by name.
"""
import pytest

from conftest import USER_PASSWORD, create_user, new_client, refresh
from test_admin_referentials import wait_idle

# cveID, like the CISA KEV
KEV = """cveID,vendorProject,product,knownRansomware
CVE-2024-3400,Palo Alto,PAN-OS,Known
CVE-2021-44228,Apache,Log4j2,Known
CVE-2019-0708,Microsoft,Remote Desktop Services,Unknown
"""

# cve, like the EPSS scores
EPSS = """cve,epss,percentile
CVE-2024-3400,0.94012,0.99901
CVE-2021-44228,0.97442,0.99989
CVE-2015-0001,0.00042,0.10000
"""

# id, like an advisory feed, and a CPE and a CWE in the same row
ADVISORIES = """id,summary,platform,weakness,reference
CVE-2024-3400,Command injection in GlobalProtect,cpe:2.3:o:paloaltonetworks:pan-os:11.1.0:*:*:*:*:*:*:*,CWE-77,https://security.paloaltonetworks.com/CVE-2024-3400
CVE-2021-44228,JNDI lookup in Log4j2,cpe:2.3:a:apache:log4j:2.14.1:*:*:*:*:*:*:*,CWE-502,https://logging.apache.org/log4j/2.x/security.html
"""

# An exploit feed tying a CVE to an ATT&CK technique and to the host that serves it
EXPLOITS = """exploit_id,cve,technique,host,sha256
EDB-52118,CVE-2024-3400,T1190,exploit.example.com,6f8db599de986fab7a21625b7916589c14f1b5c8d9b6e0d25c4b0c25c4b0c25c
EDB-50592,CVE-2021-44228,T1190,poc.example.org,9e107d9d372bb6826bd81d3542a419d6f8db599de986fab7a21625b7916589c1
"""


# --------------------------------------------------------------------------- recognising a value

# entities.py is pure: these need no application, and no feature switch
@pytest.mark.parametrize("value,expected", [
    ("CVE-2024-3400", ["cve"]),
    ("cve-2021-44228", ["cve"]),
    ("CWE-77", ["cwe"]),
    ("T1059.001", ["attack_technique"]),
    ("cpe:2.3:o:paloaltonetworks:pan-os:11.1.0:*:*:*:*:*:*:*", ["cpe"]),
    ("192.168.1.1", ["ip"]),
    ("2001:db8::1", ["ip"]),
    ("10.0.0.0/8", ["cidr"]),
    ("AS13335", ["asn"]),
    ("alice@example.com", ["email"]),
    ("https://example.com/a?b=c", ["url"]),
    ("d41d8cd98f00b204e9800998ecf8427e", ["md5"]),
    ("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", ["sha256"]),
    # What must never be taken for an identifier
    ("Microsoft", []),
    ("PAN-OS", []),
    ("Command injection in GlobalProtect", []),
    ("42", []),
    ("2024-10-29", []),
    ("1.2", []),
    ("", []),
])
def test_what_a_value_is_recognised_as(value, expected):
    from app import entities

    assert entities.detect(value) == expected


def test_a_column_is_typed_from_the_two_values_the_profiling_kept():
    from app import entities

    # The same identifier under three names, which is why the values and not the name drive this
    for column in ("cveID", "cve", "id"):
        assert entities.column_entities(column, "VARCHAR", ["CVE-1999-0001", "CVE-2024-9999"]) == ["cve"]
    # Free text, product names and numbers are left alone
    for column, sample in [("vendorProject", ["Apache", "Microsoft"]), ("product", ["Log4j2", "PAN-OS"]),
                           ("summary", ["Command injection", "JNDI lookup in Log4j2"]), ("count", ["0", "2999"]),
                           ("created", ["1831-07-21", "1978-12-29"]), ("code", ["BE", "IT"])]:
        assert entities.column_entities(column, "VARCHAR", sample) == [], column


def test_a_column_holding_two_kinds_of_identifier_is_typed_with_both():
    """The rdata of a passive DNS carries addresses and host names side by side."""
    from app import entities

    assert entities.column_entities("rdata", "VARCHAR", ["10.0.0.0", "mx.circl.lu"]) == ["ip", "domain"]
    # And each value of such a column is then typed by what that value actually is
    assert entities.of_value("10.0.0.0", ["ip", "domain"]) == "ip"
    assert entities.of_value("mx.circl.lu", ["ip", "domain"]) == "domain"


def test_a_type_is_named_once_however_many_hints_its_column_name_matches():
    """`owner_email` matches both the `email` and the `mail` hint of one type."""
    from app import entities

    assert entities.column_entities("owner_email", "VARCHAR", ["a@example.com", "z@example.org"]) == ["email"]


def test_a_mixed_column_is_left_alone_when_a_sample_matches_nothing():
    """One end of the column being an identifier is not enough: free text stays free text."""
    from app import entities

    assert entities.column_entities("notes", "VARCHAR", ["see CVE-2024-3400 for details", "example.com"]) == []


def test_normalising_a_value_puts_two_spellings_on_one_node():
    from app import entities

    assert entities.normalize("cve", "cve-2024-3400") == "CVE-2024-3400"
    assert entities.normalize("domain", "WWW.Example.COM") == "www.example.com"
    assert entities.normalize("ip", "2001:0DB8::0:1") == "2001:db8::1"
    assert entities.normalize("cidr", "10.0.0.5/8") == "10.0.0.0/8"
    # An address Python refuses as ambiguous (leading zeros) is left exactly as the source wrote it, rather
    # than guessed at: 192.168.011.1 is not obviously 192.168.11.1
    assert entities.normalize("ip", "192.168.001.001") == "192.168.001.001"


# Free text mentioning a CVE: the exact lookup alone would never find this row
NOTES = """ticket,note
INC-1,Patched the GlobalProtect flaw CVE-2024-3400 on the perimeter
INC-2,Nothing to do with any of this
"""


def enable(client, on: bool = True) -> None:
    """Turn the relational search on (or off): it is an experimental feature, off by default."""
    r = client.put("/api/admin/settings/experimental", json={"relational_search": on})
    assert r.status_code == 200, r.text


@pytest.fixture(autouse=True)
def feature(env):
    """Every test of this module runs with the feature on; the gate itself is tested explicitly below."""
    enable(env)
    return env


def _source(env, ref_id, name, content, **config):
    folder = env.data_dir / ref_id / "raw"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "data.csv").write_text(content, encoding="utf-8")
    r = env.post("/api/admin/referentials",
                 json={"config": {"id": ref_id, "name": name, "format": "csv",
                                  "source": {"type": "local", "path": "raw/*.csv"}, **config}})
    assert r.status_code == 201, r.text
    assert wait_idle(env, ref_id)["status"] == "success"


@pytest.fixture()
def vulns(env):
    """A small vulnerability catalogue naming the same CVE three different ways."""
    _source(env, "kev", "Known exploited", KEV, key="cveID", category="Vulnerabilities")
    _source(env, "epss", "EPSS scores", EPSS, key="cve", category="Vulnerabilities")
    _source(env, "advisories", "Advisories", ADVISORIES, key="id", category="Vulnerabilities")
    _source(env, "exploits", "Exploits", EXPLOITS, key="exploit_id", category="Exploits")
    return env


def investigate(client, value, **body):
    r = client.post("/api/investigate", json={"value": value, **body})
    assert r.status_code == 200, r.text
    return r.json()


def investigate_many(client, values, **body):
    r = client.post("/api/investigate", json={"values": values, **body})
    assert r.status_code == 200, r.text
    return r.json()


def path_of(connection):
    """The chain of evidence of one connection, as readable labels."""
    return [step["label"] for step in connection["path"]]


def nodes_of(answer, entity):
    return sorted(n["value"] for n in answer["nodes"] if n.get("entity") == entity)


def sources_of(answer):
    return sorted(n["value"] for n in answer["nodes"] if n["kind"] == "source")


# --------------------------------------------------------------------------- typing a value

def test_the_entity_types_say_where_each_kind_of_identifier_lives(vulns):
    answer = vulns.get("/api/investigate/entities").json()
    types = {t["name"]: t for t in answer["types"]}
    cve_columns = {(c["referential_id"], c["column"]) for c in types["cve"]["columns"]}
    # The same identifier, three names, found by its values and not by its column name
    assert ("kev", "cveID") in cve_columns
    assert ("epss", "cve") in cve_columns
    assert ("advisories", "id") in cve_columns
    assert ("exploits", "cve") in cve_columns
    assert ("advisories", "platform") in {(c["referential_id"], c["column"]) for c in types["cpe"]["columns"]}
    assert ("advisories", "weakness") in {(c["referential_id"], c["column"]) for c in types["cwe"]["columns"]}
    assert ("exploits", "sha256") in {(c["referential_id"], c["column"]) for c in types["sha256"]["columns"]}
    assert ("exploits", "technique") in {(c["referential_id"], c["column"]) for c in types["attack_technique"]["columns"]}
    # A column of free text is not an identifier
    assert ("advisories", "summary") not in {(c["referential_id"], c["column"])
                                             for t in answer["types"] for c in t["columns"]}


def test_free_text_is_a_way_into_the_graph_not_an_error(vulns):
    """Anything the global search finds, an investigation finds: it must not be the narrower of the two."""
    # A vendor name: not an identifier, and nothing types it
    answer = investigate(vulns, "Palo Alto")
    assert answer["seed"]["entity"] is None
    assert answer["seed"]["id"] == "query:Palo Alto"
    assert "kev" in sources_of(answer)
    # And the identifiers of the matching rows are the first hop, so free text leads into the relations
    assert "CVE-2024-3400" in nodes_of(answer, "cve")


def test_what_the_global_search_finds_the_investigation_finds(vulns):
    """The contract, checked against /api/search itself rather than against an expectation of mine."""
    for value in ("Palo Alto", "GlobalProtect", "Log4j2", "Deserialization", "CVE-2024-3400"):
        globally = {r["id"] for r in vulns.get("/api/search", params={"q": value}).json()["results"]
                    if r["total"]}
        relational = set(sources_of(investigate(vulns, value)))
        assert globally <= relational, f"{value}: the global search found {globally - relational} and we did not"


def test_the_text_pass_searches_every_text_column_not_only_the_key(vulns):
    """Where the global search narrows to the key for cost, an investigation reads the whole row.

    `kev` names no search column and has `cveID` as its key, so a vendor in `vendorProject` is invisible to a
    search restricted to the key — and a vendor is exactly the kind of thing one investigates.
    """
    answer = investigate(vulns, "Palo Alto")
    assert "kev" in sources_of(answer)
    hit = next(h for h in answer["hits"] if h["referential_id"] == "kev")
    assert hit["how"] == "text" and hit["rows"][0]["cveID"] == "CVE-2024-3400"


def test_free_text_reaches_the_rows_through_a_text_hit(vulns):
    answer = investigate(vulns, "GlobalProtect")
    hit = next(h for h in answer["hits"] if h["referential_id"] == "advisories")
    # A text match says so, so an exact lookup is never confused with a mention
    assert hit["how"] == "text"
    assert hit["rows"][0]["id"] == "CVE-2024-3400"


def test_an_identifier_is_also_searched_as_text_so_a_mention_counts(vulns):
    """A CVE named in a free text column of another row is a relation the exact lookup alone would miss."""
    _source(vulns, "notes", "Incident notes", NOTES, key="ticket")
    answer = investigate(vulns, "CVE-2024-3400")
    assert "notes" in sources_of(answer)
    hit = next(h for h in answer["hits"] if h["referential_id"] == "notes")
    assert hit["how"] == "text"
    # While the referentials holding it in a typed column are still exact matches
    assert next(h for h in answer["hits"] if h["referential_id"] == "kev")["how"] == "exact"


def test_the_passes_can_be_asked_for_one_at_a_time(vulns):
    _source(vulns, "notes", "Incident notes", NOTES, key="ticket")
    exact = investigate(vulns, "CVE-2024-3400", match="exact")
    assert "notes" not in sources_of(exact) and "kev" in sources_of(exact)
    assert exact["seed"]["searched"] == {"exact": True, "text": False}

    text = investigate(vulns, "CVE-2024-3400", match="text")
    assert "notes" in sources_of(text)
    assert text["seed"]["searched"] == {"exact": False, "text": True}


def test_exact_only_on_something_that_is_not_an_identifier_says_what_to_do(vulns):
    r = vulns.post("/api/investigate", json={"value": "Palo Alto", "match": "exact"})
    assert r.status_code == 400
    assert "search it as text instead" in r.json()["detail"]


def test_an_unknown_entity_type_is_refused(vulns):
    r = vulns.post("/api/investigate", json={"value": "CVE-2024-3400", "entity": "nope"})
    assert r.status_code == 400 and "unknown entity type" in r.json()["detail"]


# --------------------------------------------------------------------------- the walk

def exact_hits(answer):
    """Hits from a typed lookup, keyed by referential. A referential can also carry a text hit."""
    return {h["referential_id"]: h for h in answer["hits"] if h["how"] == "exact"}


def test_a_cve_is_found_in_every_referential_that_carries_it(vulns):
    answer = investigate(vulns, "CVE-2024-3400")
    assert answer["seed"]["entity"] == "cve"
    assert sources_of(answer) == ["advisories", "epss", "exploits", "kev"]
    hits = exact_hits(answer)
    # The same identifier under four column names, each found exactly
    assert hits["kev"]["column"] == "cveID" and hits["kev"]["row_count"] == 1
    assert hits["epss"]["column"] == "cve"
    assert hits["advisories"]["column"] == "id"
    assert hits["exploits"]["column"] == "cve"
    # The rows themselves are the evidence
    assert hits["kev"]["rows"][0]["product"] == "PAN-OS"
    assert answer["stats"]["referentials_matched"] == 4


def test_a_cve_that_only_one_source_knows(vulns):
    answer = investigate(vulns, "CVE-2015-0001")
    assert sources_of(answer) == ["epss"]
    # Found exactly in epss, so epss is not text-searched for it as well: the row is counted once
    assert answer["stats"]["rows"] == 1
    assert [h["how"] for h in answer["hits"]] == ["exact"]


def test_the_neighbours_of_the_matching_rows_become_nodes(vulns):
    """One hop already reaches what the matching rows name: the platform, the weakness, the technique."""
    answer = investigate(vulns, "CVE-2024-3400")
    assert nodes_of(answer, "cpe") == ["cpe:2.3:o:paloaltonetworks:pan-os:11.1.0:*:*:*:*:*:*:*"]
    assert nodes_of(answer, "cwe") == ["CWE-77"]
    assert nodes_of(answer, "attack_technique") == ["T1190"]
    assert nodes_of(answer, "domain") == ["exploit.example.com"]
    assert nodes_of(answer, "url") == ["https://security.paloaltonetworks.com/CVE-2024-3400"]
    # Each of those is tied to the CVE by the row they share, and that row names its referential
    relations = {(e["target"], e["referential_id"]) for e in answer["edges"]
                 if e["kind"] == "same_row" and e["source"] == answer["seed"]["id"]}
    assert ("cwe:CWE-77", "advisories") in relations
    assert ("attack_technique:T1190", "exploits") in relations


def test_depth_two_follows_a_neighbour_back_into_the_catalogue(vulns):
    """The technique found through one CVE leads to the other CVE that shares it."""
    shallow = investigate(vulns, "CVE-2024-3400", depth=1)
    assert "cve:CVE-2021-44228" not in {n["id"] for n in shallow["nodes"]}

    deep = investigate(vulns, "CVE-2024-3400", depth=2)
    assert "cve:CVE-2021-44228" in {n["id"] for n in deep["nodes"]}
    found = next(n for n in deep["nodes"] if n["id"] == "cve:CVE-2021-44228")
    assert found["depth"] == 2 and "exploits" in found["referentials"]


def test_the_expansion_can_be_restricted_to_some_entity_types(vulns):
    answer = investigate(vulns, "CVE-2024-3400", depth=2, follow=["cwe"])
    assert nodes_of(answer, "cwe") == ["CWE-77"]
    assert nodes_of(answer, "cpe") == [] and nodes_of(answer, "domain") == []


def test_the_scope_narrows_the_search_to_referentials_or_a_category(vulns):
    assert sources_of(investigate(vulns, "CVE-2024-3400", scope=["kev"])) == ["kev"]
    assert sources_of(investigate(vulns, "CVE-2024-3400", scope=["category:Exploits"])) == ["exploits"]
    both = investigate(vulns, "CVE-2024-3400", scope=["kev", "category:Exploits"])
    assert sources_of(both) == ["exploits", "kev"]


def test_the_value_is_found_whatever_its_case(vulns):
    answer = investigate(vulns, "cve-2024-3400")
    assert answer["seed"]["value"] == "CVE-2024-3400"  # the node holds the normalised form
    assert sources_of(answer) == ["advisories", "epss", "exploits", "kev"]


def test_an_identifier_stored_without_its_prefix_is_still_found(vulns):
    """A declared `entities` mapping types a column whose values carry no prefix (a CWE stored as 77)."""
    _source(vulns, "weaknesses", "Weaknesses", "ID,Name\n77,Command injection\n502,Deserialization\n",
            key="ID", entities={"ID": "cwe"})
    answer = investigate(vulns, "CWE-77")
    assert "weaknesses" in sources_of(answer)
    assert next(h for h in answer["hits"] if h["referential_id"] == "weaknesses")["rows"][0]["Name"] == "Command injection"


def test_a_column_can_be_taken_out_of_the_typed_lookups(vulns):
    """`entities: {col: ""}` is about typing: the column stops being an identifier to pivot on.

    It does not hide the referential from the text pass, which is the global search and has its own rules —
    `scope` is what keeps a referential out of an investigation entirely.
    """
    _source(vulns, "quiet", "Quiet", EPSS, key="cve", entities={"cve": ""})
    types = {t["name"]: t for t in vulns.get("/api/investigate/entities").json()["types"]}
    assert ("quiet", "cve") not in {(c["referential_id"], c["column"]) for c in types["cve"]["columns"]}

    answer = investigate(vulns, "CVE-2024-3400")
    assert "quiet" not in {h["referential_id"] for h in answer["hits"] if h["how"] == "exact"}
    # Still reachable as text, like any other referential
    assert "quiet" in {h["referential_id"] for h in answer["hits"] if h["how"] == "text"}
    # And out of it altogether once the scope says so
    assert "quiet" not in sources_of(investigate(vulns, "CVE-2024-3400", scope=["kev", "epss"]))


def test_an_ip_and_a_domain_pivot_on_the_passive_dns_referential(env):
    """The fixture referential: rrname holds host names, rdata holds addresses."""
    refresh(env, "pdns")
    answer = investigate(env, "mail.circl.lu")
    assert answer["seed"]["entity"] == "domain"
    assert "pdns" in sources_of(answer)
    assert "mx.circl.lu" in nodes_of(answer, "domain")

    by_ip = investigate(env, "185.194.93.14")
    assert by_ip["seed"]["entity"] == "ip"
    assert "www.circl.lu" in nodes_of(by_ip, "domain")


# --------------------------------------------------------------------------- bounds

def test_the_graph_stops_at_the_node_limit_and_says_so(env):
    refresh(env, "pdns")
    answer = investigate(env, "10.0.1.1", depth=3, max_nodes=12, rows_per_lookup=20)
    assert len([n for n in answer["nodes"] if n["kind"] == "entity"]) <= 12
    assert answer["stats"]["truncated"] is True
    assert any("stopped at 12 nodes" in w for w in answer["warnings"])


def test_an_investigation_that_fits_is_not_reported_as_truncated(vulns):
    answer = investigate(vulns, "CVE-2015-0001")
    assert answer["stats"]["truncated"] is False and answer["warnings"] == []


# --------------------------------------------------------------------------- rights

def test_only_the_referentials_this_account_may_read_are_searched(vulns):
    create_user(vulns, "reader")
    uid = next(u["id"] for u in vulns.get("/api/admin/users").json() if u["username"] == "reader")
    vulns.post("/api/admin/grants", json={"referential_id": "kev", "user_id": uid, "level": "read"})
    client = new_client(vulns, "reader", USER_PASSWORD)

    answer = investigate(client, "CVE-2024-3400")
    assert sources_of(answer) == ["kev"]
    # And the catalogue of entity types only shows that one too
    types = {t["name"]: t for t in client.get("/api/investigate/entities").json()["types"]}
    assert {c["referential_id"] for c in types["cve"]["columns"]} == {"kev"}


def test_personal_data_columns_are_masked_in_an_investigation(env):
    """Masked for everyone here, even for an account allowed to reveal them on /rows."""
    folder = env.data_dir / "contacts" / "raw"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "data.csv").write_text("host,owner_email\nmail.circl.lu,alice@example.com\n", encoding="utf-8")
    r = env.post("/api/admin/referentials", json={"config": {
        "id": "contacts", "name": "Contacts", "format": "csv", "key": "host",
        "source": {"type": "local", "path": "raw/*.csv"},
        "pii": {"columns": ["owner_email"], "strategy": "partial", "reveal_groups": []},
    }})
    assert r.status_code == 201, r.text
    assert wait_idle(env, "contacts")["status"] == "success"

    answer = investigate(env, "mail.circl.lu")
    hit = next(h for h in answer["hits"] if h["referential_id"] == "contacts")
    assert hit["rows"][0]["owner_email"] != "alice@example.com"
    # Even asking on /rows with reveal does not change what the investigation serves
    assert "alice@example.com" not in str(answer)


# --------------------------------------------------------------------------- saved searches

def _save(client, name="Palo Alto incident", **spec):
    body = {"name": name, "description": "Everything tied to the GlobalProtect flaw",
            "spec": {"value": "CVE-2024-3400", "depth": 2, **spec}, "tags": ["incident"]}
    r = client.post("/api/searches", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _uid(env, username):
    return next(u["id"] for u in env.get("/api/admin/users").json() if u["username"] == username)


def test_a_search_is_kept_and_replayed(vulns):
    saved = _save(vulns)
    assert saved["level"] == "owner" and saved["spec"]["value"] == "CVE-2024-3400"
    assert saved["last_run_at"] is None

    answer = vulns.post(f"/api/searches/{saved['id']}/run")
    assert answer.status_code == 200, answer.text
    result = answer.json()
    assert sources_of(result) == ["advisories", "epss", "exploits", "kev"]
    assert result["search"]["id"] == saved["id"]
    # Replaying one records when, so a stale search is recognisable
    assert vulns.get(f"/api/searches/{saved['id']}").json()["last_run_at"] is not None


def test_a_saved_search_holds_the_question_not_the_result(vulns):
    """A source added after the search was saved is in the next replay: the catalogue is read as it is then."""
    saved = _save(vulns)
    assert sources_of(vulns.post(f"/api/searches/{saved['id']}/run").json()) == [
        "advisories", "epss", "exploits", "kev"]
    _source(vulns, "latecomer", "Late comer", EPSS, key="cve", category="Vulnerabilities")
    assert "latecomer" in sources_of(vulns.post(f"/api/searches/{saved['id']}/run").json())


def test_a_search_is_private_until_it_is_shared(vulns):
    saved = _save(vulns)
    create_user(vulns, "colleague")
    client = new_client(vulns, "colleague", USER_PASSWORD)
    assert client.get("/api/searches").json()["searches"] == []
    # Not shared and not existing are the same answer: an identifier never says something exists
    assert client.get(f"/api/searches/{saved['id']}").status_code == 404
    assert client.post(f"/api/searches/{saved['id']}/run").status_code == 404


def test_a_search_shared_with_an_account_can_be_replayed_but_not_changed(vulns):
    saved = _save(vulns)
    create_user(vulns, "colleague")
    uid = _uid(vulns, "colleague")
    vulns.post("/api/admin/grants", json={"referential_id": "*", "user_id": uid, "level": "read"})
    r = vulns.put(f"/api/searches/{saved['id']}/shares", json={"shares": [{"user_id": uid, "level": "read"}]})
    assert r.status_code == 200, r.text
    assert r.json()["shares"][0]["username"] == "colleague"

    client = new_client(vulns, "colleague", USER_PASSWORD)
    listed = client.get("/api/searches").json()
    assert [s["name"] for s in listed["searches"]] == [saved["name"]]
    assert listed["searches"][0]["level"] == "read" and listed["owned"] == 0
    assert client.post(f"/api/searches/{saved['id']}/run").status_code == 200
    changed = {"name": "Mine now", "description": "", "spec": {"value": "CVE-2021-44228"}, "tags": []}
    assert client.put(f"/api/searches/{saved['id']}", json=changed).status_code == 403
    assert client.delete(f"/api/searches/{saved['id']}").status_code == 403


def test_a_search_shared_for_editing_can_be_changed_but_not_deleted(vulns):
    saved = _save(vulns)
    create_user(vulns, "colleague")
    uid = _uid(vulns, "colleague")
    vulns.put(f"/api/searches/{saved['id']}/shares", json={"shares": [{"user_id": uid, "level": "edit"}]})
    client = new_client(vulns, "colleague", USER_PASSWORD)
    changed = {"name": "Renamed", "description": "", "spec": {"value": "CVE-2021-44228"}, "tags": []}
    assert client.put(f"/api/searches/{saved['id']}", json=changed).status_code == 200
    assert vulns.get(f"/api/searches/{saved['id']}").json()["name"] == "Renamed"
    # Deleting stays with the owner
    assert client.delete(f"/api/searches/{saved['id']}").status_code == 403
    assert vulns.delete(f"/api/searches/{saved['id']}").status_code == 200


def test_a_search_shared_with_a_group(vulns):
    saved = _save(vulns)
    group = vulns.post("/api/admin/groups", json={"name": "soc"}).json()
    create_user(vulns, "analyst", group_ids=[group["id"]])
    create_user(vulns, "outsider")
    vulns.put(f"/api/searches/{saved['id']}/shares", json={"shares": [{"group_id": group["id"], "level": "read"}]})

    member = new_client(vulns, "analyst", USER_PASSWORD)
    assert [s["name"] for s in member.get("/api/searches").json()["searches"]] == [saved["name"]]
    other = new_client(vulns, "outsider", USER_PASSWORD)
    assert other.get("/api/searches").json()["searches"] == []


def test_replaying_a_shared_search_uses_the_rights_of_whoever_runs_it(vulns):
    """The question is shared, not the data: a colleague sees what their own grants allow, nothing more."""
    saved = _save(vulns)
    create_user(vulns, "colleague")
    uid = _uid(vulns, "colleague")
    vulns.post("/api/admin/grants", json={"referential_id": "kev", "user_id": uid, "level": "read"})
    vulns.put(f"/api/searches/{saved['id']}/shares", json={"shares": [{"user_id": uid, "level": "read"}]})

    client = new_client(vulns, "colleague", USER_PASSWORD)
    assert sources_of(client.post(f"/api/searches/{saved['id']}/run").json()) == ["kev"]
    # While the owner still sees the whole catalogue through the very same search
    assert sources_of(vulns.post(f"/api/searches/{saved['id']}/run").json()) == [
        "advisories", "epss", "exploits", "kev"]


def test_unsharing_is_leaving_a_subject_out_of_the_list(vulns):
    saved = _save(vulns)
    create_user(vulns, "colleague")
    uid = _uid(vulns, "colleague")
    vulns.put(f"/api/searches/{saved['id']}/shares", json={"shares": [{"user_id": uid, "level": "read"}]})
    client = new_client(vulns, "colleague", USER_PASSWORD)
    assert client.get(f"/api/searches/{saved['id']}").status_code == 200

    vulns.put(f"/api/searches/{saved['id']}/shares", json={"shares": []})
    assert client.get(f"/api/searches/{saved['id']}").status_code == 404
    assert vulns.get(f"/api/searches/{saved['id']}").json()["shares"] == []


def test_a_share_names_a_user_or_a_group_never_both(vulns):
    saved = _save(vulns)
    group = vulns.post("/api/admin/groups", json={"name": "soc"}).json()
    create_user(vulns, "colleague")
    uid = _uid(vulns, "colleague")
    for bad in ({"user_id": uid, "group_id": group["id"]}, {}):
        r = vulns.put(f"/api/searches/{saved['id']}/shares", json={"shares": [{**bad, "level": "read"}]})
        assert r.status_code == 422, bad
    r = vulns.put(f"/api/searches/{saved['id']}/shares", json={"shares": [{"user_id": 9999, "level": "read"}]})
    assert r.status_code == 422 and "unknown user" in r.json()["detail"]


def test_an_administrator_sees_every_saved_search(vulns):
    create_user(vulns, "colleague")
    client = new_client(vulns, "colleague", USER_PASSWORD)
    saved = _save(client, name="Theirs")
    listed = vulns.get("/api/searches").json()
    assert "Theirs" in [s["name"] for s in listed["searches"]]
    assert vulns.get("/api/searches", params={"mine": True}).json()["searches"] == []


def test_who_a_search_can_be_shared_with_is_scoped_to_your_groups(vulns):
    """Signing in does not hand the account directory to everyone: an ordinary account sees its groups."""
    group = vulns.post("/api/admin/groups", json={"name": "soc"}).json()
    other = vulns.post("/api/admin/groups", json={"name": "legal"}).json()
    create_user(vulns, "analyst", group_ids=[group["id"]])
    create_user(vulns, "colleague", group_ids=[group["id"]])
    create_user(vulns, "lawyer", group_ids=[other["id"]])

    client = new_client(vulns, "analyst", USER_PASSWORD)
    targets = client.get("/api/searches/share-targets").json()
    assert targets["scoped_to_groups"] is True
    assert [g["name"] for g in targets["groups"]] == ["soc"]
    # The people of its own group, itself excluded; nobody from a group it is not in
    assert [u["username"] for u in targets["users"]] == ["colleague"]

    # An administrator sees every account and every group
    admin = vulns.get("/api/searches/share-targets").json()
    assert admin["scoped_to_groups"] is False
    assert {"soc", "legal"} <= {g["name"] for g in admin["groups"]}
    assert "lawyer" in {u["username"] for u in admin["users"]}


def test_share_targets_is_not_read_as_a_search_identifier(vulns):
    """`/searches/share-targets` must not be matched as `/searches/{id}`."""
    assert vulns.get("/api/searches/share-targets").status_code == 200
    assert vulns.get("/api/searches/not-a-number").status_code == 422


# --------------------------------------------------------------------------- the experimental switch

def test_the_feature_is_off_until_an_administrator_turns_it_on(env):
    """Off by default, and off means not reachable — not merely hidden in the menu."""
    enable(env, False)
    assert env.get("/api/investigate/status").json() == {"enabled": False, "experimental": True}
    for method, path, body in [
        ("post", "/api/investigate", {"value": "CVE-2024-3400"}),
        ("get", "/api/investigate/entities", None),
        ("get", "/api/searches", None),
        ("post", "/api/searches", {"name": "x", "description": "", "spec": {"value": "CVE-2024-3400"}, "tags": []}),
        ("get", "/api/searches/share-targets", None),
        ("get", "/api/searches/1", None),
        ("post", "/api/searches/1/run", None),
    ]:
        call = getattr(env, method)
        r = call(path, json=body) if body is not None else call(path)
        assert r.status_code == 404, f"{method.upper()} {path} answered {r.status_code}"
        assert "not enabled" in r.json()["detail"]


def test_turning_it_on_makes_it_reachable_and_off_again_hides_it(vulns):
    assert vulns.get("/api/investigate/status").json()["enabled"] is True
    saved = _save(vulns)
    assert vulns.post(f"/api/searches/{saved['id']}/run").status_code == 200

    enable(vulns, False)
    assert vulns.post(f"/api/searches/{saved['id']}/run").status_code == 404
    # The searches are kept, not deleted: turning the feature back on finds them again
    enable(vulns, True)
    assert [x["name"] for x in vulns.get("/api/searches").json()["searches"]] == [saved["name"]]


def test_only_an_administrator_can_turn_it_on(env):
    create_user(env, "someone")
    client = new_client(env, "someone", USER_PASSWORD)
    assert client.put("/api/admin/settings/experimental", json={"relational_search": True}).status_code == 403
    # And an ordinary account can still ask whether it is on, to know what to show
    assert client.get("/api/investigate/status").status_code == 200


def test_the_assistant_is_not_offered_the_tools_of_a_feature_that_is_off(env):
    from app.mcp import hidden_tools
    from app.db import session_factory

    names = {"investigate", "entity_types", "list_saved_searches", "run_saved_search"}
    with session_factory()() as db:
        enable(env, False)
        assert hidden_tools(db) == names
        enable(env, True)
        assert hidden_tools(db) == frozenset()


# --------------------------------------------------------------------------- several seeds, and their link

def test_several_values_are_all_investigated(vulns):
    answer = investigate_many(vulns, ["CVE-2024-3400", "CVE-2021-44228"])
    assert answer["stats"]["seeds"] == 2
    assert [s["value"] for s in answer["seeds"]] == ["CVE-2024-3400", "CVE-2021-44228"]
    # Each seed keeps its own place in the graph, and says which seed it is
    seeds = {n["id"]: n for n in answer["nodes"] if n.get("seed")}
    assert set(seeds) == {"cve:CVE-2024-3400", "cve:CVE-2021-44228"}
    assert seeds["cve:CVE-2021-44228"]["seed_index"] == 1
    # And every referential carrying either of them is in
    assert set(sources_of(answer)) >= {"kev", "epss", "advisories", "exploits"}


def test_the_link_between_two_values_is_reported_with_its_chain(vulns):
    """Both CVEs are exploited through T1190, so the technique is what ties them together."""
    answer = investigate_many(vulns, ["CVE-2024-3400", "CVE-2021-44228"], depth=2)
    assert answer["connections"], answer["warnings"]
    link = answer["connections"][0]
    assert link["seeds"] == [0, 1]
    assert link["through"]["label"] == "T1190"
    # The chain reads seed to seed through the meeting point
    chain = path_of(link)
    assert chain[0] == "CVE-2024-3400" and chain[-1] == "CVE-2021-44228"
    assert "T1190" in chain
    assert link["hops"] == len(chain) - 1


def test_a_node_reached_from_both_values_is_a_meeting_point(vulns):
    answer = investigate_many(vulns, ["CVE-2024-3400", "CVE-2021-44228"], depth=2)
    shared = {s["label"]: s for s in answer["shared"]}
    assert "T1190" in shared and shared["T1190"]["seeds"] == [0, 1]
    # A node only one of them reaches is not shared
    assert "CWE-77" not in shared
    # Every node says which seeds reached it, so the graph can be coloured by provenance
    technique = next(n for n in answer["nodes"] if n["id"] == "attack_technique:T1190")
    assert technique["seeds"] == [0, 1]


def test_two_values_with_nothing_in_common_report_no_link(vulns):
    answer = investigate_many(vulns, ["CVE-2024-3400", "CVE-2015-0001"], depth=2)
    assert answer["connections"] == []
    assert answer["stats"]["connections"] == 0


def test_a_depth_too_shallow_to_meet_says_so_instead_of_implying_there_is_no_link(vulns):
    """A vendor and a weakness are four hops apart, so depth 1 cannot meet — a bound, not an answer.

    `Palo Alto` reaches CVE-2024-3400 and `CWE-502` reaches CVE-2021-44228, each in one hop; the two CVEs
    then meet on T1190, which takes one hop more from each side.
    """
    shallow = investigate_many(vulns, ["Palo Alto", "CWE-502"], depth=1)
    assert shallow["connections"] == []
    assert any("no link found between the 2 values at depth 1" in w for w in shallow["warnings"])
    # And the link is there once the walk is deep enough
    deep = investigate_many(vulns, ["Palo Alto", "CWE-502"], depth=2)
    assert deep["connections"], deep["warnings"]
    assert path_of(deep["connections"][0])[0] == "Palo Alto"


def test_two_values_whose_own_rows_already_name_the_same_thing_meet_at_depth_one(vulns):
    """Both CVEs are exploited through T1190, and each says so in its own row: one hop is enough."""
    answer = investigate_many(vulns, ["CVE-2024-3400", "CVE-2021-44228"], depth=1)
    assert answer["connections"][0]["through"]["label"] == "T1190"


def test_sharing_a_referential_is_not_a_link(vulns):
    """Every CVE is in the EPSS feed: routing a link through a referential would tie any two together."""
    answer = investigate_many(vulns, ["CVE-2024-3400", "CVE-2015-0001"], depth=2)
    assert "epss" in sources_of(answer)  # both are in it
    assert answer["connections"] == []   # which says nothing about them being related
    assert all(s["kind"] == "entity" for s in answer["shared"])


def test_each_hop_of_a_chain_names_the_referential_that_justifies_it(vulns):
    answer = investigate_many(vulns, ["CVE-2024-3400", "CVE-2021-44228"], depth=2)
    chain = answer["connections"][0]["path"]
    assert "via" not in chain[0]  # the seed itself is not justified by anything
    assert chain[1]["via"] == ["exploits"]
    assert all(step["via"] for step in chain[1:])


def test_an_identifier_and_free_text_can_be_linked(vulns):
    """The seeds do not have to be the same kind of thing: a CVE and a vendor name meet all the same."""
    answer = investigate_many(vulns, ["Palo Alto", "T1190"], depth=2)
    assert answer["seeds"][0]["entity"] is None  # free text
    assert answer["seeds"][1]["entity"] == "attack_technique"
    assert answer["connections"], answer["warnings"]
    assert path_of(answer["connections"][0])[0] == "Palo Alto"


def test_three_values_report_a_link_per_pair(vulns):
    answer = investigate_many(vulns, ["CVE-2024-3400", "CVE-2021-44228", "T1190"], depth=2)
    pairs = {tuple(c["seeds"]) for c in answer["connections"]}
    # The two CVEs meet each other, and each meets the technique
    assert (0, 1) in pairs and (0, 2) in pairs and (1, 2) in pairs
    # Shortest first, so the most direct link is the one read
    assert [c["hops"] for c in answer["connections"]] == sorted(c["hops"] for c in answer["connections"])


def test_the_closest_meeting_point_is_the_one_reported(vulns):
    answer = investigate_many(vulns, ["CVE-2024-3400", "CVE-2021-44228"], depth=3)
    link = next(c for c in answer["connections"] if c["seeds"] == [0, 1])
    # Several nodes may tie them; the chain shown is the shortest one
    assert link["meeting_points"] >= 1
    others = [c for c in answer["connections"] if c["seeds"] == [0, 1]]
    assert len(others) == 1, "one connection per pair of seeds"


def test_one_value_behaves_exactly_as_before(vulns):
    """A single seed has nothing to tie, and the older single-value shape still answers."""
    answer = investigate(vulns, "CVE-2024-3400")
    assert answer["stats"]["seeds"] == 1
    assert answer["connections"] == [] and answer["shared"] == []
    assert answer["seed"]["value"] == "CVE-2024-3400"
    assert [s["value"] for s in answer["seeds"]] == ["CVE-2024-3400"]


def test_a_value_is_required(vulns):
    assert vulns.post("/api/investigate", json={}).status_code == 422
    assert vulns.post("/api/investigate", json={"values": []}).status_code == 422
    assert vulns.post("/api/investigate", json={"value": "   "}).status_code == 422


def test_duplicate_seeds_are_investigated_once(vulns):
    """Two spellings of one value are one seed, or it would be reported as linked to itself."""
    answer = investigate_many(vulns, ["CVE-2024-3400", "cve-2024-3400", "CVE-2024-3400"])
    assert answer["stats"]["seeds"] == 1
    assert [s["value"] for s in answer["seeds"]] == ["CVE-2024-3400"]
    assert answer["connections"] == [] and answer["shared"] == []
    # It is still the same investigation as giving it once
    assert sources_of(answer) == sources_of(investigate(vulns, "CVE-2024-3400"))


def test_a_saved_search_can_hold_several_values(vulns):
    body = {"name": "Two CVEs", "description": "", "tags": [],
            "spec": {"values": ["CVE-2024-3400", "CVE-2021-44228"], "depth": 2}}
    saved = vulns.post("/api/searches", json=body)
    assert saved.status_code == 201, saved.text
    answer = vulns.post(f"/api/searches/{saved.json()['id']}/run")
    assert answer.status_code == 200, answer.text
    assert answer.json()["connections"]


def test_a_search_saved_before_several_values_still_replays(vulns):
    """The single-value spelling is what older saved searches hold."""
    saved = vulns.post("/api/searches", json={"name": "One", "description": "", "tags": [],
                                              "spec": {"value": "CVE-2024-3400", "depth": 1}}).json()
    answer = vulns.post(f"/api/searches/{saved['id']}/run")
    assert answer.status_code == 200, answer.text
    assert [s["value"] for s in answer.json()["seeds"]] == ["CVE-2024-3400"]


# --------------------------------------------------------------------------- expanding in place

def test_expanding_from_a_value_is_an_ordinary_call_whose_ids_line_up(vulns):
    """The interface grows the graph by merging a depth-1 walk into the one on screen.

    What makes that possible is that a node identifier is derived from the value and nothing else, so the same
    value is the same node in two separate answers. This test is the contract behind that merge.
    """
    first = investigate(vulns, "CVE-2024-3400", depth=1)
    technique = next(n for n in first["nodes"] if n["id"] == "attack_technique:T1190")

    # Expanding from the technique: exactly what the page sends when you click it
    added = vulns.post("/api/investigate",
                       json={"values": [technique["value"]], "entity": "attack_technique",
                             "match": "exact", "depth": 1}).json()
    # The node of the clicked value is the same identifier on both sides, which is what the union merges on
    assert added["seeds"][0]["id"] == technique["id"]
    # And it brings the other CVE that shares the technique, which the first walk had not reached
    assert "cve:CVE-2021-44228" in {n["id"] for n in added["nodes"]}
    assert "cve:CVE-2021-44228" not in {n["id"] for n in first["nodes"]}
    # The referential nodes line up too, so a referential is never drawn twice
    assert {n["id"] for n in added["nodes"] if n["kind"] == "source"} <= {f"source:{r}" for r in
                                                                         ("exploits", "advisories", "epss", "kev")}


def test_a_value_picked_out_of_a_row_can_be_expanded_even_if_it_is_not_an_identifier(vulns):
    """Picking `PAN-OS` out of a row is how you follow something no pattern recognises."""
    answer = investigate(vulns, "CVE-2024-3400")
    row = next(h for h in answer["hits"] if h["referential_id"] == "kev")["rows"][0]
    assert row["product"] == "PAN-OS"
    # The page sends it as plain text, since nothing types it
    expanded = vulns.post("/api/investigate", json={"values": [row["product"]], "match": "auto", "depth": 1})
    assert expanded.status_code == 200, expanded.text
    assert "kev" in sources_of(expanded.json())
