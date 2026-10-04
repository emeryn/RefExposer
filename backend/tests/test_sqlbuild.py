import duckdb
import pytest

from app.sqlbuild import Filter, QueryError, TableQuery, build_source_sql, lit, parse_filters, type_kind


def test_literal_escaping():
    assert lit("a'b") == "'a''b'"
    assert lit("x\\'; DROP") == "'x\\''; DROP'"
    assert duckdb.sql(f"SELECT {lit(chr(39) + 'a' + chr(92))}").fetchone()[0] == "'a\\"
    assert lit({"a": "VARCHAR"}) == "{'a': 'VARCHAR'}"
    assert lit([1, "x"]) == "[1, 'x']"


def test_parse_filters():
    fs = parse_filters([("q", "x"), ("year__gte", "2000"), ("name", "a"), ("weird__col", "1"), ("limit", "5")])
    assert [(f.column, f.op, f.value) for f in fs] == [("year", "gte", "2000"), ("name", "eq", "a"), ("weird__col", "eq", "1")]


def test_type_kind():
    assert type_kind("VARCHAR[]") == "list"
    assert type_kind("DECIMAL(10,2)") == "number"
    assert type_kind("TIMESTAMP WITH TIME ZONE") == "temporal"
    assert type_kind("STRUCT(a INTEGER)") == "struct"


@pytest.fixture()
def con():
    c = duckdb.connect()
    c.execute("CREATE TABLE t AS SELECT * FROM (VALUES ('FR', 10, ['a','b'], DATE '2020-01-01'), ('DE', 20, ['c'], NULL), ('it''s', 30, [], DATE '2021-06-01')) v(code, n, tags, d)")
    return c


COLS = {"code": "VARCHAR", "n": "INTEGER", "tags": "VARCHAR[]", "d": "DATE"}


@pytest.mark.parametrize(
    "filters,expected",
    [
        ([Filter("code", "eq", "FR")], 1),
        ([Filter("n", "gte", "20")], 2),
        ([Filter("n", "in", "10, 30")], 2),
        ([Filter("n", "nin", "10")], 2),
        ([Filter("code", "contains", "'")], 1),
        ([Filter("code", "startswith", "f")], 1),
        ([Filter("tags", "eq", "B")], 1),
        ([Filter("tags", "contains", "c")], 1),
        ([Filter("tags", "isnull", "true")], 1),
        ([Filter("d", "isnull", "false")], 2),
        ([Filter("d", "gt", "2020-06-01")], 1),
        ([Filter("code", "regex", "^[A-Z]{2}$")], 2),
        ([Filter("code", "ne", "FR"), Filter("n", "lt", "25")], 1),
    ],
)
def test_filters(con, filters, expected):
    tq = TableQuery("t", COLS, filters=filters)
    assert con.execute(tq.count_sql()).fetchone()[0] == expected


def test_search_and_sort(con):
    tq = TableQuery("t", COLS, search="b", sort="-n")
    assert con.execute(tq.select_sql(10)).fetchall()[0][0] == "FR"
    tq = TableQuery("t", COLS, sort="-d,code", select=["code"])
    assert [r[0] for r in con.execute(tq.select_sql(10)).fetchall()] == ["it's", "FR", "DE"]


def test_unknown_column(con):
    with pytest.raises(QueryError):
        TableQuery("t", COLS, filters=[Filter("nope", "eq", "1")]).where()
    with pytest.raises(QueryError):
        TableQuery("t", COLS, sort="nope").order_by()
    with pytest.raises(QueryError):
        TableQuery("t", COLS, filters=[Filter("n", "bogus", "1")]).where()


def test_transform_placeholders():
    sql = build_source_sql("csv", [["/a.csv"], ["/b.csv"]], {}, "SELECT * FROM {source0} JOIN {source1} USING (k) -- {files1}")
    assert "read_csv(['/a.csv'])" in sql and "read_csv(['/b.csv'])" in sql and "['/b.csv']" in sql
    sql = build_source_sql("tsv", [["/a.tsv", "/b.tsv"]], {"nullstr": "\\N"}, None)
    assert sql == "SELECT * FROM read_csv(['/a.tsv', '/b.tsv'], nullstr='\\N', delim='\t', union_by_name=true)"
