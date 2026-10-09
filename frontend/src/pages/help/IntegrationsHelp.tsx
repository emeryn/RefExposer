import { Alert, Anchor, Code, List, Table, Text, Title } from '@mantine/core';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { absoluteUrl } from '../../api/client';
import type { Me } from '../../api/types';

export type Section = { id: string; title: string; admin?: boolean; visible?: (me: Me) => boolean; body: (me: Me) => ReactNode };

const H = ({ children }: { children: ReactNode }) => (
  <Title order={4} mt="lg" mb={6}>
    {children}
  </Title>
);
const P = ({ children }: { children: ReactNode }) => (
  <Text size="sm" mb="sm" maw={860} style={{ lineHeight: 1.6 }}>
    {children}
  </Text>
);
const Shell = ({ children }: { children: ReactNode }) => (
  <Code block mb="sm" maw={860}>
    {children}
  </Code>
);
const Steps = ({ items }: { items: ReactNode[] }) => (
  <List type="ordered" size="sm" spacing={4} maw={860} mb="sm">
    {items.map((it, i) => (
      <List.Item key={i}>{it}</List.Item>
    ))}
  </List>
);
const origin = () => absoluteUrl('');
const api = (path: string) => `${origin()}/api/referentials/${path}`;

const TokenNote = () => (
  <Alert color="blue" maw={860} p="sm" mb="sm">
    <Text size="sm">
      All the examples use the referential <Code>cisa-kev</Code> (key <Code>cveID</Code>): replace it with the identifier shown in the
      address of your referential. The <b>API</b> tab of each referential gives its exact routes, columns and ready-to-copy commands.
      Tokens (<Code>rfx_…</Code>) come from <Anchor component={Link} to="/account" size="sm">My account</Anchor>, or from a{' '}
      <b>service account</b> created by an administrator (recommended for tools).
    </Text>
  </Alert>
);

export const INTEGRATIONS: Section[] = [
  {
    id: 'integrations',
    title: 'Overview',
    body: () => (
      <>
        <P>
          Any tool able to call an HTTP API or to download a file can use the referentials: SOAR and automation (n8n…), SIEM (Splunk,
          Elastic, Graylog…), BI tools, IDS, scripts. Choose the pattern according to the volume and the freshness you need.
        </P>
        <Table fz="sm" maw={860} mb="sm" withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={190}>Need</Table.Th>
              <Table.Th>Route</Table.Th>
              <Table.Th>Typical use</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {[
              ['One value at a time', 'GET /lookup/{value}', 'Workflow step, alert enrichment, ticket. 404 when the value is unknown.'],
              ['Many values at once', 'POST /lookup', 'Up to 10,000 values per request: enrichment of a batch of events, scheduled reports.'],
              ['Filtered rows', 'GET /rows?column=value…', 'Dashboards, searches with filters, sort and pagination.'],
              ['Whole referential', 'GET /download/{csv|json|jsonl|parquet}', 'Local copy: SIEM lookup tables, Elasticsearch index, Excel / Power BI.'],
              ['Raw file', 'GET /raw', 'MaxMind DB (.mmdb) and Bloom filters for Logstash, Suricata, Zeek, Graylog…'],
            ].map(([a, b, c]) => (
              <Table.Tr key={a}>
                <Table.Td fw={600}>{a}</Table.Td>
                <Table.Td>
                  <Code>{b}</Code>
                </Table.Td>
                <Table.Td>{c}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        <H>Authentication</H>
        <P>
          Every call carries an API token, in one of two forms: <Code>Authorization: Bearer rfx_…</Code>, or <b>HTTP Basic</b> with any
          user name and the token as password, for tools that only know a user and a password (Power BI, wget, some SIEM connectors). A
          token has exactly the rights of its owner: give the service account the <b>Read</b> right on the referentials it uses, nothing
          more.
        </P>
        <H>Good practices</H>
        <List size="sm" spacing={4} maw={860} mb="sm">
          <List.Item>
            <b>Prefer batches</b>: one <Code>POST /lookup</Code> of 1,000 values is much faster than 1,000 calls, and stays below the rate
            limits of the reverse proxy.
          </List.Item>
          <List.Item>
            <b>Download only when it changed</b>: downloads and raw files answer <Code>304 Not Modified</Code> to a conditional request
            (<Code>If-None-Match</Code> with the previous <Code>ETag</Code>, or <Code>If-Modified-Since</Code>). With curl:{' '}
            <Code>--etag-compare</Code> / <Code>--etag-save</Code>, or <Code>-z file</Code>.
          </List.Item>
          <List.Item>
            <b>Match the schedule</b>: there is no point in synchronizing every 5 minutes a referential updated every day. Its schedule
            and last update are shown in its header.
          </List.Item>
          <List.Item>
            <b>Certificates</b>: if RefExposer uses a company certificate authority, add it to the trust store of the tool (or of its
            container).
          </List.Item>
        </List>
        <P>
          Machine-readable documentation: the OpenAPI specification of a referential (<Code>/api/referentials/&lt;id&gt;/openapi.json</Code>)
          or of all of them (<Code>/api/catalog/openapi.json</Code>) can be imported into Postman, Insomnia or a client generator.
        </P>
      </>
    ),
  },
  {
    id: 'integrations-scripts',
    title: 'Scripts (Python & Bash)',
    body: () => (
      <>
        <TokenNote />
        <P>
          Two ready-to-use scripts with the same commands: look up a value, look up a list of values, read rows with filters, and
          download a referential only when it changed. Set the token (and the address, if it differs) before using them:
        </P>
        <Shell>{`export REFEX_TOKEN=rfx_…
export REFEX_URL=${origin()}     # optional`}</Shell>
        <Table fz="sm" maw={860} mb="sm" withTableBorder>
          <Table.Tbody>
            {[
              ['lookup cisa-kev CVE-2021-44228', 'The row of one value, as JSON (exit code 1 when unknown).'],
              ['batch cisa-kev cves.txt', 'One value per line in the file: CSV on stdout with value, found, and the columns of the referential.'],
              ['rows cisa-kev vendorProject=Microsoft sort=-dateAdded limit=20', 'Rows with filters, sort and pagination, as JSON Lines.'],
              ['download cisa-kev csv kev.csv', 'The whole referential (csv, csv.gz, json, jsonl, parquet, xlsx), downloaded only when it changed.'],
            ].map(([a, b]) => (
              <Table.Tr key={a}>
                <Table.Td w={360}>
                  <Code>{a}</Code>
                </Table.Td>
                <Table.Td>{b}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        <H>Python (standard library only)</H>
        <Shell>{`#!/usr/bin/env python3
"""Query RefExposer referentials.

  refex.py lookup   <referential> <value>
  refex.py batch    <referential> <file>          one value per line -> CSV on stdout
  refex.py rows     <referential> [column=value ...] [sort=...] [limit=...]
  refex.py download <referential> <format> <file> only when it changed (ETag kept in <file>.etag)
"""
import csv
import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("REFEX_URL", "${origin()}").rstrip("/") + "/api/referentials"
TOKEN = os.environ.get("REFEX_TOKEN") or sys.exit("export REFEX_TOKEN=rfx_...")
BATCH = 10_000  # values per request (API limit)


def call(path, body=None, headers=None):
    h = {"Authorization": f"Bearer {TOKEN}", **(headers or {})}
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    return urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, headers=h), timeout=600)


def q(value):
    return urllib.parse.quote(value, safe="")


def lookup(ref, value):
    try:
        with call(f"/{q(ref)}/lookup/{q(value)}") as r:
            print(json.dumps(json.load(r), indent=2, ensure_ascii=False))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            sys.exit(f"{value}: not found in {ref}")
        raise


def batch(ref, path):
    with open(path, encoding="utf-8") as f:
        values = list(dict.fromkeys(line.strip() for line in f if line.strip()))
    results = {}
    for i in range(0, len(values), BATCH):
        with call(f"/{q(ref)}/lookup", {"values": values[i:i + BATCH]}) as r:
            results.update(json.load(r)["results"])
    columns = list(dict.fromkeys(k for rec in results.values() if rec for k in rec))
    out = csv.writer(sys.stdout)
    out.writerow(["value", "found", *columns])
    for v in values:
        rec = results.get(v)
        out.writerow([v, rec is not None, *("" if rec is None or rec.get(c) is None else
                                            json.dumps(rec[c]) if isinstance(rec[c], (list, dict)) else rec[c]
                                            for c in columns)])
    print(f"{sum(r is not None for r in results.values())} found / {len(values)}", file=sys.stderr)


def rows(ref, *params):
    qs = urllib.parse.urlencode([tuple(p.split("=", 1)) for p in params])
    with call(f"/{q(ref)}/rows?{qs}") as r:
        data = json.load(r)
    for row in data["rows"]:
        print(json.dumps(row, ensure_ascii=False))
    print(f"{len(data['rows'])} row(s), total {data['total']}", file=sys.stderr)


def download(ref, fmt, dest):
    etag_file = dest + ".etag"
    headers = {}
    if os.path.exists(dest) and os.path.exists(etag_file):
        with open(etag_file) as f:
            headers["If-None-Match"] = f.read().strip()
    try:
        with call(f"/{q(ref)}/download/{q(fmt)}", headers=headers) as r, open(dest + ".tmp", "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
            etag = r.headers.get("ETag")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            print(f"{dest}: unchanged", file=sys.stderr)
            return
        raise
    os.replace(dest + ".tmp", dest)
    if etag:
        with open(etag_file, "w") as f:
            f.write(etag)
    print(f"{dest}: downloaded", file=sys.stderr)


COMMANDS = {"lookup": lookup, "batch": batch, "rows": rows, "download": download}

if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    try:
        COMMANDS[sys.argv[1]](*sys.argv[2:])
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")`}</Shell>
        <H>Bash (curl and jq)</H>
        <Shell>{`#!/usr/bin/env bash
# refex.sh lookup   <referential> <value>
# refex.sh batch    <referential> <file>           one value per line (10,000 max) -> CSV on stdout
# refex.sh rows     <referential> [column=value ...] [sort=...] [limit=...]
# refex.sh download <referential> <format> <file>  only when it changed (ETag kept in <file>.etag)
set -euo pipefail
: "\${REFEX_TOKEN:?export REFEX_TOKEN=rfx_...}"
BASE="\${REFEX_URL:-${origin()}}/api/referentials"

api() { curl -fsS -H "Authorization: Bearer $REFEX_TOKEN" "$@"; }
enc() { jq -rn --arg v "$1" '$v | @uri'; }

case "\${1:-}" in
  lookup)
    api "$BASE/$2/lookup/$(enc "$3")" | jq . || { echo "$3: not found in $2 (or error)" >&2; exit 1; } ;;
  batch)
    jq -R 'select(length > 0)' "$3" | jq -s '{values: unique}' |
      api -H "Content-Type: application/json" --data @- "$BASE/$2/lookup" |
      jq -r '.results
        | ([.[] | select(. != null) | keys_unsorted[]] | unique) as $cols
        | (["value", "found"] + $cols),
          (to_entries[] | [.key, (.value != null)]
             + [$cols[] as $c | (.value[$c] // "") | if type == "array" or type == "object" then tojson else . end])
        | @csv' ;;
  rows)
    ref=$2; shift 2
    args=(); for p in "$@"; do args+=(--data-urlencode "$p"); done
    api -G "\${args[@]}" "$BASE/$ref/rows" | jq -c '.rows[]' ;;
  download)
    [ -f "$4" ] || rm -f "$4.etag"   # file deleted: download it again
    api --etag-compare "$4.etag" --etag-save "$4.etag" -o "$4.tmp" "$BASE/$2/download/$3"
    if [ -s "$4.tmp" ]; then mv "$4.tmp" "$4"; echo "$4: downloaded" >&2
    else rm -f "$4.tmp"; echo "$4: unchanged" >&2; fi ;;
  *)
    sed -n '2,5p' "$0" >&2; exit 2 ;;
esac`}</Shell>
        <P>
          The Bash version sends the whole file in one request (10,000 values maximum): split bigger lists with{' '}
          <Code>split -l 10000</Code>. <Code>--etag-compare</Code> needs curl 7.68 or later. Both scripts work with every kind of
          referential: on a Bloom filter or a MaxMind DB, <Code>lookup</Code> and <Code>batch</Code> test values or IP addresses, and{' '}
          <Code>download &lt;id&gt; raw &lt;file&gt;</Code> fetches the raw file.
        </P>
      </>
    ),
  },
  {
    id: 'integrations-n8n',
    title: 'n8n',
    body: () => (
      <>
        <TokenNote />
        <H>1. Credential</H>
        <Steps
          items={[
            <>
              In n8n, <b>Credentials › Add credential › Header Auth</b>.
            </>,
            <>
              Name: <Code>Authorization</Code> — Value: <Code>Bearer rfx_…</Code> (token of a service account).
            </>,
          ]}
        />
        <H>2. Look up one value per item</H>
        <P>
          Add an <b>HTTP Request</b> node: Method <Code>GET</Code>, Authentication <i>Generic Credential Type › Header Auth</i> with the
          credential above, and the URL (expression):
        </P>
        <Shell>{`${api('cisa-kev')}/lookup/{{ encodeURIComponent($json.cve) }}`}</Shell>
        <P>
          The node runs once per item and returns the row as JSON. An unknown value answers <b>404</b>: in the node{' '}
          <b>Settings</b>, set <i>On Error</i> to <i>Continue</i> (or, in the options, <i>Response › Never Error</i>) and test the status
          in an <b>If</b> node, so that a missing value does not stop the workflow.
        </P>
        <H>3. Enrich a whole batch in one call</H>
        <P>
          For many items, send them in one request: an HTTP Request node with Method <Code>POST</Code>, <b>Settings › Execute Once</b>{' '}
          enabled, <i>Send Body</i> as JSON, and this body (expression):
        </P>
        <Shell>{`{{ { "values": $input.all().map(i => i.json.cve) } }}`}</Shell>
        <P>
          The answer gives <Code>found</Code>, <Code>missing</Code> and <Code>results</Code> (one entry per value, <Code>null</Code> when
          absent). A <b>Code</b> node then attaches each result to its item:
        </P>
        <Shell>{`const results = $('HTTP Request').first().json.results;
return $('Previous node').all().map(item => ({
  json: { ...item.json, kev: results[item.json.cve] ?? null },
}));`}</Shell>
        <H>4. Keep a local copy</H>
        <P>
          A <b>Schedule Trigger</b> followed by an HTTP Request on <Code>/download/json</Code> returns the whole referential (one item per
          row with <i>Split Into Items</i>), to write into a database, a spreadsheet or another tool.
        </P>
      </>
    ),
  },
  {
    id: 'integrations-splunk',
    title: 'Splunk',
    body: () => (
      <>
        <TokenNote />
        <P>Two approaches: a CSV lookup table refreshed on a schedule (simple, fast), or an external lookup that queries RefExposer during the search (always up to date).</P>
        <H>1. CSV lookup table, refreshed by cron</H>
        <P>On the search head, a script downloads the referential only when it changed, then replaces the lookup file in one move:</P>
        <Shell>{`#!/bin/sh
# /opt/refexposer/sync-kev.sh — cron: 15 * * * *
set -e
export REFEX_TOKEN=rfx_…
DEST=/opt/splunk/etc/apps/search/lookups/cisa_kev.csv
curl -fsS --etag-compare /opt/refexposer/kev.etag --etag-save /opt/refexposer/kev.etag \\
     -H "Authorization: Bearer $REFEX_TOKEN" -o "$DEST.tmp" \\
     "${api('cisa-kev')}/download/csv"
# 304 Not Modified: nothing received, the current table is kept
if [ -s "$DEST.tmp" ]; then mv "$DEST.tmp" "$DEST"; else rm -f "$DEST.tmp"; fi`}</Shell>
        <P>
          Declare it in <Code>transforms.conf</Code> of the app (or in <i>Settings › Lookups › Lookup definitions</i>), then use it in
          your searches:
        </P>
        <Shell>{`# transforms.conf
[cisa_kev]
filename = cisa_kev.csv

# SPL
index=vuln sourcetype=scanner
| lookup cisa_kev cveID AS cve OUTPUT vendorProject product dateAdded knownRansomwareCampaignUse
| where isnotnull(dateAdded)`}</Shell>
        <P>
          Fine up to a few million rows. Beyond that, CSV downloads are not offered for large referentials: use the external lookup
          below, or the API with filters.
        </P>
        <H>2. External lookup (live)</H>
        <P>
          Splunk passes the values of the search to a script, which looks them up in batches of 10,000 and returns the columns.
          Save it as <Code>bin/refex_lookup.py</Code> in your app, and the token in <Code>local/refexposer.token</Code> (readable by the
          splunk account only):
        </P>
        <Shell>{`#!/usr/bin/env python3
"""Splunk external lookup: refex_lookup.py <referential> <field>"""
import csv, json, os, sys, urllib.request

BASE = "${origin()}"
TOKEN = open(os.path.join(os.path.dirname(__file__), "..", "local", "refexposer.token")).read().strip()

ref, key = sys.argv[1], sys.argv[2]
reader = csv.DictReader(sys.stdin)
rows = list(reader)
values = sorted({r[key] for r in rows if r.get(key)})
found = {}
for i in range(0, len(values), 10000):
    req = urllib.request.Request(
        f"{BASE}/api/referentials/{ref}/lookup",
        data=json.dumps({"values": values[i:i + 10000], "column": key}).encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        found.update(json.load(resp)["results"])

writer = csv.DictWriter(sys.stdout, fieldnames=reader.fieldnames)
writer.writeheader()
for r in rows:
    rec = found.get(r.get(key)) or {}
    for f in reader.fieldnames:
        if f != key and rec.get(f) is not None:
            r[f] = rec[f]
    writer.writerow(r)`}</Shell>
        <Shell>{`# transforms.conf — the field names are the column names of the referential
[refex_kev]
external_cmd = refex_lookup.py cisa-kev cveID
fields_list = cveID, vendorProject, product, dateAdded
python.version = python3

# SPL
... | lookup refex_kev cveID AS cve OUTPUT vendorProject product dateAdded`}</Shell>
        <H>3. GeoIP with your own MaxMind DB</H>
        <P>
          If RefExposer publishes a MaxMind DB, download its raw file (<Code>/raw</Code>, see <i>Other tools</i>) to{' '}
          <Code>$SPLUNK_HOME/share/GeoLite2-City.mmdb</Code>, or set <Code>db_path</Code> in the <Code>[iplocation]</Code> stanza of{' '}
          <Code>limits.conf</Code>: the <Code>iplocation</Code> command then uses it.
        </P>
      </>
    ),
  },
  {
    id: 'integrations-elastic',
    title: 'Elastic (Elasticsearch, Logstash)',
    body: () => (
      <>
        <TokenNote />
        <H>1. Index a referential with Logstash</H>
        <P>
          The JSON download is an array of rows: the <Code>http_poller</Code> input turns it into one event per row. The key is used as
          document id, so a new version updates the documents in place.
        </P>
        <Shell>{`input {
  http_poller {
    urls => {
      kev => {
        method => get
        url => "${api('cisa-kev')}/download/json"
        headers => { "Authorization" => "Bearer \${REFEX_TOKEN}" }
      }
    }
    schedule => { cron => "15 * * * *" }
    codec => "json"
    request_timeout => 600
  }
}
output {
  elasticsearch {
    hosts => ["https://elasticsearch:9200"]
    index => "refex-cisa-kev"
    document_id => "%{cveID}"
  }
}`}</Shell>
        <P>
          <Code>{'${REFEX_TOKEN}'}</Code> is read from the environment or the Logstash keystore (
          <Code>bin/logstash-keystore add REFEX_TOKEN</Code>). Rows removed from the referential stay in the index: recreate the index
          from time to time, or index into a dated index behind an alias.
        </P>
        <H>2. Enrich events at ingest time (enrich processor)</H>
        <Shell>{`PUT /_enrich/policy/cisa-kev
{
  "match": {
    "indices": "refex-cisa-kev",
    "match_field": "cveID",
    "enrich_fields": ["vendorProject", "product", "dateAdded", "knownRansomwareCampaignUse"]
  }
}

POST /_enrich/policy/cisa-kev/_execute

PUT /_ingest/pipeline/kev-enrich
{
  "processors": [
    { "enrich": { "policy_name": "cisa-kev", "field": "vulnerability.id",
                  "target_field": "kev", "ignore_missing": true } }
  ]
}`}</Shell>
        <P>
          An enrich policy works on a snapshot: run <Code>_execute</Code> again after each synchronization (e.g. a Watcher, or a cron
          right after the Logstash schedule).
        </P>
        <H>3. Enrich events in Logstash (live lookup)</H>
        <P>For low volumes, the <Code>http</Code> filter queries RefExposer for each event (404 when unknown, tagged <Code>_httprequestfailure</Code>):</P>
        <Shell>{`filter {
  if [vulnerability][id] {
    http {
      url => "${api('cisa-kev')}/lookup/%{[vulnerability][id]}"
      verb => "GET"
      headers => { "Authorization" => "Bearer \${REFEX_TOKEN}" }
      target_body => "[kev]"
    }
  }
}`}</Shell>
        <H>4. Your own GeoIP database</H>
        <P>
          Download the raw MaxMind DB (see <i>Other tools</i>), then point the Logstash <Code>geoip</Code> filter at it (
          <Code>database =&gt; "/etc/logstash/geoip/GeoLite2-City.mmdb"</Code>), or put it in <Code>$ES_PATH_CONF/ingest-geoip/</Code> on
          every ingest node for the Elasticsearch <Code>geoip</Code> processor (<Code>database_file</Code>).
        </P>
      </>
    ),
  },
  {
    id: 'integrations-graylog',
    title: 'Graylog',
    body: () => (
      <>
        <TokenNote />
        <P>Graylog lookup tables query RefExposer directly, with a cache: <b>System › Lookup Tables</b>.</P>
        <H>1. Data adapter</H>
        <Steps
          items={[
            <>
              <b>Data Adapters › Create › HTTP JSONPath</b>.
            </>,
            <>
              Lookup URL: <Code>{`${api('cisa-kev')}/lookup/\${key}`}</Code>
            </>,
            <>
              Single value JSONPath: <Code>$.vendorProject</Code> (the value shown in messages) — Multi value JSONPath: <Code>$</Code> (the
              whole row).
            </>,
            <>
              HTTP Headers: <Code>Authorization</Code> = <Code>Bearer rfx_…</Code>.
            </>,
          ]}
        />
        <H>2. Cache and table</H>
        <Steps
          items={[
            <>
              <b>Caches › Create › Node-local, in-memory cache</b>, e.g. 10,000 entries, expiry after write of 1 hour (according to the
              schedule of the referential).
            </>,
            <>
              <b>Lookup Tables › Create</b> with this adapter and this cache, name <Code>cisa_kev</Code>.
            </>,
          ]}
        />
        <H>3. Use it</H>
        <Shell>{`// Pipeline rule
rule "KEV enrichment"
when
  has_field("cve")
then
  let kev = lookup("cisa_kev", to_string($message.cve));
  set_field("kev_vendor", kev["vendorProject"]);
  set_field("kev_product", kev["product"]);
end`}</Shell>
        <P>
          An unknown value (404) gives an empty result. A MaxMind DB published by RefExposer can feed the <b>Geo IP - MaxMind™ Databases</b>{' '}
          data adapter: download its raw file on the Graylog servers (see <i>Other tools</i>) and give its path.
        </P>
      </>
    ),
  },
  {
    id: 'integrations-sentinel',
    title: 'Microsoft Sentinel',
    body: () => (
      <>
        <TokenNote />
        <Alert color="orange" maw={860} p="sm" mb="sm">
          <Text size="sm">
            Sentinel runs in Azure: its Logic Apps must reach RefExposer. If RefExposer is internal, go through an{' '}
            <b>on-premises data gateway</b>, or run the synchronization script below on a server of your network. KQL{' '}
            <Code>externaldata()</Code> cannot send an <Code>Authorization</Code> header: do not use it with RefExposer.
          </Text>
        </Alert>
        <H>1. Referential as a watchlist</H>
        <P>
          A watchlist is a table that KQL queries and analytics rules can join. A script (scheduled task, Azure Automation runbook…)
          downloads the referential as CSV and replaces the watchlist through the Azure API:
        </P>
        <Shell>{`# PowerShell 7, Az module (Connect-AzAccount, or a managed identity in Azure Automation)
$sub = "<subscription id>"; $rg = "<resource group>"; $ws = "<Log Analytics workspace>"
$csv = (Invoke-WebRequest -Headers @{ Authorization = "Bearer $env:REFEX_TOKEN" } \`
        -Uri "${api('cisa-kev')}/download/csv").Content
$body = @{ properties = @{
    displayName = "CISA KEV"; provider = "RefExposer"; source = "Local file"
    itemsSearchKey = "cveID"; contentType = "text/csv"; numberOfLinesToSkip = 0
    rawContent = $csv } } | ConvertTo-Json -Depth 4
$path = "/subscriptions/$sub/resourceGroups/$rg/providers/Microsoft.OperationalInsights/workspaces/$ws" +
        "/providers/Microsoft.SecurityInsights/watchlists/cisa_kev?api-version=2023-02-01"
Invoke-AzRestMethod -Method PUT -Path $path -Payload $body`}</Shell>
        <Shell>{`// KQL
SecurityAlert
| extend cve = tostring(parse_json(ExtendedProperties).CVE)
| join kind=inner (_GetWatchlist('cisa_kev') | project cveID, vendorProject, product, dateAdded) on $left.cve == $right.cveID`}</Shell>
        <P>
          The upload through the API is limited to about 3.8 MB of CSV: for a bigger referential, export only the useful columns or rows
          (<Code>/export?format=csv&amp;columns=cveID,vendorProject&amp;…</Code>), or use a large watchlist through Azure Storage.
        </P>
        <H>2. Enrich incidents with a playbook</H>
        <Steps
          items={[
            <>
              Logic App with the trigger <b>Microsoft Sentinel incident</b>, then <b>Entities - Get …</b> (IPs, hosts, file hashes…).
            </>,
            <>
              For each entity, an <b>HTTP</b> action: Method <Code>GET</Code>, URI{' '}
              <Code>{`${api('cisa-kev')}/lookup/@{encodeUriComponent(items('For_each')?['Address'])}`}</Code>, header{' '}
              <Code>Authorization</Code> = <Code>Bearer</Code> + the token, read from <b>Azure Key Vault</b> (never written in the
              Logic App).
            </>,
            <>
              In the HTTP action settings, accept the 404 (unknown value): <i>Configure run after</i> on the next step, or a{' '}
              <b>Condition</b> on <Code>outputs('HTTP')['statusCode']</Code>.
            </>,
            <>
              <b>Add comment to incident</b> with the returned row, or <b>Update incident</b> (tags, severity).
            </>,
          ]}
        />
        <P>For several values, one <Code>POST /lookup</Code> with the body <Code>{'{"values": [...]}'}</Code> replaces the loop.</P>
      </>
    ),
  },
  {
    id: 'integrations-qradar',
    title: 'IBM QRadar',
    body: () => (
      <>
        <TokenNote />
        <P>
          QRadar uses <b>reference data</b> in its rules and AQL searches: a <b>reference set</b> for a simple list of values, a{' '}
          <b>reference table</b> for values with columns. A script loads them from RefExposer through the QRadar API (token of an{' '}
          <i>Authorized Service</i> in the <Code>SEC</Code> header).
        </P>
        <H>1. Create the reference table (once)</H>
        <Shell>{`QRADAR=https://qradar.example.com
curl -fsS -X POST -H "SEC: $QRADAR_TOKEN" \\
  "$QRADAR/api/reference_data/tables?name=cisa_kev&element_type=ALN&key_name_types=%5B%7B%22key_name%22%3A%22vendorProject%22%2C%22element_type%22%3A%22ALN%22%7D%2C%7B%22key_name%22%3A%22product%22%2C%22element_type%22%3A%22ALN%22%7D%5D"
# key_name_types = [{"key_name":"vendorProject","element_type":"ALN"},{"key_name":"product","element_type":"ALN"}]`}</Shell>
        <H>2. Load it on a schedule</H>
        <Shell>{`#!/bin/sh
# cron: 20 * * * *  — needs curl and jq
set -e
curl -fsS -H "Authorization: Bearer $REFEX_TOKEN" "${api('cisa-kev')}/download/json" |
  jq 'map({(.cveID): {vendorProject, product}}) | add' > /tmp/kev.json
# Empty the table (keeps its definition), then load the new version
curl -fsS -X DELETE -H "SEC: $QRADAR_TOKEN" "$QRADAR/api/reference_data/tables/cisa_kev?purge_only=true" >/dev/null
curl -fsS -X POST -H "SEC: $QRADAR_TOKEN" -H "Content-Type: application/json" \\
  --data @/tmp/kev.json "$QRADAR/api/reference_data/tables/bulk_load/cisa_kev" >/dev/null`}</Shell>
        <P>
          For a simple list (IOCs, known IP ranges…), a reference set is enough:{' '}
          <Code>jq 'map(.indicator)'</Code> then <Code>POST /api/reference_data/sets/bulk_load/&lt;name&gt;</Code>. Purging then loading
          leaves the table empty for a few seconds: schedule it outside busy hours, or load a second table and switch the rules.
        </P>
        <H>3. Use it</H>
        <Shell>{`-- AQL
SELECT sourceip, "CVE ID",
       REFERENCETABLE('cisa_kev', 'vendorProject', "CVE ID") AS kev_vendor
FROM events
WHERE REFERENCETABLE('cisa_kev', 'vendorProject', "CVE ID") IS NOT NULL
LAST 24 HOURS`}</Shell>
        <P>
          In the rule wizard, the tests <i>when any of these properties are contained in any of these reference set(s)</i> and{' '}
          <i>… reference table(s)</i> use the same data. QRadar 7.5 also offers the newer <Code>/api/reference_data_collections</Code>{' '}
          API: the principle is the same.
        </P>
      </>
    ),
  },
  {
    id: 'integrations-misp',
    title: 'MISP',
    body: () => (
      <>
        <TokenNote />
        <H>1. Referential of indicators as a MISP feed</H>
        <P>
          A referential of IOCs (IP addresses, domains, hashes…) can be read by MISP as a CSV feed: <b>Sync Actions › Feeds › Add Feed</b>.
        </P>
        <Steps
          items={[
            <>
              Source format: <b>Simple CSV Parsed Feed</b> — Input source: <b>Network</b>.
            </>,
            <>
              URL: <Code>{`${api('threatfox')}/export?format=csv&columns=ioc_value`}</Code> (only the column of the values, with filters if
              needed, e.g. <Code>&amp;ioc_type=ip:port</Code>).
            </>,
            <>
              Headers: <Code>Authorization: Bearer rfx_…</Code> — Value field(s) in the CSV: <Code>1</Code>.
            </>,
            <>
              Enable it, enable <i>Caching</i>, and fetch it with <i>Fetch and store all feed data</i> or the scheduled{' '}
              <Code>fetchFeed</Code> / <Code>cacheFeed</Code> tasks.
            </>,
          ]}
        />
        <P>The correlations of MISP then show which attributes of your events are in the referential.</P>
        <H>2. Referential of known-good values as a warninglist</H>
        <P>
          Warninglists flag attributes that are probably false positives (company IP ranges, legitimate domains…). A script turns a
          referential into a custom warninglist:
        </P>
        <Shell>{`#!/bin/sh
# cron: 0 5 * * *  — on the MISP server
set -e
D=/var/www/MISP/app/files/warninglists/lists/refex-internal-ranges
mkdir -p "$D"
curl -fsS -H "Authorization: Bearer $REFEX_TOKEN" "${api('internal-ranges')}/download/json" |
  jq --argjson v "$(date +%Y%m%d)" '{
      name: "Internal ranges (RefExposer)", version: $v, type: "cidr",
      description: "Company IP ranges published by RefExposer",
      matching_attributes: ["ip-src", "ip-dst", "ip-src|port", "ip-dst|port"],
      list: map(.cidr) }' > "$D/list.json.tmp"
mv "$D/list.json.tmp" "$D/list.json"
sudo -u www-data /var/www/MISP/app/Console/cake Admin updateWarningLists`}</Shell>
        <P>
          <Code>type</Code>: <Code>cidr</Code> for networks, <Code>string</Code> for exact values, <Code>hostname</Code> for domains and
          their sub-domains. Enable the list once in <b>Input Filters › Warninglists</b>.
        </P>
        <H>3. Enrichment module (misp-modules)</H>
        <P>
          To query any referential from the <i>Enrich</i> menu of an attribute, add an expansion module to misp-modules (
          <Code>misp_modules/modules/expansion/refexposer.py</Code>), then set its <Code>url</Code>, <Code>token</Code> and{' '}
          <Code>referential</Code> in <b>Administration › Server Settings › Plugin › Enrichment</b>:
        </P>
        <Shell>{`import json
import requests

misperrors = {"error": "Error"}
mispattributes = {"input": ["vulnerability", "ip-src", "ip-dst", "domain", "sha1", "sha256", "md5"], "output": ["text"]}
moduleinfo = {"version": "1", "author": "RefExposer", "module-type": ["expansion", "hover"],
              "description": "Looks the value up in a RefExposer referential"}
moduleconfig = ["url", "token", "referential"]


def handler(q=False):
    if q is False:
        return False
    request = json.loads(q)
    cfg = request.get("config", {})
    value = next((request[t] for t in mispattributes["input"] if t in request), None)
    if not value or not all(cfg.get(k) for k in moduleconfig):
        misperrors["error"] = "missing value or module configuration"
        return misperrors
    r = requests.get(f"{cfg['url'].rstrip('/')}/api/referentials/{cfg['referential']}/lookup/{requests.utils.quote(value, safe='')}",
                     headers={"Authorization": f"Bearer {cfg['token']}"}, timeout=15)
    if r.status_code == 404:
        return {"results": [{"types": ["text"], "values": [f"{value}: not in {cfg['referential']}"]}]}
    r.raise_for_status()
    return {"results": [{"types": ["text"], "values": [json.dumps(r.json(), indent=2, ensure_ascii=False)]}]}


def introspection():
    return mispattributes


def version():
    moduleinfo["config"] = moduleconfig
    return moduleinfo`}</Shell>
        <P>Restart misp-modules, then enable the module. As a <i>hover</i> module, it also answers when the mouse is over an attribute.</P>
      </>
    ),
  },
  {
    id: 'integrations-other',
    title: 'Other tools',
    body: () => (
      <>
        <TokenNote />
        <H>Raw files (MaxMind DB, Bloom filters) for Suricata, Zeek, nginx…</H>
        <P>
          The raw file has a stable URL and is downloaded only when it changed (<Code>-z</Code> compares the date with the local file):
        </P>
        <Shell>{`#!/bin/sh
# cron: 30 4 * * *
F=/var/lib/geoip/GeoLite2-City.mmdb
curl -fsS -z "$F" -o "$F.tmp" -H "Authorization: Bearer $REFEX_TOKEN" "${api('geolite2-city')}/raw" \\
  && [ -s "$F.tmp" ] && mv "$F.tmp" "$F" || rm -f "$F.tmp"`}</Shell>
        <List size="sm" spacing={4} maw={860} mb="sm">
          <List.Item>
            <b>Suricata</b>: <Code>geoip-database: /var/lib/geoip/GeoLite2-Country.mmdb</Code> in <Code>suricata.yaml</Code>.
          </List.Item>
          <List.Item>
            <b>Zeek</b>: <Code>redef mmdb_dir = "/var/lib/geoip";</Code> (files named <Code>GeoLite2-City.mmdb</Code> /{' '}
            <Code>GeoLite2-ASN.mmdb</Code>).
          </List.Item>
          <List.Item>
            <b>nginx</b> (<Code>geoip2</Code> module): <Code>geoip2 /var/lib/geoip/GeoLite2-Country.mmdb {'{ … }'}</Code>, then{' '}
            <Code>nginx -s reload</Code> after an update.
          </List.Item>
          <List.Item>
            A Bloom filter (DCSO format) can be used offline by the <Code>bloom</Code> tool of DCSO or by your own scripts.
          </List.Item>
        </List>
        <H>Excel and Power BI (Power Query)</H>
        <Steps
          items={[
            <>
              <b>Data › Get Data › From Web</b>, URL: <Code>{`${api('cisa-kev')}/download/csv`}</Code>.
            </>,
            <>
              Authentication: <b>Basic</b>, any user name, the token as <b>password</b>.
            </>,
            <>
              Load, then <b>Refresh</b> (or schedule the refresh in the Power BI service, through a gateway if RefExposer is internal).
            </>,
          ]}
        />
        <Shell>{`let
    Source = Csv.Document(Web.Contents("${api('cisa-kev')}/download/csv"),
                          [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Rows = Table.PromoteHeaders(Source, [PromoteAllScalars = true])
in
    Rows`}</Shell>
        <P>
          For a selection only, use the export with filters: <Code>{`${api('cisa-kev')}/export?format=csv&vendorProject=Microsoft`}</Code>.
        </P>
        <H>Grafana (Infinity data source)</H>
        <P>
          Create an <b>Infinity</b> data source with <i>Authentication › Bearer Token</i> (the token) and your RefExposer address in{' '}
          <i>Allowed hosts</i>. In a panel: type JSON, URL <Code>{`${api('cisa-kev')}/rows?limit=1000&sort=-dateAdded`}</Code>, rows
          root <Code>rows</Code>.
        </P>
        <P>
          For your own scripts, see <Anchor component={Link} to="/help/integrations-scripts" size="sm">Scripts (Python &amp; Bash)</Anchor>.
          Other tools (SOAR, ticketing, MISP modules, Ansible <Code>uri</Code>…) follow the same pattern: an HTTP call with the{' '}
          <Code>Authorization</Code> header, <Code>GET /lookup/&#123;value&#125;</Code> for one value, <Code>POST /lookup</Code> for a
          batch.
        </P>
      </>
    ),
  },
];
