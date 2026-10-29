# RefExposer documentation

[RefExposer](../README.md) · [Changelog](../CHANGELOG.md) · [Contributing](../CONTRIBUTING.md) · [Security policy](../SECURITY.md)

Everything about running RefExposer, declaring referentials and opening them to your users. Start with
[Getting started](install.md); the rest is read when you need it.

## Run it

| Page | What is in it |
|---|---|
| [Getting started](install.md) | Requirements, folders, `.env`, first start, first sign-in, first referential. Updating, backing up, and what the containers run as. |
| [Deployment](deployment.md) | Behind an nginx reverse proxy (HTTPS, headers, rate limits), rootless Podman with Quadlet units, and the public URL. |
| [Configuration](configuration.md) | Every environment variable, with its default and what it changes. |
| [Architecture](architecture.md) | What happens end to end, the components, the layout of `data/`, where each piece of data really lives, and the update cycle. |

## Put data in

| Page | What is in it |
|---|---|
| [Declaring a referential](referentials.md) | The 4-step editor, bulk discovery of a Git repository or an HTTP folder, export and import between instances, the YAML reference, the sync folder, manual imports, deltas merged into a referential, and download retries. |
| [Formats, options and transformations](formats.md) | CSV, JSON, XML, Excel, Parquet and the DuckDB options of each; SQL transformations; how nested JSON and XML shapes are read. |
| [Special and large sources](sources.md) | Bloom filters (CIRCL hashlookup), Git repositories, MaxMind DB / GeoIP, SQLite databases and the NIST NSRL with its SQL deltas, and referentials of hundreds of millions of rows. |
| [Internal and unified referentials](internal-and-unified.md) | Lists maintained inside RefExposer (row editor, write API) and referentials built from other referentials (union, enrich, or a query of the SQL console kept as one). |

## Open it to your users

| Page | What is in it |
|---|---|
| [Users, rights and authentication](users-and-rights.md) | Roles, rights per referential and per category, service accounts for tools, LDAP / Active Directory, OpenID Connect, access approval, two-factor authentication, and the security of the sign-in itself. |
| [Search, downloads and the API](api.md) | Searching a referential, the download formats, the generated REST API of each referential, the SQL console sandbox, and the two MCP servers. |
| [Governance, confidentiality and quality](data-governance.md) | Visibility and ownership, referentials encrypted at rest, masking personal data columns, quality rules and the quality gate, and time travel between published versions. |
| [Relational search (investigations)](investigations.md) | *Experimental, off by default.* Starting from any value and following it across the catalogue: entity types recognised from the values, the graph of what a value touches, the bounds of a walk, and saved searches shared with accounts or groups. |

## Watch it

| Page | What is in it |
|---|---|
| [Alerts and notifications](monitoring.md) | The alert feed and what counts as one, e-mail and webhook channels, and every outgoing connection the referentials make. |
| [Assistant and watchdogs](assistant.md) | The in-application chatbot on a model you host, agent mode and its limits, saved connectors, small models, and scheduled investigations. |
| [Operations](operations.md) | The secret manager, branding, replication between instances, system tasks and configuration backups, log levels and syslog forwarding. |
| [Enterprise security](security.md) | What the application does for identity, data protection, integrity, operations, traceability and exposure — and what is left to the operator. |

## Work on it

| Page | What is in it |
|---|---|
| [Development and contributing](development.md) | Running the backend and the frontend locally, the test suite, how to contribute, the licence, and what may come next. |
| [Building from company repositories](../enterprise/README.md) | Internal registry, PyPI mirror, npm registry, TLS interception, and a build with nothing reachable at all. |
