#!/usr/bin/env python3

"""Coordinate writers that publish managed Project Conversation files."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path


LOCK_RELATIVE_PATH = Path(".scratch/_conversations/.write-lock")
OWNER_FILE = "owner.json"


def lock_path(project: Path) -> Path:
    resolved = project.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError(f"project root is not a directory: {resolved}")
    return resolved / LOCK_RELATIVE_PATH


def atomic_write_json(path: Path, value: dict[str, object]) -> None:
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_owner(lock: Path) -> dict[str, object]:
    try:
        value = json.loads((lock / OWNER_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def ensure_lock_directory(lock: Path) -> None:
    details = lock.lstat()
    if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
        raise ValueError(f"conversation lock path is not a directory: {lock}")


def lock_age(lock: Path, now: float) -> float:
    owner = read_owner(lock)
    refreshed = owner.get("refreshed_unix", owner.get("created_unix"))
    if isinstance(refreshed, (int, float)) and not isinstance(refreshed, bool):
        return max(0.0, now - float(refreshed))
    return max(0.0, now - lock.stat().st_mtime)


def archive_stale_lock(lock: Path, now: float) -> Path:
    stamp = datetime.fromtimestamp(now).astimezone().strftime("%Y%m%dT%H%M%S%z")
    archive = lock.with_name(f"{lock.name}.stale-{stamp}-{secrets.token_hex(4)}")
    os.rename(lock, archive)
    return archive


def acquire(project: Path, wait_seconds: float, stale_seconds: float) -> str:
    lock = lock_path(project)
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    deadline = time.monotonic() + wait_seconds
    token = secrets.token_hex(16)

    while True:
        now = time.time()
        try:
            lock.mkdir(mode=0o700)
        except FileExistsError:
            ensure_lock_directory(lock)
            if lock_age(lock, now) >= stale_seconds:
                try:
                    archive_stale_lock(lock, now)
                except FileNotFoundError:
                    continue
                except OSError as error:
                    raise ValueError(f"could not archive stale conversation lock {lock}: {error}") from error
                continue
            if time.monotonic() >= deadline:
                owner = read_owner(lock)
                created = owner.get("created_at", "unknown")
                raise TimeoutError(f"conversation save is already locked since {created}: {lock}")
            time.sleep(0.1)
            continue

        try:
            atomic_write_json(
                lock / OWNER_FILE,
                {
                    "managed_by": "conversation-continuity",
                    "token": token,
                    "created_at": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
                    "created_unix": now,
                    "refreshed_at": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
                    "refreshed_unix": now,
                },
            )
        except Exception:
            try:
                lock.rmdir()
            except OSError:
                pass
            raise
        return token


def owned_lock(project: Path, token: str) -> tuple[Path, dict[str, object]]:
    lock = lock_path(project)
    try:
        ensure_lock_directory(lock)
    except FileNotFoundError as error:
        raise ValueError(f"conversation lock does not exist: {lock}") from error
    owner = read_owner(lock)
    if owner.get("token") != token:
        raise ValueError(f"conversation lock token does not match: {lock}")
    return lock, owner


def refresh(project: Path, token: str) -> None:
    lock, owner = owned_lock(project, token)
    now = time.time()
    owner["refreshed_at"] = datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds")
    owner["refreshed_unix"] = now
    atomic_write_json(lock / OWNER_FILE, owner)


def release(project: Path, token: str) -> None:
    lock, owner = owned_lock(project, token)
    owner_path = lock / OWNER_FILE
    owner_path.unlink()
    try:
        lock.rmdir()
    except OSError as error:
        atomic_write_json(owner_path, owner)
        raise ValueError(f"conversation lock contains unexpected files: {lock}") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    acquire_parser = subparsers.add_parser("acquire", help="Acquire the project publication lock")
    acquire_parser.add_argument("project", type=Path)
    acquire_parser.add_argument("--wait", type=float, default=30)
    acquire_parser.add_argument("--stale-after", type=float, default=1800)
    refresh_parser = subparsers.add_parser("refresh", help="Refresh a held project publication lock")
    refresh_parser.add_argument("project", type=Path)
    refresh_parser.add_argument("token")
    release_parser = subparsers.add_parser("release", help="Release the project publication lock")
    release_parser.add_argument("project", type=Path)
    release_parser.add_argument("token")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "acquire":
            if args.wait < 0 or args.stale_after <= 0:
                raise ValueError("--wait cannot be negative and --stale-after must be positive")
            print(acquire(args.project, args.wait, args.stale_after))
        elif args.command == "refresh":
            refresh(args.project, args.token)
        else:
            release(args.project, args.token)
    except (OSError, TimeoutError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
