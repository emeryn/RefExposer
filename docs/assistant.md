# Assistant and watchdogs

[← Documentation](README.md) · [RefExposer](../README.md)

## In-application assistant

A chatbot on a page of its own (**Assistant** in the menu), backed by **a model you host**: Ollama, or anything
speaking the OpenAI API (vLLM, llama.cpp server, LocalAI, TGI). Disabled by default; the menu entry only appears
once an administrator enabled it in **Settings › Assistant**.

In **agent mode** it uses the application for the person who is chatting: it lists the referentials, queries them,
runs SQL, hands over a query to open in the SQL console, produces a JSON or CSV file, and — when writes are
allowed — starts an update or creates a referential. The same tools as the [MCP servers](api.md#mcp-data-server-every-user),
driven from the chat instead of an external client.

### Rights: the session of the user, never more

Every tool call goes through the **REST API in-process, carrying the authentication of the person chatting**: the
same session, the same per-referential grants, the same validations, the same audit log. There is no elevation
anywhere.

- A user who cannot see referential *B* cannot query it from the chat: the API answers `404`, exactly as it would
  in the interface, and the model is told it has no access.
- An administrator can create a referential from the chat; a reader asking for the same thing gets a refusal.
- Write tools are offered only when **Allow actions that change something** is on, and they still obey the rights
  of the account.

### Confidential referentials

**Confidential referentials may be queried** is off by default. While it is off, nothing of a confidential
referential reaches the model — not its rows, not its columns, not its name:

- the identifiers of every confidential referential are put in the `denied` set of the account for the whole tool
  call, and `CurrentUser.level()` checks that set **before the role**, so an administrator is restricted too;
- every read goes through it: `/rows`, `/lookup`, `/export`, `/download`, `/search`, `/facets`, the catalog, and
  the tables registered in the **SQL sandbox** — a query naming a confidential table finds no such table;
- the agent refuses a tool call naming one before making it, and filters what the administration endpoints list.

It is an authorization rule, not a prompt instruction: a model that tries anyway gets nothing.

### Saved connectors (profiles)

A **connector** is a saved way of reaching a model: the server, the API, the model, the generation parameters
and whether it is a small model. Save as many as you like and switch with one click — a 1B model on a
workstation to try something, a 70B on the GPU server for the real work, a gateway for a hosted one. The
assistant, the agent and the watchdogs all follow **the one in use**: they read it on every call, so switching
needs no restart and interrupts nothing.

| Action | What it does |
|---|---|
| **Use this one** | Makes it the connector in use. Exactly one ever is |
| **Test** | Asks the server what models it serves, and says whether the chosen one is among them. Works on a connector that is *not* in use, which is the point: check it before switching |
| **Duplicate** | A copy to try a variant (another model on the same gateway). It carries the secrets, so there is no key to retype |
| **Delete** | Deleting the one in use hands over to another, so the assistant is never left without a connector |

**Secrets.** The API key and the values of the extra headers — where a gateway key routinely lives — are kept
in **one AES-256-GCM blob bound to the connector**, like the secret manager. No endpoint ever returns them:
the API answers whether a key is set, and the *names* of the headers. The interface edits a connector without
ever holding its secrets, so an untouched header row is sent back masked and keeps its stored value. Nothing
of them reaches the audit log either — a creation records `api_key: true`, not the key.

API: `GET|POST /api/admin/llm-profiles`, `GET|PATCH|DELETE /api/admin/llm-profiles/{id}`,
`POST /api/admin/llm-profiles/{id}/{activate,duplicate,test}`.

### Small models (1B, 3B)

A connector can declare that **the model is a small one**. Agent mode then stops asking of it what it cannot
do, which is a different thing from asking it nicely:

| | Normal | Small model |
|---|---|---|
| Tools offered | all the account may use (up to ~25 with writes) | **5 read-only ones** (`list_referentials`, `get_referential`, `query_rows`, `lookup`, `search`), plus `run_sql` and `sql_schema` when the account may use SQL |
| Tool calls per step | as many as the model asks for | **one**: the rest of a burst is dropped, with the first result to work from |
| Steps before concluding | up to the configured maximum | **4**, or the configured maximum when it is lower |
| Result handed back | up to 20 000 characters | **2 000** |
| `limit` of a tool call | what the model asks for | **capped at 10**, and added when the model forgot it |
| System prompt | the full one | a **short** one, one instruction per line |
| A call written as text | ignored | **run anyway** |

That last one matters in practice: a small model often *prints* `{"name": "query_rows", "arguments": {…}}`
instead of using the tool-call field of the API. It is parsed and executed — but only for a tool the model was
actually offered, so the fallback is not a way around the short list.

**What it will not do.** No writing, no handing you a file, no building a query for you: those tools are not
offered, because choosing between twenty tools is exactly what a model this size gets wrong. It answers
questions over one or two referentials, and it says so in the interface when the box is ticked.

### Configuration (Settings › Assistant)

What is left in the settings is **policy**, which does not change when you switch model:

| Setting | What it does |
|---|---|
| Offer the assistant | Off: no Assistant entry in the menu, and its API answers that it is unavailable |
| Agent mode, steps, writes | Whether it may use the application, how many tool calls per answer, and whether it may change anything |
| Scheduled watchdogs | Off by default, administrators and advanced users only ([Watchdogs](#watchdogs-scheduled-investigations)) |
| Confidential referentials | Above |
| Groups allowed, accounts allowed | Who may use it: these groups and these named accounts. Everyone when both are empty; administrators always; service accounts never (no interface session) |
| System prompt | Visible and editable, with **Load the default** and **Back to default**. A small model uses a short prompt of its own unless one is set here |
| Messages kept | How much of the conversation goes into the prompt |
| Questions per minute, retention | Per-account rate limit, and how long conversations are kept (purged by the `assistant_retention` task) |

The browser never calls the inference server: RefExposer is its only client, so the server does not have to be
reachable from the workstations. An instance configured before the connectors existed keeps working: its
connection becomes a connector named *Default* at the next start, and the settings remain the fallback until
one is saved.

### What the user sees

The conversation is kept server-side and **belongs to one account**: someone else's conversation is reported as
unknown. It goes on until the user deletes it (one conversation, or all of them), so several questions in a row
build on each other. Answers stream in as they are written, each tool call shows up as a line (tool, arguments,
result), and the answer is rendered as Markdown built into React elements — no HTML, no remote images, links
with their real target.

API: `GET /api/assistant` (availability), `/api/assistant/conversations` (list, create, delete all),
`/api/assistant/conversations/{id}` (read, rename, delete), `POST /api/assistant/conversations/{id}/messages`
(server-sent events: `token`, `step`, `done`), `POST /api/assistant/test` (administrators).

## Watchdogs: scheduled investigations

A **watchdog** is the assistant asking itself the same question again and again, over a **fixed set of
referentials**, and telling someone **only when it finds something**.

They are **off by default**: an administrator turns them on in *Settings › Assistant* (**Scheduled
watchdogs**), which needs the agent mode. They are also **reserved to administrators and advanced users** —
for anyone else the menu is not there and the whole API answers 403. Turning the switch off removes every
schedule at once, and a trigger that slips through is refused rather than run.

### How one is defined

| Field | What it does |
|---|---|
| Name, description | What this watchdog is for. |
| **Referentials** | The **only** ones it may read. Chosen explicitly — there is no "everything". |
| **Trigger** | `cron` (a crontab expression, or one of the usual schedules) or `on_sync`: right after one of its referentials publishes a new version. |
| **Prompt** | The investigation itself. |
| **Recipients** | E-mail addresses, and/or the [notification channels](monitoring.md#notifications-e-mail-webhooks) already configured. |
| **Severity floor** | A finding below it is recorded, but nobody is woken up. |

### Three things it cannot do

- **Leave its scope.** The list of referentials travels **signed** with every tool call, and
  `CurrentUser.level()` returns 0 for anything outside it. The model can ask for another referential; it does
  not exist for the call.
- **Exceed its account.** It runs with the rights of the account it belongs to, through a session opened for
  the run and closed after it, so every tool call goes through the same checks as a click in the interface.
  Creating a watchdog is refused when that account cannot read a referential of the scope.
- **Write.** The tools that change something are off for a watchdog, whatever *Allow writes* permits in the
  chat.

### What it answers

The model is given a prompt of its own and must return a structured verdict:

```json
{"finding_detected": true, "severity": "HIGH", "summary": "3 machines are not in the CMDB",
 "details_markdown": "| host | ip | seen |\n| --- | --- | --- |\n…", "affected_items_count": 3}
```

An answer that is **not** that JSON is recorded as an **error**, with the text kept — never as "nothing to
report". Silence is never mistaken for an all-clear.

### Ready-made investigations

The editor offers three templates (plus a free one), pre-filling the prompt and the description:

- **Shadow IT hunt (CMDB vs infrastructure / network)** — assets alive on the network or the hypervisor that
  the CMDB does not know, or that carry no EDR agent.
- **External exposure drift (EASM)** — new sub-domains, newly exposed addresses or sensitive services, using
  the diff with the last snapshot when the referential keeps them.
- **IAM audit & orphan accounts** — the HR referential crossed with the active accounts (AD, VPN,
  applications): accounts still open for someone who has left.

### Following them

Findings also land in [Alerts](monitoring.md#alerts-in-the-application), with the report attached. Each watchdog lists its
**executions**: date, what started it (`cron`, `on_sync:<referential>`, `manual`),
*nothing to report* / *finding* with its severity / *failed*, the report the agent wrote, the tool calls it
went through, and whether each notification was delivered. **Run now** starts one on the spot (in the
background: an investigation calls the model several times). The 100 most recent runs of a watchdog are kept.
Every run is audited (`watchdog.run`).

API: `GET|POST /api/watchdogs`, `GET|PATCH|DELETE /api/watchdogs/{id}`, `POST /api/watchdogs/{id}/run`,
`GET /api/watchdogs/{id}/runs`, `GET /api/watchdogs/runs`, `GET /api/watchdogs/templates`.
