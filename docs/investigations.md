# Relational search (investigations)

[← Documentation](README.md) · [RefExposer](../README.md)

A referential answers *is this value in here?*. **Investigate** answers the question behind it: *what does this
value touch?* — the referentials that carry it, the rows that prove it, and what those rows lead to.

It is a **superset of the global search**, not a narrower mode beside it. Anything the global search finds, an
investigation finds; what it adds is the relations around it.

> **Experimental feature, off by default.** Turn it on in **Settings › Experimental** (`relational_search`).
> It is complete and tested, but young: its shape may still change, and what a deep walk costs depends on the
> catalogue it runs against — so try it where you can watch it before opening it to everyone. While it is off
> it is not merely hidden: the menu entry and the route are gone, every endpoint below answers `404`, and the
> assistant is not offered its tools.

## Why the type of a value, and not the name of a column

The same identifier is named differently by every source. In a typical catalogue a CVE is `cveID` in the CISA
KEV, `cve` in the EPSS scores, `id` in the NVD feed and in the GitHub advisories. Matching columns by name
therefore finds nothing, and declaring every pair by hand would mean configuring the whole catalogue before
seeing a single relation.

So a column is typed by **what its values look like**: `CVE-2024-3400` is a CVE wherever it is stored. The
recognition costs nothing — it reads the `min` and the `max` the [column profile](referentials.md) already
recorded, two real values of that column, with no scan.

| Entity type | Recognised as | Typical columns |
|---|---|---|
| `cve` | `CVE-2024-3400` | `cveID`, `cve`, `id` |
| `cwe` | `CWE-77` (or a bare number on a column named for it) | `weakness`, `cwe_id` |
| `capec` | `CAPEC-66` | `capec` |
| `attack_technique` | `T1190`, `T1059.001` | `technique` |
| `cpe` | `cpe:2.3:…`, `cpe:/…` | `platform`, `cpeName` |
| `cidr` | `10.0.0.0/8`, `2001:db8::/32` | `ip_prefix`, `network` |
| `ip` | IPv4 and IPv6 addresses | `ip`, `rdata`, `address` |
| `asn` | `AS13335` (or a bare number on a column named for it) | `asn` |
| `sha256`, `sha1`, `md5` | a hash of the right length | `sha256`, `hash` |
| `email` | an address | `email`, `contact` |
| `url` | `http(s)://…` | `reference`, `link` |
| `domain` | a host or domain name | `rrname`, `domain`, `host` |
| `mac` | `aa:bb:cc:dd:ee:ff` | `mac` |

Two rules keep this from claiming columns it should not:

- **Every** sample has to be covered. A column whose min is a sentence and whose max happens to look like a
  domain is left alone, so free text, product names (`PAN-OS`), country codes and numbers are never typed.
- A column **may hold several types**, and then each value is typed by what that value is: the `rdata` of a
  passive DNS carries addresses *and* host names, and each row lands on the right kind of node.

`GET /api/investigate/entities` lists what the instance recognises and which column holds each type — the
quickest way to see what you can pivot on.

### Correcting it

`entities` in a definition overrides the recognition for one column, for what a pattern gets wrong or cannot
see — values stored without the prefix of their type, most often:

```yaml
- id: cwec-latest
  key: ID
  entities:
    ID: cwe              # the column holds 77, not CWE-77
    Related_Weaknesses: cwe
    Description: ""      # take a column out of the relational search entirely
```

Setting a column to `""` is about **typing**: the column stops being something to pivot on or to read a
neighbour from. It does not hide the referential from the text pass, which is the global search and has rules
of its own — `scope` is what keeps a referential out of an investigation entirely.

A **confidential** referential is never typed automatically: its profiling deliberately keeps no values. Give
it an `entities` mapping if you want it in the exact pass; it is then read, like everywhere else, only by
accounts that may read it.

## Two ways in

`match` says how the value is looked for, and `auto` — the default — does both:

| `match` | What it does |
|---|---|
| `auto` | both passes below: whatever is recognised is looked up exactly, and the text is searched anyway |
| `exact` | typed lookups only, on the columns holding that kind of identifier. Refused for a value that is not an identifier |
| `text` | a text search only, like the global search |

**The exact pass** applies when the value is a recognised identifier: every column of that type, in every
referential you may read, is looked up for it. Precise, and it uses the sorted and indexed columns.

**The text pass** applies to anything at all. A vendor, a product, a binary name, a label — what is not an
identifier is never refused: the text of each referential is searched for it, and the identifiers sitting in
the matching rows become the first hop of the graph. So free text is a *door into* the relations rather than a
dead end: searching `Palo Alto` reaches the CVEs of that vendor, and from there everything tied to them.

It is built on the same `TableQuery` as the global search, with one deliberate difference: where
`/api/search` narrows to the key of a referential that names no `search_columns` — a sensible saving for a
fan-out across the whole catalogue — an investigation searches **every text column**. A vendor sitting in
`vendorProject` would otherwise be invisible, and a vendor is exactly the sort of thing one investigates.

Both passes also run for an identifier, which is the point: a CVE *mentioned* in an incident note or in the
references of another advisory is a relation no exact lookup would find. A referential where the seed already
matched exactly is not text-searched for it as well — same rows, counted twice.

## What a walk does

1. The value is recognised, or you pick the type yourself, or it is simply searched as text.
2. The passes above find the rows that match.
3. Each matching row is evidence: it puts the value in that referential, and every *other* typed column of
   that same row is a neighbour — a CPE, an address, a URL, a hash.
4. With `depth` 2 or 3, those neighbours are looked up in turn. That is what turns a flat multi-referential
   search into a graph: from a CVE to the products it affects, the exploits that target it and the techniques
   they use.

Past the seed the walk follows identifiers **exactly**. Text searching every value found along the way would
cost a full pass per value and drown the graph in coincidences.

`scope` narrows the walk to some referentials or to `category:<name>`, and `follow` to some entity types.

## Several values, and what ties them together

`values` takes up to eight seeds. The walk is the same, run from each of them, and **every node remembers
which seeds reached it** — so a node reached from more than one is a **meeting point**, and the shortest path
from each seed to it is the link between them.

`connections` reports one link per pair of seeds, shortest first: the value they meet on, how many hops, and
the whole chain seed to seed. Each hop names the **referentials whose rows justify it**, so a link is never
asserted without something to read behind it:

```
CVE-2024-3400  --exploits-->  T1190  --exploits-->  CVE-2021-44228
```

`shared` lists every meeting point, not only the closest one of each pair.

**A shared referential is not a link.** Two values both appearing in the EPSS feed says nothing — every CVE is
in it, so routing a link through a referential would tie any two CVEs together and report noise as a finding.
A link runs through shared *values*, over the `same_row` edges only. The `found_in` edges stay in the graph,
because they are how you see where a value lives, but they are not a path.

Two seeds only meet where their expansions overlap, so **depth matters**: at depth 1 they meet only if their
own rows already name the same thing. When nothing is found and a greater depth could help, the answer says so
in `warnings` rather than implying there is no link.

### The graph

A **circle** is a value, a **square** a referential, a **diamond** the free text you searched for. A dashed
line puts the value *in* that referential; a solid arrow means two values **share a row** of it — and that row
is shown as the evidence. A referential is a node of its own on purpose: a value that leads nowhere still has
to show *where it is*.

With several seeds, the **ring** around a node is the seed that reached it, and a **yellow double ring** marks
a meeting point — a value several seeds reach.

The **spacing control** holds the nodes further apart or closer together, live: a rich catalogue draws a
crowded graph and no single constant suits every one of them. It only re-runs the layout, never the query. On
a large graph it is applied when the slider is released rather than while it is dragged, because the
simulation is quadratic in the number of nodes.

**Hiding a kind of element**: clicking an entry of the legend takes that kind out of the picture — the
referentials, the URLs, whatever is crowding it. Hidden nodes leave the *layout* too, so what is left actually
spreads out instead of keeping the holes. Seeds are always shown, and a line under the graph says how many
nodes are hidden, so a filtered picture is never mistaken for the whole one.

**Enlarging it**: the graph takes the whole window, with the rows of the selected node in a pane beside it
rather than in a drawer over the picture they describe. Several seeds are pinned around the centre — two of
them read left and right, with what ties them together in between — and a node only one seed reached starts
on that seed's side, so the picture settles into one cluster per value.

## Carrying on from what you see

A graph is a question you did not finish asking, so it can be **grown in place** rather than started again:

- **Expand from here**, on any value of the graph, adds what that value touches and keeps everything already
  on screen. A value whose type the graph knows is looked up exactly, which is the cheap and precise path.
- **Any value of any row** can be expanded too: click a row in the panel and every one of its values is
  offered. This is how you follow something no pattern recognises — a product name, a ticket, a label — since
  such a value is searched as text.

Merging is safe because a node identifier is derived from the value and nothing else (`<entity>:<value>`), so
the same value is the same node in two separate answers and nothing is ever drawn twice. Two things are
deliberately **not** taken from an expansion: a node already on screen keeps its *distance from the original
question*, and a node found by expanding inherits the *seeds* of the place it was expanded from — so an
expansion can never invent a link between your values.

Chains between seeds are worked out at search time, by one rule in one place. When an expansion turns up a new
value that several seeds reach, the page says so and offers to search again rather than working a chain out
from a second rule that could drift from the first.

Every hit says **how** it was found: `exact` on a named typed column, or `text` when the rows merely mention
the value. An exact match is never passed off as a mention, or the other way round.

### Bounds, and saying so

The fan-out of a popular value is unbounded by nature, so every walk is bounded — nodes, rows per lookup,
queries and wall clock. What a bound cut off is reported in `warnings` and the answer is marked `truncated`,
so a partial result never passes for a complete one. A referential too large to be read whole for a column
that is neither sorted nor indexed is skipped and says why: set `storage.sort_by` or `storage.indexes` on that
column to bring it in (see [Large volumes](referentials.md)).

## Rights and personal data

Nothing is re-implemented here:

- Only referentials **you** may read are visited, and an investigation never reveals that a referential exists
  by refusing differently.
- Personal data columns are **always masked** in an investigation, even for an account allowed to reveal them
  on `/rows`. There is no `reveal` on this endpoint.

## Saved searches

A saved search holds the **question** — the value, its type, the depth, the scope — not its result. Replaying
it reads the catalogue as it is *then*, so a referential added last week is in the next replay.

Sharing one gives access to that question and to nothing else: **whoever replays it uses their own rights**. A
colleague with narrower grants sees fewer referentials through the very same search, and an author with broad
grants never widens anybody. Two levels: `read` replays it, `edit` also changes and reshares it; deleting
stays with the owner.

Who you may share with is deliberately narrow for an ordinary account: **your own groups, and the people in
them**. Signing in is not a reason to hand the whole account directory to everyone, and a colleague you work
with is a colleague you share a group with. An administrator sees every account and every group.

## From the assistant

In agent mode the assistant has `investigate` and `entity_types`, so a question such as *what are we exposed to
through CVE-2024-3400?* — or *what do we know about this vendor?* — becomes one call instead of a search and a
lookup per referential, and the answer carries the rows it is built on. *Is there any link between this address
and that CVE?* is the same call with two `values`, and the answer is the chain of evidence or nothing. Because the tool takes plain text as
well, the model never has to decide first whether what the user typed is an identifier. It also has `list_saved_searches` and `run_saved_search`, which run with the
rights of the account the conversation belongs to.

## Turning it on

**Settings › Experimental** holds the switch (`PUT /api/admin/settings/experimental` with
`{"relational_search": true}`, administrators only). `GET /api/investigate/status` answers for every account,
enabled or not, which is what the interface uses to decide whether to show the feature at all.

Turning it back off hides it everywhere at once and changes nothing else: **saved searches are kept**, not
deleted, and they are all there again the next time it is on.

## API

| Call | What it does |
|---|---|
| `GET /api/investigate/status` | whether the feature is enabled here (answered whether it is or not) |
| `GET /api/investigate/entities` | the entity types, and the columns of the catalogue holding each |
| `POST /api/investigate` | `{value \| values, entity?, depth?, scope?, follow?, rows_per_lookup?, max_nodes?, match?}` |
| `GET /api/searches?mine=` | saved searches you own or had shared with you |
| `POST /api/searches` | `{name, description, spec, tags}` |
| `GET /api/searches/{id}` · `PUT` · `DELETE` | one saved search (`PUT` needs `edit`, `DELETE` the owner) |
| `GET /api/searches/share-targets` | the accounts and groups you may share with |
| `PUT /api/searches/{id}/shares` | `{shares: [{user_id\|group_id, level}]}` — the list replaces what was there |
| `POST /api/searches/{id}/run` | replay it with your rights |
