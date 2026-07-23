#!/usr/bin/env python3
"""Collect read-only Git state without conflating upstream and comparison refs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


def run_git(project: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(
        ["git", "-C", str(project), *args],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="surrogateescape",
        env=env,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"git exited {result.returncode}"
        raise RuntimeError(f"git {' '.join(args)}: {detail}")
    return result


def relation(project: Path, ref: str) -> dict[str, Any]:
    oid = run_git(project, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()
    counts = run_git(project, "rev-list", "--left-right", "--count", f"HEAD...{ref}").stdout.split()
    if len(counts) != 2:
        raise RuntimeError(f"unexpected ahead/behind result for {ref}: {' '.join(counts)}")
    return {"ref": ref, "oid": oid, "ahead": int(counts[0]), "behind": int(counts[1])}


def parse_status(raw: str) -> list[dict[str, str]]:
    parts = raw.split("\0")
    if parts and parts[-1] == "":
        parts.pop()

    changes: list[dict[str, str]] = []
    index = 0
    while index < len(parts):
        entry = parts[index]
        if len(entry) < 3:
            raise RuntimeError(f"unexpected porcelain entry: {entry!r}")
        code = entry[:2]
        change = {"status": code, "path": entry[3:]}
        index += 1
        if "R" in code or "C" in code:
            if index >= len(parts):
                raise RuntimeError(f"rename or copy entry lacks a source path: {entry!r}")
            change["source_path"] = parts[index]
            index += 1
        changes.append(change)
    return changes


def collect(project: Path, compare_refs: list[str]) -> tuple[dict[str, Any], bool]:
    project = project.expanduser().resolve()
    root = Path(run_git(project, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    head_result = run_git(root, "rev-parse", "--verify", "HEAD", check=False)
    head = head_result.stdout.strip() if head_result.returncode == 0 else None

    branch_result = run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None

    upstream_result = run_git(
        root,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
        check=False,
    )
    upstream = None
    upstream_error = None
    warnings: list[str] = []
    had_error = False
    if upstream_result.returncode == 0:
        upstream_name = upstream_result.stdout.strip()
        try:
            upstream = relation(root, upstream_name)
        except RuntimeError as error:
            upstream_error = str(error)
            warnings.append(f"could not inspect configured upstream {upstream_name}: {error}")
            had_error = True
    else:
        detail = upstream_result.stderr.strip() or upstream_result.stdout.strip()
        expected_absence = (
            "no upstream configured" in detail
            or "HEAD does not point to a branch" in detail
            or (head is None and ("unknown revision" in detail or "no such branch" in detail))
        )
        if not expected_absence:
            upstream_error = detail or f"git exited {upstream_result.returncode}"
            warnings.append(f"could not resolve configured upstream: {upstream_error}")
            had_error = True

    comparisons: list[dict[str, Any]] = []
    for ref in compare_refs:
        try:
            comparisons.append(relation(root, ref))
        except RuntimeError as error:
            comparisons.append({"ref": ref, "error": str(error)})
            warnings.append(f"could not compare {ref}: {error}")
            had_error = True

    status = run_git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    snapshot = {
        "checked_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "worktree": str(root),
        "branch": branch,
        "head": head,
        "configured_upstream": upstream,
        "configured_upstream_error": upstream_error,
        "comparisons": comparisons,
        "changes": parse_status(status),
        "warnings": warnings,
    }
    return snapshot, had_error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="Path inside the Git worktree")
    parser.add_argument("--compare", action="append", default=[], metavar="REF", help="Ref to compare with HEAD")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        snapshot, had_error = collect(args.project, args.compare)
    except RuntimeError as error:
        print(json.dumps({"error": str(error)}, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 1 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
