#!/usr/bin/env python3
"""TinyFish web search + page fetch (stdlib only, no SDK/CLI dependencies).

Search: GET https://api.search.tinyfish.ai?query=...
Fetch:  POST https://api.fetch.tinyfish.ai  {"urls": [...], "format": ..., "ttl": ...}
Auth:   X-API-Key header — from TINYFISH_API_KEY env var or --api-key.

Exit codes: 0 ok, 1 usage/validation error, 2 API/network error.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

KEYCHAIN_SERVICE = "tinyfish-api-key"  # macOS Keychain generic-password service name
SEARCH_URL = "https://api.search.tinyfish.ai"
FETCH_URL = "https://api.fetch.tinyfish.ai"
TIMEOUT = 90


def die(msg: str, code: int = 1) -> "None":
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def keychain_read(service: str) -> str | None:
    """Read a generic-password item via the macOS `security` CLI. None if absent/unavailable."""
    if sys.platform != "darwin":
        return None
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if r.returncode != 0:
        return None
    val = r.stdout.strip()
    return val or None


def keychain_write(service: str, key: str) -> None:
    """Create/update the generic-password item via the macOS `security` CLI."""
    r = subprocess.run(
        ["security", "add-generic-password", "-a", os.environ.get("USER", "user"),
         "-s", service, "-w", key, "-U"],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        die(f"keychain write failed: {r.stderr.strip()}", 1)


def get_key(explicit: str | None, kc_service: str) -> str:
    """Resolution order: --api-key > TINYFISH_API_KEY env > macOS Keychain."""
    if explicit:
        return explicit
    env = os.environ.get("TINYFISH_API_KEY")
    if env:
        return env
    kc = keychain_read(kc_service)
    if kc:
        return kc
    die(
        "no API key found. Store one in the macOS Keychain (recommended):\n"
        f"  python3 {os.path.abspath(__file__)} set-key 'YOUR_KEY'\n"
        "or export TINYFISH_API_KEY=...  (key from https://agent.tinyfish.ai/api-keys)",
        1,
    )


def call(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        hint = {401: "check your API key", 403: "key may lack access", 422: "invalid parameters"}.get(e.code, "")
        die(f"HTTP {e.code} from TinyFish{' (' + hint + ')' if hint else ''}: {body}", 2)
    except urllib.error.URLError as e:
        die(f"cannot reach TinyFish: {e.reason}", 2)
    except json.JSONDecodeError as e:
        die(f"non-JSON response from TinyFish: {e}", 2)


# ----------------------------------------------------------------- search ---
def cmd_search(args: argparse.Namespace) -> None:
    params: dict = {"query": args.query}
    if args.location:
        params["location"] = args.location
    if args.language:
        params["language"] = args.language
    if args.recency_minutes is not None:
        if args.after or args.before:
            die("recency_minutes cannot be combined with after/before dates (per API docs)")
        if not (1 <= args.recency_minutes <= 5256000):
            die("recency_minutes must be 1..5256000")
        params["recency_minutes"] = args.recency_minutes
    for flag, key in (("after", "after_date"), ("before", "before_date")):
        val = getattr(args, flag)
        if val:
            params[key] = val
    if args.after and args.before and args.after > args.before:
        die("after_date must be <= before_date")
    if args.domain_type:
        params["domain_type"] = args.domain_type
        if args.domain_type == "research_paper" and (args.after or args.before or args.recency_minutes):
            die("date filters are not supported for domain_type=research_paper; use --pub-year-min/max")
    if args.pub_year_min is not None:
        params["pub_year_min"] = args.pub_year_min
    if args.pub_year_max is not None:
        params["pub_year_max"] = args.pub_year_max
    if args.purpose:
        params["purpose"] = args.purpose

    url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-API-Key": get_key(args.api_key, args.keychain_service), "Accept": "application/json"})
    data = call(req)

    results = data.get("results", [])
    if args.limit is not None:
        results = results[: args.limit]

    if args.json:
        print(json.dumps({**data, "results": results}, indent=2, ensure_ascii=False))
        return
    if not results:
        print("no results")
        return
    for r in results:
        print(f"{r.get('position')}. {r.get('title', '')}")
        print(f"   {r.get('url', '')}")
        snippet = (r.get("snippet") or "").replace("\n", " ").strip()
        if snippet:
            print(f"   {snippet[:300]}")
        extra = [v for k, v in (("site_name", r.get("site_name")),) if v]
        if extra:
            print(f"   [{'; '.join(extra)}]")
        print()
    print(f"-- showing {len(results)} of total_results={data.get('total_results')} page={data.get('page')}")


# ------------------------------------------------------------------ fetch ---
def cmd_fetch(args: argparse.Namespace) -> None:
    urls = args.urls
    if not urls:
        die("provide at least one URL")
    if len(urls) > 10:
        die("max 10 URLs per request (TinyFish limit)")
    body: dict = {"urls": urls, "format": args.format}
    if args.ttl is not None:
        body["ttl"] = args.ttl

    req = urllib.request.Request(
        FETCH_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "X-API-Key": get_key(args.api_key, args.keychain_service),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    data = call(req)

    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    pages = data.get("results", [])
    for r in pages:
        title = r.get("title") or "(untitled)"
        final = r.get("final_url") or r.get("url", "")
        print(f"# {title}\n  {final}")
        text = r.get("text", "")
        if isinstance(text, (dict, list)):
            text = json.dumps(text, indent=2, ensure_ascii=False)
        text = (text or "").strip()
        if args.max_chars and len(text) > args.max_chars:
            text = text[: args.max_chars] + f"\n… [truncated at {args.max_chars} chars]"
        print(text, "\n")
    for e in data.get("errors", []):
        print(f"FAILED: {e.get('url')} – {e.get('error')}", file=sys.stderr)
    if not pages and not data.get("errors"):
        print("(no results returned)")


def cmd_set_key(args: argparse.Namespace) -> None:
    if sys.platform != "darwin":
        die("set-key requires macOS (Keychain)")
    keychain_write(args.keychain_service, args.key)
    got = keychain_read(args.keychain_service)
    print(f"stored in Keychain service {args.keychain_service!r}"
          f" ({'verified readable' if got else 'WARNING: read-back failed'})")


def main() -> None:
    ap = argparse.ArgumentParser(prog="tinyfish", description="TinyFish web search + fetch (X-API-Key auth)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="web search: titles, snippets, URLs")
    s.add_argument("query", help="search query (quote it)")
    s.add_argument("--limit", type=int, default=None, help="client-side trim of returned results")
    s.add_argument("--location", help="geo-target, e.g. 'United States'")
    s.add_argument("--language", help="language filter")
    s.add_argument("--recency-minutes", type=int, help="freshness window 1..5256000 (not with date filters)")
    s.add_argument("--after", dest="after", help="YYYY-MM-DD lower bound")
    s.add_argument("--before", dest="before", help="YYYY-MM-DD upper bound")
    s.add_argument("--domain-type", choices=["web", "news", "research_paper"], help="content category")
    s.add_argument("--pub-year-min", type=int, help="research papers: min publication year")
    s.add_argument("--pub-year-max", type=int, help="research papers: max publication year")
    s.add_argument("--purpose", help="state the intent behind the query for better results")
    s.add_argument("--api-key", help="override env/keychain resolution")
    s.add_argument("--keychain-service", default=KEYCHAIN_SERVICE,
                   help=f"Keychain service name (default {KEYCHAIN_SERVICE})")
    s.add_argument("--json", action="store_true", help="raw JSON output")
    s.set_defaults(func=cmd_search)

    f = sub.add_parser("fetch", help="fetch clean page text for 1-10 URLs")
    f.add_argument("urls", nargs="*", help="URLs to fetch (positional)")
    f.add_argument("--format", choices=["markdown", "html", "json"], default="markdown")
    f.add_argument("--ttl", type=int, help="cache freshness seconds; 0 = live fetch")
    f.add_argument("--max-chars", type=int, default=6000, help="display truncation per page (0 = no truncation)")
    f.add_argument("--api-key", help="override env/keychain resolution")
    f.add_argument("--keychain-service", default=KEYCHAIN_SERVICE,
                   help=f"Keychain service name (default {KEYCHAIN_SERVICE})")
    f.add_argument("--json", action="store_true", help="raw JSON output")
    f.set_defaults(func=cmd_fetch)

    k = sub.add_parser("set-key", help="store the API key in the macOS Keychain")
    k.add_argument("key", help="the TinyFish API key (never echoed back)")
    k.add_argument("--keychain-service", default=KEYCHAIN_SERVICE)
    k.set_defaults(func=cmd_set_key)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
