"""Git repository sources: files of a repository on GitHub, GitLab, Gitea or any Git server over HTTPS.

- Only the files wanted are downloaded: shallow (one commit), partial (blobs on demand) and sparse fetch.
- `git ls-remote` first: when the branch / tag still points to the published commit, nothing is fetched.
- Private repositories: an access token, sent as HTTP Basic credentials (username "oauth2" by default, accepted
  by GitHub, GitLab and Gitea). The token is given to git through environment variables (never in the URL nor
  on the command line) and is masked in every message.
- The proxy and the company certificate authorities of the network settings apply. Only https is allowed by
  default (REFEX_GIT_PROTOCOLS): no ssh, file or ext transports, no submodules, no prompts.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from .config import SourceConfig, expand_env
from .settings import Settings

SHA_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


class GitError(Exception):
    pass


def _token(src: SourceConfig) -> str | None:
    return expand_env(src.token, src.repository).strip() or None if src.token else None


def _scrub(text: str, secrets_: list[str]) -> str:
    for s in secrets_:
        if s:
            text = text.replace(s, "***")
    return re.sub(r"(Authorization: Basic )\S+", r"\1***", text, flags=re.I)


class _Git:
    """git commands with a controlled configuration (no user / system config, no prompt)."""

    def __init__(self, settings: Settings, src: SourceConfig):
        from .network import current_proxy

        self.settings, self.src = settings, src
        self.token = _token(src)
        self.secrets: list[str] = []
        self._tmp = tempfile.TemporaryDirectory(prefix="refex-git-")
        config: list[tuple[str, str]] = [("protocol.allow", "never"), ("submodule.recurse", "false"),
                                         ("core.hooksPath", os.devnull), ("credential.helper", "")]
        for proto in (p.strip() for p in settings.git_protocols.split(",") if p.strip()):
            config.append((f"protocol.{proto}.allow", "always"))
        if self.token:
            basic = base64.b64encode(f"{src.username or 'oauth2'}:{self.token}".encode()).decode()
            config.append(("http.extraHeader", f"Authorization: Basic {basic}"))
            self.secrets += [self.token, basic]
        proxy = current_proxy()
        if proxy.https_proxy or proxy.http_proxy:
            from .network import _with_credentials

            url = _with_credentials(proxy.https_proxy or proxy.http_proxy, proxy.username, proxy.password)
            config.append(("http.proxy", url))
            if proxy.password:
                self.secrets.append(proxy.password)
        verify = settings.verify is not False and proxy.verify_tls
        if not verify:
            config.append(("http.sslVerify", "false"))
        else:
            cas = []
            if isinstance(settings.verify, str):
                cas.append(Path(settings.verify).read_text(encoding="utf-8"))
            if proxy.ca_bundle:
                cas.append(proxy.ca_bundle)
            if cas:  # public CAs plus the company ones
                import certifi

                bundle = Path(self._tmp.name) / "ca.pem"
                bundle.write_text(Path(certifi.where()).read_text(encoding="utf-8") + "\n" + "\n".join(cas), encoding="utf-8")
                config.append(("http.sslCAInfo", str(bundle)))
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": self._tmp.name,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_COUNT": str(len(config)),
            **{k: v for i, (key, value) in enumerate(config) for k, v in ((f"GIT_CONFIG_KEY_{i}", key), (f"GIT_CONFIG_VALUE_{i}", value))},
        }
        for var in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy"):
            if var in os.environ and not (proxy.https_proxy or proxy.http_proxy):
                self.env[var] = os.environ[var]

    def run(self, *args: str, cwd: Path | None = None) -> str:
        try:
            r = subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True, text=True,
                               timeout=self.settings.git_timeout)
        except FileNotFoundError as e:
            raise GitError("git is not installed in this image") from e
        except subprocess.TimeoutExpired as e:
            raise GitError(f"git {args[0]}: no answer after {self.settings.git_timeout}s") from e
        if r.returncode != 0:
            msg = _scrub((r.stderr or r.stdout).strip(), self.secrets)
            if re.search(r"Authentication failed|could not read Username|terminal prompts disabled|403|401", msg):
                msg += " (private repository: check the access token and its read permission)"
            raise GitError(f"git {args[0]}: {msg[-800:]}")
        return r.stdout

    def close(self) -> None:
        self._tmp.cleanup()


def remote_commit(settings: Settings, src: SourceConfig) -> str | None:
    """Commit the branch / tag currently points to (None for a commit id, or a reference not found)."""
    ref = src.ref or "HEAD"
    if SHA_RE.match(ref):
        return ref
    git = _Git(settings, src)
    try:
        out = git.run("ls-remote", src.repository or "", ref)
    finally:
        git.close()
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        if name in (ref, f"refs/heads/{ref}", f"refs/tags/{ref}^{{}}", f"refs/tags/{ref}"):
            return sha
    return None


def fetch(settings: Settings, src: SourceConfig, dest: Path, log: Callable[[str], None] = lambda m: None,
          paths: list[str] | None = None) -> tuple[str, list[Path]]:
    """Fetch the files matching `src.path` (or the exact `paths`) at `src.ref` into `dest`. Returns (commit, files)."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    git = _Git(settings, src)
    ref = src.ref or "HEAD"
    patterns = ["/" + _glob_escape(p.lstrip("/")) for p in paths] if paths else ["/" + (src.path or "").lstrip("/")]
    try:
        git.run("init", "-q", str(dest))
        git.run("remote", "add", "origin", src.repository or "", cwd=dest)
        git.run("sparse-checkout", "set", "--no-cone", *patterns, cwd=dest)
        git.run("fetch", "-q", "--depth", "1", "--filter=blob:none", "--no-tags", "origin", ref, cwd=dest)
        git.run("checkout", "-q", "FETCH_HEAD", cwd=dest)
        commit = git.run("rev-parse", "FETCH_HEAD", cwd=dest).strip()
    finally:
        git.close()
    shutil.rmtree(dest / ".git", ignore_errors=True)  # history not kept: the next update fetches again
    if paths:
        files = sorted(dest / p.lstrip("/") for p in paths if (dest / p.lstrip("/")).is_file())
    else:
        files = sorted(p for p in dest.glob((src.path or "").lstrip("/")) if p.is_file())
    log(f"Git: {src.repository} @ {ref} = commit {commit[:12]}, {len(files)} file(s) matching '{src.path}'")
    return commit, files


def _glob_escape(path: str) -> str:
    """Exact path in a sparse-checkout pattern (gitignore syntax)."""
    return re.sub(r"([*?\[\]!#\\])", r"\\\1", path)


def list_files(settings: Settings, src: SourceConfig) -> tuple[str, list[str]]:
    """Paths of every file of the repository at `src.ref`, without downloading their content (trees only)."""
    git = _Git(settings, src)
    ref = src.ref or "HEAD"
    with tempfile.TemporaryDirectory(prefix="refex-git-list-") as tmp:
        try:
            git.run("init", "-q", "--bare", tmp)
            git.run("remote", "add", "origin", src.repository or "", cwd=Path(tmp))
            git.run("fetch", "-q", "--depth", "1", "--filter=blob:none", "--no-tags", "origin", ref, cwd=Path(tmp))
            commit = git.run("rev-parse", "FETCH_HEAD", cwd=Path(tmp)).strip()
            out = git.run("ls-tree", "-r", "-z", "--name-only", "FETCH_HEAD", cwd=Path(tmp))
        finally:
            git.close()
    return commit, sorted(p for p in out.split("\0") if p)
