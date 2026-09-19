---
name: tinyfish-search
description: Search the web and fetch clean page text via the TinyFish API using a bundled Python script (stdlib only). Use for web lookups, finding sources, checking current information, news/research-paper searches, and reading pages found. Does not search local files, code, or private services.
---

# TinyFish Search & Fetch

Use the bundled [scripts/tinyfish.py](scripts/tinyfish.py) — plain Python 3 stdlib
(urllib), no SDK, no CLI dependency, no browser. Two subcommands mirroring TinyFish's
two free APIs:

- `search` → `GET https://api.search.tinyfish.ai` — ranked results (position, title, snippet, url)
- `fetch`  → `POST https://api.fetch.tinyfish.ai` — clean extracted page text for 1–10 URLs

## Setup (one time)

Store the key in the **macOS Keychain** (recommended — survives reboots, no plaintext
env files; get a key at https://agent.tinyfish.ai/api-keys):

```sh
python3 /absolute/path/to/tinyfish-search/scripts/tinyfish.py set-key 'YOUR_KEY'
```

Key resolution order: `--api-key` flag > `TINYFISH_API_KEY` env var > Keychain
(service `tinyfish-api-key`, account = your username). The script errors with clear
instructions if none is found; never fabricate or echo the key.

## Search

Resolve the script path relative to this SKILL.md, then:

```sh
python3 /absolute/path/to/tinyfish-search/scripts/tinyfish.py search 'your query'
```

Quote the query as one shell argument. Useful flags (validated against the API docs):

- `--limit N` — trim output to the first N results
- `--location "United States" --language en` — geo/language targeting
- `--recency-minutes 1440` — last 24h (cannot combine with date filters)
- `--after 2026-01-01 --before 2026-09-01` — calendar bounds (YYYY-MM-DD)
- `--domain-type news` — news results (publisher/date fields); `research_paper` adds
  authors/venue/citations and pairs with `--pub-year-min/--pub-year-max` (no date filters)
- `--purpose "finding benchmark numbers to cite"` — state the intent behind terse queries
- `--json` — raw JSON (machine processing)

## Fetch pages

```sh
python3 .../scripts/tinyfish.py https://example.com/page          # positional URL
python3 .../scripts/tinyfish.py fetch URL1 URL2 URL3              # up to 10 URLs
python3 .../scripts/tinyfish.py fetch --format markdown URL
python3 .../scripts/tinyfish.py fetch --ttl 0 URL                 # bypass cache, live fetch
python3 .../scripts/tinyfish.py fetch --max-chars 20000 URL
```

Markdown is the default text format (`--format html|json` for others). Per-URL failures
arrive on stderr as `FAILED: <url> – <reason>` and never abort the batch.

## Notes for the agent

- Exit codes: 0 ok · 1 usage/validation (bad flag combos, missing key) · 2 API/network error.
  On 2, report the error; do not retry blindly.
- Search snippets are previews — `fetch` the promising URLs before quoting or citing.
- The script never prints the API key. Keep it that way in any output you relay.
