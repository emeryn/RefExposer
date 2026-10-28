# Enterprise security

[← Documentation](README.md) · [RefExposer](../README.md)

This section lists the security mechanisms in place, by area. The linked sections give the details.

## Identity and access

| Area | Mechanism |
|---|---|
| Identity sources | Local accounts (can be disabled for **single sign-on only**, `REFEX_LOCAL_LOGIN=false`), **LDAP / Active Directory** and **OpenID Connect**. LDAP uses LDAPS or StartTLS; the certificate is checked against the company CAs and user input is escaped in the LDAP filters. OpenID Connect uses the authorization code flow with **PKCE (S256)**, `state` and `nonce`. The ID token signature, issuer, audience and expiry are checked, only asymmetric algorithms are accepted, and the return path after sign-in is restricted to the application (no open redirect). See [LDAP directory, OpenID Connect and access approval](users-and-rights.md#ldap-directory-openid-connect-and-access-approval). |
| Approval | Every account coming from LDAP or OpenID Connect is created **pending**. Nobody gets in until an administrator approves the account. |
| Least privilege | Three roles: administrator, advanced user, user. Rights are given to users or groups (read / manage) on **one referential, a whole category, or all of them**; a category right is resolved at each request from the current category of each referential. A referential that is not granted is invisible: it is missing from the lists and the search, and answers `404`, like an unknown one. See [Users and rights](users-and-rights.md#users-and-rights). |
| SQL console | Reserved to administrators and advanced users. Each set of rights gets its own **sandbox**: only the granted tables, read-only, a single `SELECT` per query, no external access, a locked configuration and a time limit. See [SQL console security](api.md#sql-console-security). |
| Watchdogs | Off unless the agent mode is on. A watchdog is confined to the referentials it names: the scope travels **HMAC-signed** with every tool call and `CurrentUser.level()` returns 0 outside it. It runs with the rights of its own account, through a session opened for the run and closed after it, and the tools that change something are off whatever the assistant settings allow. An answer that is not the expected structured verdict is recorded as an error, never as an all-clear. Every run is audited (`watchdog.run`). See [Watchdogs: scheduled investigations](assistant.md#watchdogs-scheduled-investigations). |
| Assistant (LLM) | Off by default. Every tool call goes through the REST API **with the session of the person chatting**: no elevation, so the model can never read or do what that account could not. Confidential referentials are added to the `denied` set of the account for the whole call, checked **before the role**, which covers the rows, the catalog and the SQL sandbox alike; the agent also refuses a call naming one. The internal header that applies the restriction is HMAC-signed, so a browser cannot forge it. Model output is rendered as React elements (no HTML, no remote images), tool results are passed as data and never as instructions, generation is rate limited per account and every action is audited with `via: agent`. See [In-application assistant](assistant.md#in-application-assistant). |
| Service accounts | Accounts for tools, which **cannot sign in to the interface**. They only use API tokens created by an administrator, and they cannot create their own. See [Service accounts](users-and-rights.md#service-accounts). |
| API tokens | 256 random bits, shown once and stored as **SHA3-256** fingerprints. They can expire (a maximum can be enforced with `REFEX_API_TOKEN_MAX_DAYS`), are revocable at any time, and carry exactly the rights of their owner. They are sent with `Authorization: Bearer`, or with HTTP Basic (token as password) for tools that only support it. |
| Two-factor authentication | Authenticator app (**TOTP**, RFC 6238) and **security keys / passkeys** (**WebAuthn / FIDO2**) for local and LDAP accounts. It is optional, or required for everybody or for some groups. TOTP codes cannot be replayed, the signature counter of keys is checked, recovery codes are one-time, and the session opens only after the second factor. See [Two-factor authentication](users-and-rights.md#two-factor-authentication). |
| Passwords | **Argon2id** (RFC 9106). Policy: 12 characters minimum, 3 kinds of characters, not containing the username. Lockout after repeated failures, and the same response time whether the username exists or not. The initial administrator must change the password at first sign-in. See [Security](users-and-rights.md#security). |
| Sessions | Stored server side. The cookie is `HttpOnly`, `SameSite=Lax`, limited to `/api`, and `Secure` behind HTTPS. Expiration is sliding. **CSRF** protection: changing requests need the `X-Requested-With` header. Changing a password closes the other sessions. |

## Data protection

| Area | Mechanism |
|---|---|
| In transit | HTTPS terminated by the reverse proxy: the [nginx example](deployment.md#expose-behind-an-nginx-reverse-proxy) gives TLS 1.2/1.3 with the hybrid post-quantum key exchange **X25519MLKEM768** (OpenSSL 3.5+), HSTS and an HTTP → HTTPS redirect. Connections to sources, Git servers, LDAP and syslog verify certificates against public CAs and the company CAs. |
| Secrets in the database | LDAP, OpenID Connect and proxy secrets and the syslog client key are encrypted with **AES-256-GCM**, and are never sent back by the API. |
| Keys | Derived with **HKDF-SHA256** from `REFEX_SECRET_KEY`, one key per use and per referential. The backend refuses to start with a key shorter than 32 characters. |
| Confidential referentials | Data **encrypted at rest**: Parquet files (AES-GCM), internal rows (AES-256-GCM), keys of these rows (HMAC fingerprint) and column profiles. Source files and generated downloads are never kept on disk. See [Confidential referentials](data-governance.md#confidential-referentials). |
| Connectors to a model | The API key of a saved connector, and the values of its extra headers (where a gateway key routinely lives), are kept in **one AES-256-GCM blob bound to that connector**. No endpoint returns them — the API answers whether a key is set and the header *names* — and the audit log records that a key was given, never its value. See [Saved connectors](assistant.md#saved-connectors-profiles). |
| Personal data | The columns named in `pii` are **masked by the DuckDB view of the referential**, so every read is masked: rows, lookups, exports, statistics, search, diffs, generated downloads, the SQL console, the MCP servers and the assistant. Revealing them needs the right (`reveal_groups` / `reveal_users`, or administrator) **and** an explicit `reveal=true`, which is audited (`pii.reveal`); the assistant and the MCP servers can never reveal. A unified referential reads its sources masked. See [Personal data: column masking](data-governance.md#personal-data-column-masking). |
| Source credentials | API keys, license keys and Git tokens (HTTP headers, `basic_auth`, `token`) reference the secret manager (`${secret:…}`: encrypted, write-only, optionally restricted to their hosts) or `${REFEX_SOURCE_…}` environment variables, so they stay out of the definitions. Only this prefix of variables is expanded, so other secrets can never be sent to a source. Credentials are masked in the API answers. |

## Integrity of the published data

| Area | Mechanism |
|---|---|
| Corrupted sources | An empty file, an HTML error page, a broken archive or an unreadable file is refused: the current version is kept and the error is shown on the referential. |
| Validation | Optional checks on the minimum row count, the maximum drop compared with the previous version, and the uniqueness of the key. A MaxMind DB is refused if its type changes or if it is older than the published one. |
| Safe publication | New files are built in a staging area, then swapped in atomically. The previous version is kept, and a version can be frozen. |
| Archives | Paths escaping the destination folder (*zip slip*) are refused. |
| Downloads | **SHA-256** fingerprint of every file (`X-Checksum-SHA256`, `ETag`), stable URLs. |

## Operations

| Area | Mechanism |
|---|---|
| Scheduled checks | Integrity of the published data, stale referentials, source availability and certificate expiry. Warnings go to the audit log and syslog. See [System tasks](operations.md#system-tasks). |
| Account hygiene | Inactive accounts (reported or disabled), expired API tokens, reminders of pending access requests, LDAP resynchronisation. |
| Configuration backups | Daily, encrypted (AES-256-GCM), with retention, and restorable from the interface. |

## Traceability

| Area | Mechanism |
|---|---|
| Audit log | Records, with user, IP and details:<br>• sign-ins (successful or not, with their source: local, LDAP or OIDC);<br>• access approvals and refusals;<br>• administration of accounts, groups and rights;<br>• settings changes, tokens;<br>• referential administration, updates, manual imports, row edits;<br>• exports, downloads and SQL queries.<br>The keys of confidential referentials are never written to it. |
| SIEM forwarding | Logs and audit trail in **syslog RFC 5424**, over UDP, TCP or **TLS**. TLS supports a private root CA and mutual TLS. Audit events are sent as structured data. See [Syslog forwarding](operations.md#syslog-forwarding-rfc-5424). |
| Log level | `REFEX_LOG_LEVEL`, from `trace` to `critical`. |

## Exposure

- **Containers without privileges**: no long-running container runs as root. They have a read-only root filesystem, no Linux capabilities and `no-new-privileges`, and the application code cannot be modified by the account running it. See [Containers](install.md#containers).
- **Single entry point**: only the `frontend` container publishes a port; the backend and PostgreSQL stay on the internal Docker network. Behind a reverse proxy, bind it to the loopback (`FRONTEND_PORT=127.0.0.1:8080`).
- **Reverse proxy** ([nginx example](deployment.md#expose-behind-an-nginx-reverse-proxy)):
  - a strict **Content-Security-Policy**, HSTS, `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`;
  - **rate limiting** on the sign-in and the second factor (10 requests per minute per IP) and on the API;
  - client IP forwarded without trusting the client (`X-Forwarded-For` replaced), so the audit log records the real address.

## Supply chain

The dependencies, the images and the Dockerfiles are scanned by one command. Docker is the only prerequisite:
every tool runs in a container, nothing is installed on the host.

```bash
make scan          # or: sh scripts/scan.sh         dependencies + Dockerfiles + images
make scan-deps     # or: sh scripts/scan.sh deps    dependencies only, no image needed
make sbom          # or: sh scripts/sbom.sh         CycloneDX bill of materials of the images
make lock          # or: sh scripts/lock.sh         recompile the Python lock files
```

### Pinned dependencies

Both parts install from a lock file, so two builds of the same commit install the same code:

| Part | Declared in | Installed from | Guarantee |
|---|---|---|---|
| Backend (Python) | `backend/requirements.in` (ranges) | `backend/requirements.txt` — 49 packages pinned with their file hashes | `pip install --require-hashes`: a version or a file that does not match the lock **fails the build** |
| Backend tests | `backend/requirements-dev.in` | `backend/requirements-dev.txt` (56 packages) | same |
| Frontend (JavaScript) | `frontend/package.json` | `frontend/package-lock.json` | `npm ci` |

To change a dependency, edit the `.in` file and run `make lock`; review the diff, then rebuild. `make lock-upgrade`
takes the newest versions the ranges allow. Resolution runs in `python:3.12-slim`, the image the backend is built
from, so the versions and the environment markers match the runtime.

What it runs, and what makes it fail:

| Check | Tool | Fails the scan on |
|---|---|---|
| Python dependencies (`backend/requirements.txt` and `requirements-dev.txt`, pinned) | `pip-audit`, `trivy fs` | any known vulnerability |
| JavaScript dependencies (`frontend/package-lock.json`) | `npm audit --audit-level=high` | high or critical |
| Images built here (backend, frontend): OS and language packages, embedded secrets, misconfiguration | `trivy image` | any **critical**, or a **high that has a fix** |
| Dockerfiles | `trivy config` | high or critical misconfiguration |
| Third-party images (PostgreSQL) | `trivy image` | never: reported only, their content belongs to their publisher |

`make scan` is the same kind of scan a registry runs (Docker Scout, Harbor, Quay): the OS packages of the image
against the vulnerability databases. Two things to know when a registry report and a local one disagree:

- **A pushed image keeps the packages it was built with.** A fix published since then is only in it once it is
  rebuilt and pushed again — which is why a registry can report a vulnerability that a fresh local build does
  not have. `scripts/scan.sh published` scans the images **as they are in the registry**, for exactly that
  comparison.
- **A vulnerability without a published fix cannot fail the scan**: there is nothing to install. It is listed
  in a section of its own, by package, because the way out is usually to stop shipping the package at all —
  see the frontend below — and that never happens if nobody looks at it.

Rebuilding with `make build` (which passes `--pull`) is what applies the fixes: both images install the
security updates of their base image at build time, so the result does not depend on when the base tag was
last rebuilt.

**Fewer packages beats patching them.** The frontend serves static files and proxies `/api/`, which needs
nothing but built-in nginx directives, so it is built on `nginx-unprivileged:*-alpine-slim`: **21 OS packages
instead of 71**, without the dynamic modules (xslt, image-filter, njs) nor `curl`. `libxml2`, `libcurl` and
`brotli` are therefore not in the image, and neither are their advisories — including the ones that have no
fix. The backend keeps Debian: it needs `git` for Git sources, which pulls `libcurl`, `krb5` and `expat` with
it, and its remaining findings wait for Debian.

### Last scan

| Target | Result | Date |
|---|---|---|
| `backend/requirements.txt` (49 pinned) — pip-audit, Trivy | No known vulnerabilities | 2026-10-21 |
| `backend/requirements-dev.txt` (56 pinned) — pip-audit, Trivy | No known vulnerabilities | 2026-10-21 |
| `frontend/package-lock.json` — npm audit | 0 vulnerabilities | 2026-10-21 |
| Dockerfiles — Trivy 0.76.0 `config` | 0 misconfigurations | 2026-10-21 |
| `refexposer-backend` image (Debian 13, 167 OS packages) | **0 critical, 0 fixable high** (49 high / 85 medium / 126 low, none with a fix published), 0 secrets | 2026-10-24 |
| `refexposer-frontend` image (Alpine 3.24.2, **21** OS packages on `-alpine-slim`) | **0 vulnerabilities**, 0 secrets | 2026-10-24 |
| `postgres:17-alpine` (third party, `sha256:b0f9560a…`) | 0 in the Alpine packages; 1 critical + 25 high in the bundled `gosu` binary (Go standard library 1.24.6) | 2026-10-21 |

The `gosu` findings come from the official PostgreSQL image: the binary is statically linked against an older Go
standard library and only runs once, at container start, to drop privileges. There is nothing to patch on this
side; it goes away when the publisher rebuilds the image. The Debian findings of the backend image are packages
Debian marks *no fix available* (`fix_deferred` / `affected`), mostly in libraries the application never calls.

Base images, pinned by tag and recorded by digest at the last scan:

| Image | Digest |
|---|---|
| `python:3.12-slim` | `sha256:a6e34c598f24…` |
| `nginxinc/nginx-unprivileged:1.31-alpine-slim` | `sha256:a8cb0f33d51f…` |
| `postgres:17-alpine` | `sha256:b0f9560a2de0…` |
| `node:22-alpine` (build stage only) | `sha256:0a7108bf6c7b…` |

### Not covered yet

- **Pinning by digest** and an update bot (Renovate, Dependabot, or a periodic review offline): pinning without
  one freezes the vulnerabilities in place, so the two go together.
- **Signed images**: `cosign` signatures and the SBOM attached as an attestation, verified at deployment.
- **CIS benchmark of the base OS** (`oscap-podman` / `oscap-docker`) and a **DAST** pass (OWASP ZAP baseline)
  against the running stack: not run.
- **Vendored code**: none in this repository — every third-party library is a declared dependency, fetched at
  build time and pinned (see [NOTICE](../NOTICE)). A vendored copy would be invisible to the dependency scan, so
  any that appears later is listed there and tracked by hand.

## Code scanning (CodeQL)

GitHub code scanning runs on `main`. Three kinds of finding come out of it, and they are handled differently.

**Fixed in the code**

| Alert | What was done |
|---|---|
| `py/clear-text-logging-sensitive-data` | A settings change is written to the audit log and to syslog through `appsettings.audit_safe()`: the encrypted fields are replaced by `***`, and so are the **values** of the free-form header maps — a gateway key routinely lives in a header. The names are kept, the values never leave the database. |
| `py/polynomial-redos` | `EMAIL_RE` was quadratic on input like `!@!.!.!.…` because the dot was inside the character classes. Rewritten so each part matches in exactly one way, with bounded label lengths, and the recipient of a test e-mail is capped at 320 characters. |
| `py/insecure-protocol` | `ssl.create_default_context()` already refuses TLS 1.0 and 1.1 on this runtime; `minimum_version = TLSv1_2` is now set explicitly, in the certificate-expiry task and in the outgoing HTTP client, so it does not depend on a default. |
| `py/reflective-xss` | The documentation page accepted any identifier `str.isalnum()` liked, which includes letters of other scripts. It is now an ASCII pattern, URL-encoded into the specification link and HTML-escaped into the title: nothing from the query string can become markup. |
| `py/url-redirection` | `oidc.safe_next()` parses the path instead of pattern-matching it and rebuilds it from its parts, so a scheme or a host anywhere in it is dropped. |
| `py/incomplete-url-substring-sanitization` (×2) | Test-only: an assertion searched for a URL inside an exported CSV. It now compares the first column of each line, which is what the test actually means. |

**Hardened, but the alert stands by design** — `py/full-ssrf` on `POST /api/admin/settings/proxy/test` and on the
federation catalog fetch. Both exist in order to call an address an administrator typed; no allowlist can make
that go away, and an administrator can already point a referential source anywhere. Each is reserved to
administrators (the router carries the dependency), and the proxy test returns the status code and the elapsed
time, never the body, so it is a reachability probe and not a way of reading a page.

Three things remove what would turn such a request into an escalation:

- `network.validate_outbound()` on every address a request supplies — the proxy test, the address of a peer and
  the folder of a bulk discovery scan: **http(s) only**, no credentials in the address, a host present, and any
  host resolving to a **link-local address refused**, the cloud metadata services among them (they answer with
  the credentials of the instance).
- **Redirects are not followed** on those calls. Checking the address is worth nothing if a `301` can then send
  the request somewhere else — and the federation call carries the API token of a service account on the peer,
  which a redirect would hand to whoever answers next. The sources keep following redirects, because a mirror
  or a signed link redirects routinely. A discovery scan does follow them (a mirror sends `/folder` to
  `/folder/`) and checks the address that actually answered.
- Private and loopback addresses stay allowed: an inference server, a syslog collector or a peer on the
  internal network is the normal case. Resolution is opportunistic, because a name reached through a proxy is
  resolved by the proxy; this is therefore **not** a defence against DNS rebinding.

These two alerts come back **with a new number** every time the surrounding code moves, because the fingerprint
of an alert includes its neighbourhood. A dismissal applies to one instance: expect to dismiss them again after
a refactoring of `settings_api.py` or `federation.py`, and check the location rather than the number.

**Accepted** — `py/stack-trace-exposure` on the syslog test. The endpoint is admin-only and its whole purpose is
to report why a collector cannot be reached, step by step; the text comes from `str(exception)`, not from a
traceback. Dismiss it as intended behaviour rather than hiding the message.

## Left to the operator

These points depend on the hosting and are not handled by RefExposer:

- **Backups**: the PostgreSQL volume, the `data/` folder (configuration backups included, `data/.backups`), and **`REFEX_SECRET_KEY`**. Back up the key separately from the data: without it, encrypted secrets and the rows of confidential internal referentials are lost.
- **Key storage**: set `REFEX_SECRET_KEY` from the environment or a secret manager. Without it, the generated key is stored in `data/.secret_key`, next to the data it protects.
- **Images**: keep the base images up to date (`docker compose build --pull`). Outside `docker compose`, give `data/` and `import/` to the account running the backend (see [Containers](install.md#containers)).
- **Administrators are trusted**: a source URL or an SQL transformation makes the server reach the network and read files, like a configuration file would. Only give the administrator role to people who need it.
- **Reverse proxy**: HTTPS, the security headers and rate limiting are provided by the reverse proxy in front of RefExposer (see [Expose behind an nginx reverse proxy](deployment.md#expose-behind-an-nginx-reverse-proxy)).
- **Memory and temporary files**: data is in clear in memory while it is used, and DuckDB may write temporary files during very large sorts (`data/.tmp`).
- **Monitoring**: `GET /api/health` (no authentication) for liveness probes. The audit trail can be forwarded to a SIEM.
