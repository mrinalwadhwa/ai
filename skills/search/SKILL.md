---
name: search
description: Search the web through Bing using agent-browser and return source titles, links, and snippets. Use for web lookups, finding sources, or checking current information. Does not search local files, code, or connected private services.
---

# Search

Use the bundled [scripts/search.py](scripts/search.py), adapted from
`~/Workspace/models/bing_search.py`. The installed copy runs independently of
that checkout and uses Python's standard library.

## Run a search

Requires Python 3.9+, `agent-browser` on `PATH`, and a Chrome installation that
can open a visible window. Check `agent-browser --help` if setup is needed;
`agent-browser install` downloads its browser.

Resolve the script path relative to this `SKILL.md`, then run:

```sh
AGENT_BROWSER_HEADED=true AGENT_BROWSER_SESSION=search AGENT_BROWSER_JSON=false \
  python3 /absolute/path/to/search/scripts/search.py --json -n 5 -- 'your query'
```

Quote the query as one shell argument. `--` allows queries beginning with a
hyphen. Omit `--json` for readable text. `-n` defaults to 10 and limits the results
extracted from the current page; it does not fetch additional pages.

The named browser session keeps searches separate from the default automation
session. Run searches in that session sequentially: concurrent commands can
replace the page while another search is reading it. The browser stays open so
it can be reused or inspected.

## Use the results

JSON output contains `title`, `url`, `display_url`, and `snippet` for each result.
Use `url` for citations; `display_url` may be abbreviated. The script decodes
Bing's redirect links when possible. Snippets are previews: open the source
pages when the task requires verification, context, or direct quotations, and
cite the pages that support the answer.

## Handle failures

The script retries navigation up to three times when the rendered page title
does not match the query, then exits with an error to avoid returning stale
results. Do not report this as a successful search.

If no results are extracted, inspect the page in the same session:

```sh
agent-browser --session search get title
agent-browser --session search snapshot
```

An empty extraction can mean a consent screen, CAPTCHA, or changed page markup.
Report the observed blocker and ask the user to complete any required browser
interaction before retrying. For a missing CLI or browser, report the missing
dependency. A blocked search does not establish that no matching sources exist.
