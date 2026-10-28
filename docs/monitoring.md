# Alerts and notifications

[← Documentation](README.md) · [RefExposer](../README.md)

## Alerts (in the application)

**Alerts** is the list of what needs a look, newest first, reachable from the bell in the header and from the
menu. It gathers two things:

| Kind | Raised when |
|---|---|
| **Watchdog** | A [scheduled investigation](assistant.md#watchdogs-scheduled-investigations) found something, or could not finish. |
| **Update** | The last update failed, was rejected by the validation, or found a corrupted source. |
| **Quality** | The published version does not pass its [quality rules](data-governance.md#data-quality). |
| **Stale** | No new version within the expected delay (`max_age`, or twice the schedule interval). |
| **Drift** | The last version changed the volume by more than 20 % on a referential that had enough rows for the ratio to mean something. A drop counts for more than growth. |

Clicking an entry opens what it takes to act on it: the **report the watchdog wrote** (its Markdown table of
affected items, the queries it went through, whether each notification was delivered), the **log of the failed
update**, the **failing quality rules** with their pass rates, or the **figures** of the drift — and a link to
the referential or the watchdog.

- **Nothing to acknowledge, nothing to clean up.** The list is built from the state of the referentials and the
  recorded watchdog runs, so an alert disappears on its own once the thing behind it is fixed, and it can never
  disagree with what the rest of the interface shows.
- **Filtered by what you may see**: a referential you cannot read is not there, a watchdog belongs to its owner
  (administrators see every one), and ordinary accounts get no watchdog alerts at all.
- The **badge on the bell** counts what arrived since you last opened the page; that mark is kept in your
  browser, so it is per person and per device, and nothing about it is stored on the server.
- This is the **in-application** view. Pushing the same events out by e-mail or webhook is
  [notification channels](#notifications-e-mail-webhooks), configured per channel; the two are independent.

API: `GET /api/alerts?limit=&kind=` (`watchdog`, `failure`, `quality`, `stale`, `drift`), `GET /api/alerts/{id}`.

## Notifications (e-mail, webhooks)

**Administration › Notifications** sends the events of the referentials to channels:

| Event | When |
|---|---|
| `failure` | an update ends in error, with a corrupted source or a rejected version (the published version is kept) |
| `recovered` | first successful update after a failure |
| `published` | a new version is published |
| `quality` | a new version is published with quality rules not met (severity `warning`), the previous one meeting them |

- **No flood**: a referential failing at every scheduled update is notified once, when it starts failing, then at its recovery. Tick *Every failed update* to be told each time.
- **Filters**: every channel chooses its events and its referentials (ids and / or categories; all when empty).
- **E-mail**: SMTP server in **Settings › E-mail** (STARTTLS, TLS or an internal relay, authentication, company certificate authorities, test button). Recipients, plus optionally the `owner` of the referential when it is an e-mail address. The message gives the error, the state before, a link to the referential and the last lines of the log.
- **Webhook**: `POST` of a JSON document. The generic document holds `event`, `text` (a ready-made sentence, read as is by Slack, Mattermost, Rocket.Chat, Google Chat and the classic Teams incoming webhooks; Teams Workflows expect an Adaptive Card template), `referential` (id, name, category, owner, url), `run` (status, message, trigger, dates, rows) and `previous_status`. A **template** can shape the body for another tool, with `{{placeholders}}` between quotes, e.g. Discord: `{"content": "{{text}}"}`.
- **Credentials and signature**: header values may be `${secret:…}` references (restricted to their hosts, see [Secret manager](operations.md#secret-manager)). Literal values are masked by the API. With a signing secret, the body is signed: `X-RefExposer-Signature: sha256=<HMAC-SHA256 of the body>`. `X-RefExposer-Event` gives the event.
- **Reliability**: deliveries run in the background (an update never waits for them), with 3 attempts (after 2 s, then 8 s). The 500 last deliveries and their result are listed on the page (`data/.notifications.jsonl`). The send button of a channel sends a test at once.
- API: `/api/admin/notifications` (channels, `POST /{id}/test`, `GET /deliveries`), `PUT /api/admin/settings/smtp`, `POST /api/admin/settings/smtp/test`.

## Outgoing connections

**Administration › Outgoing connections** lists every URL the referentials contact, **grouped by host**: an API called by several referentials appears once, with all of them.

- **Per host**: referentials, URLs, estimated **calls per day** (from the schedules), authentication used (header names, Basic, Git token, `${secret:…}` names: never the values), last contact and volume downloaded, referentials in error. Hosts reached in plain `http` are flagged.
- **Per URL**: every referential that uses it, its role (source, delta of an incremental update, Git repository, online enrichment of a Bloom filter at lookup time), its schedule and its last contact (`HTTP 304` when unchanged). A URL **downloaded by several referentials** is marked *shared*.
- **Safe to show**: credentials in URLs are masked (`user:password@`, query parameters such as `api_key`, `token`, `secret`…).
- **Other connections of the instance**: notification webhooks, SMTP, syslog, LDAP, OpenID Connect, outgoing proxy.
- **Export CSV**: one line per host (allowlist of a firewall or a proxy), or one line per URL and referential. API: `GET /api/admin/endpoints`, `GET /api/admin/endpoints/export?hosts_only=true`; MCP tool `list_endpoints`.
