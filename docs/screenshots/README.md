# Screenshots of the interface

[← Documentation](../README.md) · [RefExposer](../../README.md)

The six images of the [main README](../../README.md#screenshots). They were taken on a **throwaway instance**
holding nothing but public referentials, so no company data is in them.

| File | View |
|---|---|
| `dashboard.png` | Dashboard |
| `explorer.png` | Data tab of a referential, with a filter applied |
| `search.png` | Multi-criteria search, with a term and its facets |
| `sql-console.png` | SQL console, with a join between two referentials |
| `api.png` | API tab of a referential |
| `alerts.png` | Alerts |

## Taking them again

1. Start an instance with demo data only: a few referentials of
   [`config/cybref.yml.sample`](../../config/cybref.yml.sample) are enough (CISA KEV and EPSS make the join of
   the SQL console; one source pointing at a URL that does not exist gives a failed update, and a `max_age: 1m`
   gives a stale referential, so the alerts page has something to show).
2. Capture at **1440 × 900**, browser zoom 100 %, light mode — the images sit on a white page on GitHub.
3. **PNG**, 1440 px wide, under about 400 KB each.
4. Check the last pixel before committing: an e-mail address in a corner, a host name in a URL bar, a name in
   an owner column. `REFEX_PUBLIC_URL=http://refexposer.example.com` keeps the generated `curl` examples
   neutral.

Dark mode versions are welcome as `<name>-dark.png`, but the README does not use them yet.
