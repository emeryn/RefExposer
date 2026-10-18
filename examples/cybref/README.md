# CybrefV2 referentials

[`cybref-referentials.yaml`](cybref-referentials.yaml) declares the **124 datasets of [CybrefV2](https://github.com/emeryn/CybrefV2)** that are tables, read from its daily mirror on GitHub (`raw.githubusercontent.com/emeryn/CybrefV2/main/...`). They cover threat intelligence and IOCs, LOL binaries, vulnerabilities and exploits (NVD, CISA KEV, EPSS, Exploit-DB…), ATT&CK, CAPEC, CWE and D3FEND, cloud, CDN, VPN and Tor IP ranges, IP-to-ASN and country, domains, hardware registries and detection lists.

## Import

- **Interface**: *Administration › Referentials › Import*, choose the file. The plan lists the 124 referentials to create; apply it, with or without starting the imports.
- **API**:
  ```bash
  jq -Rs '{content: ., apply: true, pull: true}' cybref-referentials.yaml \
    | curl -X POST -H "$H" -H "Content-Type: application/json" -d @- http://localhost:8080/api/admin/config/import
  ```
- **Configuration folder**: copy the file into `config/`. The referentials are then defined by the file (read-only in the interface).

The first import downloads about 860 MB in total. Afterwards each referential is checked every day around 07:00, after the CybrefV2 refresh. The minute is spread per referential, and unchanged files answer `304 Not Modified`.

## What the definitions contain

Every definition was created, built and checked by RefExposer itself: the format and the path of the records come from the preview, the key from the built data. The file was then imported into an empty instance and every referential built again without error.

- **Key and quality rules**: each referential has a lookup key (`/api/referentials/<id>/lookup/<value>`), checked by two rules: not empty (error) and unique (warning). Where the source has a few duplicates or empty values (Spamhaus metadata line, NVD CVE present in a yearly file and in `modified`…), both rules are warnings and never block an update.
- **Rolling feeds kept as history** (`merge`): `abusech_threatfox_recent`, `abusech_malwarebazaar_recent` and `ransomwarelive_victims_recent` publish a window of the last hours or days. Each download is merged into the published data as a delta (upsert on the key), so the referential keeps everything seen since its first import. Use *Update › Replace with the source* to start again from the current window.
- **JSON documents**: the records are read from their array or object (`records_path`: `vulnerabilities`, `prefixes`, `objects`, `relays`, `@graph`, `*`…).
- **Reshaped sources** (SQL transformation): Cloudflare and Fastly IP ranges (IPv4 + IPv6 lists into one `cidr` column), GitHub meta (`service`, `cidr` for each service), MISP warning lists (one row per value, with its list).
- **abuse.ch CSV** (SSLBL, MalwareBazaar): the header is a comment, so the columns are named in the options.

## Resources

`nvd_cve` reads 27 yearly NVD files (2.7 GB of JSON, 418,757 CVEs) and needs about **8 GB of memory** for the backend, 3 minutes on 6 cores. Remove it from the file on a small host, or keep `first_epss`, `cisa_kev` and `mobile_cve` for vulnerabilities. `misp_warninglists` (1.5 million values) and `nvd_cpe` (1.8 million CPEs) are the next largest.

## Not included

`mitre_atlas` and `bluetooth_company_ids` (YAML) and `curl_ca_bundle` (PEM certificates) are not tables.
