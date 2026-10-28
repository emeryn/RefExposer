# Security policy

## Reporting a vulnerability

**Please do not open a public issue.**

Use GitHub's private reporting form: **Security › Advisories › Report a vulnerability** on
<https://github.com/emeryn/RefExposer/security/advisories/new>. It is private between you and the
maintainers, and it is the only channel — no address is published here, so nothing to keep up to date and
nothing to scrape.

Helpful in a report:

- what an attacker gets out of it, and what they need to start (an account? which role? a token?);
- the smallest sequence of requests that shows it, and the version (`GET /api/system`);
- anything about the deployment that matters (behind a proxy, OIDC, confidential referentials, the assistant).

You can expect an acknowledgement within a few days and, if the finding holds, a fix and a published advisory
crediting you unless you ask otherwise. This is a project maintained on spare time: there is no paid support
and no promised deadline, and saying so plainly seems better than a service level nobody can hold to.

## Supported versions

The latest release on `main` is the one that gets fixes. There is no long-term support branch.

## Scope

In scope — anything that lets someone:

- read or change a referential their account may not (the authorization layer, the SQL sandbox, the MCP
  servers, the assistant and the watchdogs all go through the same checks, and a way around any of them is a
  finding);
- see the personal data columns a referential masks without the right and the explicit request;
- reach a confidential referential's data, or the keys protecting it;
- sign in as someone else, keep a session they should have lost, or escalate their role;
- make the instance reach a host it was not pointed at (SSRF), run code, or read files outside its data
  directory;
- recover a secret of the secret manager, an API token, or a credential of a source.

Out of scope:

- anything that needs an administrator account to begin with, where an administrator is *meant* to be able to
  do it. RefExposer lets an administrator point a source at an arbitrary URL, write SQL and configure an
  inference server: that is the job, not a vulnerability. The two `py/full-ssrf` findings in
  [Code scanning](docs/security.md#code-scanning-codeql) are documented for exactly that reason.
- missing hardening that the operator is responsible for and the README says so: HTTPS and HSTS at the reverse
  proxy, rate limits, the database password, where `REFEX_SECRET_KEY` lives.
- denial of service through sheer volume (a query over billions of rows is slow; there are timeouts and limits
  for it, and raising them is a choice).
- reports from a scanner with no demonstrated impact.

## What the project does on its own

Every push is scanned (CodeQL for the code, the dependency scan and SBOM of `scripts/`), the Python
dependencies are pinned with hashes, and the images run rootless with a read-only root filesystem. What is
covered, what is hardened but flagged by design, and what is left to the operator is written down in
[Enterprise security](docs/security.md).
