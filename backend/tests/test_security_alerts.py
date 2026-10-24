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
