#!/usr/bin/env python3
"""Bing web search via the agent-browser CLI.

Usage:
    python3 search.py "your query"
    python3 search.py -n 5 "your query"     # number of results
    python3 search.py --json "your query"   # output JSON

Requires the agent-browser CLI on PATH and a visible/headful-capable
Chrome install. The browser is opened (or reused if already running)
and left open after the search.
"""

import argparse
import base64
import json
import subprocess
import sys
from urllib.parse import parse_qs, quote_plus, urlparse

AGENT_BROWSER = "agent-browser"
BING_URL = "https://www.bing.com"

# JS that extracts structured results from Bing's results page.
EXTRACT_JS = """
Array.from(document.querySelectorAll('li.b_algo')).map(li => {
  const a = li.querySelector('h2 a');
  const snip = li.querySelector('.b_caption p, [class*="b_lineclamp"], .b_paractl');
  if (!a) return null;
  const cite = li.querySelector('cite');
  return {
    title: a.innerText.trim(),
    url: a.href,
    display_url: cite ? cite.innerText.trim() : '',
    snippet: snip ? snip.innerText.trim() : '',
  };
}).filter(r => r && r.url && r.title)
"""


def run_browser(args: list[str], timeout: int = 60) -> str:
    """Run an agent-browser command, return stdout; raise on error."""
    try:
        result = subprocess.run(
            [AGENT_BROWSER, *args], capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"agent-browser {' '.join(args)} timed out after {timeout}s"
        ) from e
    output = result.stdout.strip()
    if result.returncode != 0:
        raise RuntimeError(
            f"agent-browser {' '.join(args)} failed: {result.stderr.strip() or output}"
        )
    return output


def resolve_redirect(url: str) -> str:
    """Bing wraps result links in /ck/a redirect URLs; the real URL is
    base64-encoded in the u=a1... query parameter."""
    if "/ck/a" not in url:
        return url
    params = parse_qs(urlparse(url).query)
    for token in params.get("u", []):
        if token.startswith("a1"):
            try:
                decoded = base64.b64decode(token[2:] + "===").decode(
                    "utf-8", errors="replace"
                )
                if decoded.startswith("http"):
                    return decoded
            except Exception:
                pass
    return url


def search(query: str, count: int = 10) -> list[dict]:
    """Search Bing for `query` and return up to `count` results.

    Navigates straight to the search URL rather than typing into the box:
    fill+Enter is racy (autocomplete, stale form state) and can return
    results for a different query than intended.
    """
    for attempt in range(3):
        run_browser(["open", f"{BING_URL}/search?q={quote_plus(query)}"])
        # Wait for the results container; non-fatal if it hangs (cold
        # browser launch) — the title check below is the real guard.
        try:
            run_browser(["wait", "#b_results", "--timeout", "5000"], timeout=30)
        except RuntimeError:
            pass  # cold browser launch; title check below is the real guard
        # Guard against a stale page: the URL bar can update before the
        # results render, or a redirect can land on the previous query.
        # Confirm the rendered title matches; re-navigate until it does.
        title = run_browser(["get", "title"]).lower().strip()
        if query.lower() in title:
            break
    else:
        raise RuntimeError(
            f"page title {title!r} does not reflect query {query!r} — "
            "aborting rather than returning stale results"
        )

    # Bing can render links before their visible text, even after the page
    # title and results container are ready.
    try:
        run_browser(
            [
                "wait", "--fn",
                "Array.from(document.querySelectorAll('li.b_algo h2 a'))"
                ".some(a => a.innerText.trim().length > 0)",
                "--timeout", "5000",
            ],
            timeout=30,
        )
    except RuntimeError:
        pass  # Extraction below also handles an empty or blocked page.

    out = run_browser(["eval", EXTRACT_JS])
    # agent-browser wraps non-object results in JSON string quotes,
    # so the payload may need one or two levels of decoding.
    for _ in range(2):
        if isinstance(out, str) and out.startswith(('[', '"')):
            out = json.loads(out)
        else:
            break
    results = out[:count]
    for r in results:
        r["url"] = resolve_redirect(r["url"])
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search the web on Bing via the agent-browser CLI."
    )
    parser.add_argument("query", help="search query")
    parser.add_argument(
        "-n", "--num", type=int, default=10, help="number of results (default: 10)"
    )
    parser.add_argument(
        "--json", action="store_true", help="output raw JSON"
    )
    args = parser.parse_args()

    try:
        results = search(args.query, args.num)
    except (RuntimeError, FileNotFoundError) as e:
        sys.exit(f"error: {e}")

    if not results:
        sys.exit("no results found (is the Bing page showing a consent wall or captcha?)")

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for i, r in enumerate(results, 1):
            url = r.get("display_url") or r["url"]
            print(f"{i}. {r['title']}")
            print(f"   {url}")
            if r["snippet"]:
                print(f"   {r['snippet']}\n")


if __name__ == "__main__":
    main()
