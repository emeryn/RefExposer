# Example scripts

| Script | What it does |
|---|---|
| [`refex-internal.py`](refex-internal.py) | Creates an internal referential and feeds it: one row at a time, or in bulk from a CSV, JSON or JSON Lines file. Standard library only. |

Both the address and the token come from the environment:

```bash
export REFEX_URL=https://refexposer.example.com
export REFEX_TOKEN=rfx_...
```

The token comes from **My account › API tokens**, or — for a script that runs on its own — from a **service account**
(*Administration › Users › New service account*) holding the **manage** right on the referentials it writes to.
Creating a referential also needs the administrator or advanced-user role.

## refex-internal.py

```bash
# 1. The referential and its columns (the key identifies a row)
./refex-internal.py create sites "Sites" --key code \
    --column code:text:required --column label:text --column owner:text \
    --column people:integer --column opened:date --column active:boolean \
    --column tags:list --column comment:text

# 2. One row at a time
./refex-internal.py add   sites code=LYO label=Lyon owner=network people=120 active=true tags=prod,eu
./refex-internal.py patch sites LYO owner=secops          # only this column changes
./refex-internal.py set   sites LYO label=Lyon owner=secops   # replaces the whole row
./refex-internal.py delete sites LYO

# 3. In bulk, from a file
./refex-internal.py load sites sites.csv     # add and update, the other rows are kept
./refex-internal.py sync sites sites.csv     # add and update, every row absent from the file is deleted

# 4. Read it back
./refex-internal.py columns sites
./refex-internal.py rows sites --limit 20 --search lyon
```

`sites.csv` is an ordinary CSV whose header holds the column names (`.tsv`, `.json` — an array of objects — and
`.jsonl` work too):

```csv
code,label,owner,people,opened,active,tags,comment
LYO,Lyon,network,120,2019-04-01,true,"prod,eu",main site
NTE,Nantes,secops,40,2021-09-15,false,lab,
```

- **Column types**: `text`, `integer`, `number`, `boolean` (`true/false`, `yes/no`, `1/0`), `date` (`YYYY-MM-DD`),
  `datetime` (ISO 8601) and `list` (comma separated values). Values are checked against the type: a batch holding one
  bad value is refused whole and nothing is written.
- **Columns of the file that the referential does not have** are left out, with a warning on stderr.
- **Large files** are sent in batches of 10,000 rows (the API accepts 50,000 changes per request).
- Each change publishes a new version a few seconds later, so a burst of calls gives one version.

The same operations are available in the interface (**Edit** tab of the referential) and in the API, documented in the
**API** tab of each referential.
