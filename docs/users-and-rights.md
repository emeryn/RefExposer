# Users and rights

[← Documentation](README.md) · [RefExposer](../README.md)

## Roles

| Role | Can… |
|---|---|
| **Administrator** | everything: all referentials, users, groups, rights, settings, audit log, System page, configuration reload |
| **Advanced user** | the referentials granted to them, plus the SQL console and creating referentials (internal, or a query of the console kept as one: they get the manage right on the ones they create) |
| **User** | only the referentials granted to them, directly or through their groups (no SQL console) |

## Rights per referential

A right links a **user or a group** to a **scope**, with a level:

| Scope | Covers |
|---|---|
| One referential | that referential |
| **A category** | every referential of that category, **now and as they are added to it** |
| `*` | every referential |

| Level | Allows |
|---|---|
| **Read** | see the status, explore, search, lookup, export, download, query in SQL (administrators and advanced users) |
| **Manage** | read + start, force or cancel an update, manual imports, editing internal referentials |

The effective right is the highest of the direct rights and those of the groups. A referential that is not granted is **invisible**: absent from the lists, the global search and the SQL console, with a `404` answer when it is requested directly.

### Rights on a whole category

Categories group the referentials in the interface; a right can be given on one, which is the way to hand a set
of referentials to a team in one row instead of a dozen:

```bash
curl -X POST -H "$H" -H "Content-Type: application/json" "http://localhost:8080/api/admin/grants"   -d '{"category": "Threat intelligence", "group_id": 3, "level": "read"}'
```

The scope picker of the rights editor (a user's page, a group's page, the **Access** tab of a referential) offers
the three forms in one list: *All referentials*, *By category* (with how many each holds) and *One referential*.

- **Resolved at every request**, against the categories the referentials have at that moment: a referential added
  to the category is covered without touching the rights, and one **moved out of it stops being**. Changing the
  category of a referential therefore changes who may read it — it is a rights change made from the referential
  editor, and it is in the audit log as a `referential.update`.
- A right on a category of a referential appears on its **Access** tab marked `category`, read-only there: it is
  edited from the user or the group it was given to, like a `*` right.
- The **highest** level wins, so a direct `manage` on one referential sits on top of a `read` given on its
  category.
- A category nothing is in yet is accepted: the right waits for its first referential rather than being refused.
- Stored as `category:<name>` in the scope of the grant, carried by the configuration export and by the MCP tool
  `grant_access` (`category` argument).

Rights are managed in the interface (**Administration › Users / Groups**, or the **Access** tab of a referential) or with `/api/admin/grants`.

## Service accounts

For tools and scripts (SIEM, enrichment, CI…), an administrator creates a **service account** in **Administration › Users › New service account** (or `POST /api/admin/service-accounts`):

- it **never signs in to the interface** (no password, no session): it only calls the API with **tokens created by an administrator** from its page (`POST /api/admin/users/{id}/tokens`), shown once and revocable at any time;
- it lists and queries the referentials it is granted, **directly or through its groups**, exactly like a user (role *user*: no SQL console, no administration);
- **advanced rights** can be ticked when the tool needs to write (an integration pushing its own data): it then queries with SQL and creates referentials — an internal one, or a query kept as one — and gets the manage right on what it creates. It can never administrate;
- it cannot create or revoke its own tokens; disabling the account cuts all its tokens at once. The last use of its tokens is shown in the user list.

```bash
curl -H "Authorization: Bearer rfx_…" "http://localhost:8080/api/referentials/cisa-kev/lookup/CVE-2021-44228"
```

## LDAP directory, OpenID Connect and access approval

**Administration › Settings** connects:

- **an LDAP directory or Active Directory**.
  - Service account, user search base and filter (OpenLDAP and AD presets provided), attributes.
  - Groups read from `memberOf` or by a group search, filtered by a regular expression.
  - LDAPS or StartTLS.
  - The username typed on the sign-in page is first looked up among the local accounts, then in the directory.
- **an OpenID Connect provider (Keycloak…)**.
  - *Authorization code* flow with PKCE, state and nonce. The signature of the id_token is checked against the JWKS.
  - Claims are configurable, including `realm_access.roles` for Keycloak roles.
  - The **redirect URL** to declare in Keycloak is shown in the page.
  - An *internal discovery URL* is used when the backend does not reach Keycloak through the same address as the browsers.

Each section has a **Test** button giving a step-by-step diagnosis. The directory can also be tested with a given account: the DN found, the attributes and the groups are then shown.

**Mandatory approval**: the first sign-in of a person through LDAP or OIDC creates a **pending** account.

- They see a waiting page that refreshes by itself. Administrators see the request (badge in the menu, notice on the dashboard and on the Users page).
- **Approve**: the admin chooses the role and optional local groups.
- **Refuse**: sign-in is then refused; the decision can be reversed.

**Groups**: directory or OIDC groups are created automatically in RefExposer, and their members are synchronized at each sign-in.

- They receive rights on referentials like a local group.
- Their members cannot be edited by hand. An external user can however be added to extra local groups.
- An optional **administrators group** gives the admin role to its members, still after approval.

External accounts have no password in RefExposer. A local account with the same username takes precedence.

**Network**: the HTTP/HTTPS proxy (with credentials and exceptions) and the extra certificate authorities (PEM, e.g. a TLS-inspecting proxy) are used for downloads and OIDC calls. The CAs are also used for LDAPS. Without a proxy set in the interface, the `HTTP_PROXY` / `HTTPS_PROXY` variables of the container apply.

**Secrets**: the LDAP service account password, the OIDC client secret and the proxy password are encrypted in the database with **AES-256-GCM**.

- The key is derived from `REFEX_SECRET_KEY` (at least 32 characters), or generated in `data/.secret_key` when the variable is absent: **back up this file**.
- These secrets are never sent back to the interface. A field left empty keeps the saved value.

## Two-factor authentication

**Local and LDAP accounts** can confirm their sign-in after the password with a second factor, from **My account › Two-factor authentication**. Two methods are available, and an account can use both:

- **Authenticator app** (TOTP, RFC 6238): Google Authenticator, Microsoft Authenticator, FreeOTP, 1Password… The account scans a QR code, then types a first code to confirm.
- **Security keys and passkeys** (WebAuthn / FIDO2): YubiKey, Windows Hello, Touch ID, a phone… An account can register several. Browsers only allow them over HTTPS (or on `localhost`), at the address set in `REFEX_PUBLIC_URL`.

Each account also gets **10 one-time recovery codes** with its first second factor. They are shown once and can be regenerated.

**Policy** (**Administration › Settings › Two-factor**):

- **optional** (default), **required for some groups**, or **required for everybody**;
- when it is required, an account without a second factor enrols one at its next sign-in;
- if a phone or key is lost, **Administration › Users** resets every second factor of the account.

Not concerned:

- OpenID Connect accounts: the identity provider handles their second factor;
- service accounts: they only use API tokens;
- API tokens in general.

The feature is off when single sign-on is the only way to sign in (`REFEX_LOCAL_LOGIN=false` and no LDAP directory).

Security:

- The session is only opened after the second factor: the password step returns a short-lived (5 min), encrypted challenge.
- A TOTP code is accepted once (replay protection), with one 30-second step of tolerance.
- The signature counter of security keys is checked, which detects cloned keys.
- Wrong codes count towards the account lockout, like wrong passwords.
- TOTP secrets are encrypted at rest; recovery codes are stored as fingerprints.

## Single sign-on only

`REFEX_LOCAL_LOGIN=false` disables username / password sign-in for **local accounts** in the interface:

- the sign-in page only offers the configured single sign-on: the OpenID Connect button, and the form when the LDAP directory is enabled;
- sessions already opened by local accounts are no longer accepted;
- API tokens keep working, including those of local accounts and of [service accounts](#service-accounts).

Configure single sign-on first, and make sure an administrator can sign in with it (administrators group of the directory or of OpenID Connect, or approval of the account), then set `REFEX_LOCAL_LOGIN=false` and restart the backend. If nobody can sign in any more, set it back to `true` and restart. The backend logs a warning at startup when local sign-in is disabled and no single sign-on is enabled.

## Initial administrator and recovery

At startup, if **no active administrator** exists, the `REFEX_ADMIN_USERNAME` / `REFEX_ADMIN_PASSWORD` account is created. Its password must be changed at first sign-in (`REFEX_ADMIN_MUST_CHANGE_PASSWORD=true`). If `REFEX_ADMIN_PASSWORD` is empty, a random password is generated and printed in the backend logs.

Lost access: set `REFEX_ADMIN_RESET_PASSWORD=true` with a new `REFEX_ADMIN_PASSWORD`, restart the backend (`docker compose up -d backend`), then set the variable back to `false`. The account is reactivated and unlocked, and its sessions are closed.

## Security

- **Passwords** hashed with **Argon2id** (RFC 9106: 64 MiB, 3 iterations, parallelism 4, 256-bit digest). The parameters can be tuned (`REFEX_ARGON2_*`) and hashes are upgraded at the next sign-in.
  - There is no standardized “post-quantum” password hashing algorithm. Post-quantum algorithms (ML-KEM, ML-DSA) are for key exchange and signatures.
  - Against a hash, a quantum computer only brings Grover's quadratic speed-up. A 256-bit digest keeps 128 bits of security in this model, and the memory cost of Argon2id strongly penalizes this kind of massively parallel attack.
- **Password policy**: 12 characters minimum (`REFEX_PASSWORD_MIN_LENGTH`), 3 kinds of characters, not containing the username.
- **Lockout** after 10 failures for 15 min (`REFEX_LOGIN_MAX_ATTEMPTS`, `REFEX_LOGIN_LOCKOUT_MINUTES`). The response time is the same whether the username exists or not.
- **Server-side sessions**: `HttpOnly`, `SameSite=Lax` cookie limited to `/api`, sliding expiration (`REFEX_SESSION_TTL_HOURS`). Tokens are stored as SHA3-256 fingerprints. Changing requests require the `X-Requested-With: RefExposer` header (CSRF protection). Changing a password closes the other sessions.
- **Personal API tokens** (`rfx_…`, 256 random bits), shown once, stored as SHA3-256, with optional expiration (a maximum can be enforced with `REFEX_API_TOKEN_MAX_DAYS`). They carry exactly the rights of their owner.
- **Sandboxed SQL console**: each set of rights has its own DuckDB connection, which only sees the allowed tables. Only the Parquet files of these tables can be read (`allowed_paths`), which also blocks bypasses such as `read_parquet('/data/…')`.
- **Audit log**: sign-ins (successful or not, with the source: local, LDAP, OIDC), access requests, approvals and refusals, settings changes, password changes, tokens, administration of accounts, rights and referentials, updates, manual imports, internal row edits, exports, downloads and SQL queries. The user, IP and details are recorded.
- Source credentials (`source.headers`, `basic_auth`, Git `token`) are never returned by the API (masked for administrators too). They should reference a secret of the [secret manager](operations.md#secret-manager) (`${secret:…}`: encrypted, optionally restricted to its hosts) or a `${REFEX_SOURCE_…}` environment variable instead of being stored in the definitions.
- Behind HTTPS, cookies are `Secure` as soon as `REFEX_PUBLIC_URL` starts with `https://`.
