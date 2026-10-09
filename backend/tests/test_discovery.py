import json

from conftest import refresh
from test_git_source import GitServer, git, repos  # noqa: F401 (fixture)


def by_path(result):
    return {c["path"]: c for c in result["candidates"]}


def test_http_folder(env, http_dir):
    feeds = env.www / "feeds"
    (feeds / "sub").mkdir(parents=True)
    (feeds / "countries.csv").write_text("code,label\nFR,France\nDE,Allemagne\n", encoding="utf-8")
    (feeds / "ranges.json").write_text(json.dumps({"syncToken": "1", "prefixes": [{"ip_prefix": "1.2.3.0/24", "region": "eu"},
                                                                                  {"ip_prefix": "5.6.7.0/24", "region": "us"}]}), encoding="utf-8")
    (feeds / "README.md").write_text("# feeds", encoding="utf-8")
    (feeds / "sub" / "words.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    url = f"http://127.0.0.1:{http_dir[1]}/feeds/"

    r = env.post("/api/admin/discovery/scan", json={"source": {"type": "http", "url": url}, "analyse": False})
    assert r.status_code == 200, r.text
    res = r.json()
    assert set(by_path(res)) == {"countries.csv", "ranges.json"} and res["skipped"] == ["README.md"]

    r = env.post("/api/admin/discovery/scan", json={"source": {"type": "http", "url": url, "depth": 1}, "analyse": True,
                                                    "category": "Feeds", "tags": ["test"], "schedule": "0 5 * * *"})
    res = by_path(r.json())
    assert set(res) == {"countries.csv", "ranges.json", "sub/words.txt"}
    ranges = res["ranges.json"]
    assert ranges["config"]["options"] == {"records_path": "prefixes"}, ranges
    assert ranges["analysis"]["columns"] == ["ip_prefix", "region"]
    cfg = res["countries.csv"]["config"]
    assert cfg["id"] == "countries-2"  # 'countries' is taken
    assert cfg["source"] == {"type": "http", "urls": [url + "countries.csv"]}
    assert cfg["category"] == "Feeds" and cfg["tags"] == ["test"] and cfg["schedule"] == "0 5 * * *"

    configs = [c["config"] for c in res.values() if c["selected"]]
    r = env.post("/api/admin/discovery/apply", json={"configs": configs + [{"id": "bad id!"}], "pull": False})
    assert r.status_code == 200, r.text
    out = r.json()
    assert sorted(out["created"]) == ["countries-2", "ranges", "words-2"] and out["errors"][0]["id"] == "bad id!"
    assert refresh(env, "ranges")["rows"] == 2

    # Scanned again: the sources already defined are flagged and not selected
    res = by_path(env.post("/api/admin/discovery/scan", json={"source": {"type": "http", "url": url}, "analyse": False}).json())
    assert res["ranges.json"]["duplicate_of"] == "ranges" and res["ranges.json"]["selected"] is False


def test_git_repository(env, repos):  # noqa: F811
    server, work = repos
    r = env.post("/api/admin/discovery/scan", json={"source": {"type": "git", "repository": server.url("public")}, "analyse": True})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["commit"] == git("rev-parse", "HEAD", cwd=work)
    cands = by_path(res)
    assert set(cands) == {"data/countries.csv", "data/notes.txt"} and set(res["skipped"]) == {"README.md", "big.bin"}
    c = cands["data/countries.csv"]
    assert c["config"]["source"] == {"type": "git", "repository": server.url("public"), "path": "data/countries.csv"}
    assert c["analysis"]["columns"] == ["code", "label"] and c["analysis"]["total_rows"] == 2

    # Glob of the files
    res = by_path(env.post("/api/admin/discovery/scan", json={"source": {"type": "git", "repository": server.url("public"),
                                                                         "path": "data/*.csv"}, "analyse": False}).json())
    assert set(res) == {"data/countries.csv"}
    r = env.post("/api/admin/discovery/apply", json={"configs": [res["data/countries.csv"]["config"]], "pull": False})
    created = r.json()["created"][0]
    assert refresh(env, created)["rows"] == 2


def test_private_repository_needs_a_secret(env, repos):  # noqa: F811
    from test_git_source import TOKEN

    server, _ = repos
    src = {"type": "git", "repository": server.url("private"), "token": TOKEN}
    r = env.post("/api/admin/discovery/scan", json={"source": src, "analyse": False})
    assert r.status_code == 400 and "secret manager" in r.text
    assert env.post("/api/admin/secrets", json={"name": "git-token", "value": TOKEN}).status_code == 201
    src["token"] = "${secret:git-token}"
    r = env.post("/api/admin/discovery/scan", json={"source": src, "analyse": True})
    assert r.status_code == 200, r.text
    c = by_path(r.json())["data/countries.csv"]
    assert c["config"]["source"]["token"] == "${secret:git-token}" and c["analysis"]["total_rows"] == 2
    assert TOKEN not in r.text
