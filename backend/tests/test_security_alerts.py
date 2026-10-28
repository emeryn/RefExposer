"""Checks behind the code-scanning alerts: what is refused, what is masked, what stays local."""
import re

import pytest

from app.appsettings import EMAIL_RE, AssistantSettings, audit_safe
from app.network import UnsafeUrl, validate_outbound
from app.oidc import safe_next


# --------------------------------------------------------------------------- outbound addresses

def test_only_http_addresses_without_credentials():
    assert validate_outbound("https://example.com/path") == "https://example.com/path"
    for bad, reason in (
        ("file:///etc/passwd", "expected"),
        ("gopher://example.com", "expected"),
        ("ftp://example.com", "expected"),
        ("", "expected"),
        ("https://user:secret@example.com", "credentials"),
        ("https://", "no host"),
    ):
        with pytest.raises(UnsafeUrl, match=reason):
            validate_outbound(bad)


def test_metadata_and_link_local_are_refused():
    """The addresses that answer with the credentials of the machine are never a legitimate target."""
    for bad in ("http://169.254.169.254/latest/meta-data/", "http://169.254.0.1/", "http://[fe80::1]/"):
        with pytest.raises(UnsafeUrl, match="link-local or metadata"):
            validate_outbound(bad)


def test_private_and_loopback_addresses_are_normal():
    """An inference server, a collector or a peer on the internal network is the usual case, not an attack."""
    for good in ("http://10.1.2.3:11434", "http://192.168.1.10/api", "http://127.0.0.1:8080/api",
                 "http://ollama.internal:11434"):
        assert validate_outbound(good) == good
    # A caller that wants to refuse this machine can say so
    with pytest.raises(UnsafeUrl, match="this machine"):
        validate_outbound("http://127.0.0.1:8080", allow_loopback=False)


def test_a_name_that_does_not_resolve_is_not_refused():
    """Through a proxy, the name is resolved by the proxy: not resolving here says nothing."""
    assert validate_outbound("http://nothing.invalid/x") == "http://nothing.invalid/x"


# --------------------------------------------------------------------------- redirects

def test_a_checked_address_is_not_followed_elsewhere(env, http_dir):
    """Validating the address is worth nothing if a redirect can then send the request anywhere.

    The proxy test and the federation catalog call an address an administrator typed, which is checked
    first; they must not follow a 301. The sources still do: a mirror or a signed link redirects routinely.
    """
    from app.network import client
    from app.settings import get_settings

    www, port = http_dir
    (www / "sub").mkdir()
    (www / "sub" / "index.html").write_text("the redirect target", encoding="utf-8")
    settings = get_settings()
    url = f"http://127.0.0.1:{port}/sub"  # a folder without its slash: the server answers 301

    with client(settings, follow_redirects=False) as c:
        r = c.get(url)
        assert r.status_code == 301 and "redirect target" not in r.text
    with client(settings, follow_redirects=True) as c:  # the sources keep following them
        r = c.get(url)
        assert r.status_code == 200 and "the redirect target" in r.text


def test_the_proxy_test_does_not_follow_a_redirect(env, http_dir, monkeypatch):
    """A 301 is already the answer the test is after: the proxy let the request through."""
    from app import network

    seen = {}
    real = network.client  # captured before the patch: api.network is this very module

    def spy(settings, cfg=None, verify_tls=True, timeout=None, follow_redirects=True):
        seen["follow_redirects"] = follow_redirects
        return real(settings, cfg, verify_tls, timeout, follow_redirects)

    monkeypatch.setattr(network, "client", spy)
    www, port = http_dir
    (www / "ok.txt").write_text("fine", encoding="utf-8")
    r = env.post("/api/admin/settings/proxy/test", json={"url": f"http://127.0.0.1:{port}/ok.txt"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True, r.text
    assert seen["follow_redirects"] is False


def test_the_federation_catalog_does_not_follow_a_redirect(env, monkeypatch):
    """The call carries the API token of the service account on the peer: a 301 must not hand it over."""
    from app import federation, ingest

    seen = {}
    real = ingest.http_client

    def spy(settings, follow_redirects=True):
        seen["follow_redirects"] = follow_redirects
        return real(settings, follow_redirects)

    monkeypatch.setattr(ingest, "http_client", spy)
    with pytest.raises(federation.FederationError):  # nothing answers at that address
        federation.fetch_catalog(env.app_.state.service, "http://127.0.0.1:1/api", "rfx_token")
    assert seen["follow_redirects"] is False


def test_a_discovery_scan_refuses_a_metadata_address(env):
    """The folder is typed by an administrator, like the address of the proxy test."""
    refused = env.post("/api/admin/discovery/scan", json={
        "source": {"type": "http", "url": "http://169.254.169.254/latest/meta-data/"}, "analyse": False})
    assert refused.status_code in (400, 422)
    assert "link-local or metadata" in refused.text


# --------------------------------------------------------------------------- after sign-in

def test_only_local_paths_after_sign_in():
    assert safe_next("/r/cisa-kev/data") == "/r/cisa-kev/data"
    assert safe_next("/search?q=a#b") == "/search?q=a#b"
    for bad in ("https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)",
                "http://evil.example/path", None, "", "evil.example"):
        assert safe_next(bad) == "/", bad


# --------------------------------------------------------------------------- logs

def test_credentials_of_the_assistant_never_reach_the_logs():
    cfg = AssistantSettings(enabled=True, base_url="http://ollama.internal:11434", model="m",
                            api_key="sk-secret", headers={"X-Api-Key": "k-123", "X-Tenant": "soc"})
    safe = audit_safe("assistant", cfg)
    assert safe["api_key"] == "***"
    # The names of the headers are useful, their values are credentials
    assert safe["headers"] == {"X-Api-Key": "***", "X-Tenant": "***"}
    assert "k-123" not in str(safe) and "sk-secret" not in str(safe)
    assert safe["model"] == "m" and safe["base_url"] == "http://ollama.internal:11434"


# --------------------------------------------------------------------------- regular expressions

def test_the_address_pattern_matches_in_one_way_only():
    assert EMAIL_RE.match("soc@example.com")
    assert EMAIL_RE.match("a.b+c@mail.corp.example.org")
    for bad in ("no-at-sign", "a@b", "a@@b.c", "a b@example.com", "a@example..com", "@example.com"):
        assert not EMAIL_RE.match(bad), bad
    # The input that made the previous pattern quadratic is matched (and refused) without backtracking
    hostile = "!@" + "!." * 4000
    assert not EMAIL_RE.match(hostile)
