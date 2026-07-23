#!/usr/bin/env python3

"""Schedule Project Conversation save and resume checks for agent clients."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional


STATE_VERSION = 1
LOCK_STALE_SECONDS = 30
LOCK_WAIT_SECONDS = 1
DEFAULT_CONFIG = {
    "save_every_turns": 3,
    "save_every_minutes": 15,
    "context_thresholds": [55, 75],
}
AUTOMATION_ENV = "AGENT_CONVERSATION_CONTINUITY"
CONFIG_ENV = "AGENT_CONVERSATION_CONTINUITY_CONFIG"
STATE_DIR_ENV = "AGENT_CONVERSATION_CONTINUITY_STATE_DIR"


@dataclass(frozen=True)
class ProjectScope:
    root: Path
    router: Optional[Path]
    managed: bool


def read_json(stream: Any) -> dict[str, Any]:
    value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("hook input must be a JSON object")
    return value


def home_directory() -> Optional[Path]:
    value = os.environ.get("HOME")
    if not value:
        return None
    return Path(value).expanduser().resolve()


def load_config(home: Path) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    configured_path = os.environ.get(CONFIG_ENV)
    path = Path(configured_path).expanduser() if configured_path else home / ".agents" / "conversation-continuity.json"
    if path.is_file():
        override = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(override, dict):
            raise ValueError(f"continuity config must be a JSON object: {path}")
        unknown = sorted(set(override) - set(DEFAULT_CONFIG))
        if unknown:
            raise ValueError(f"unknown continuity config fields: {', '.join(unknown)}")
        config.update(override)

    turns = config["save_every_turns"]
    minutes = config["save_every_minutes"]
    thresholds = config["context_thresholds"]
    if not isinstance(turns, int) or isinstance(turns, bool) or turns < 1:
        raise ValueError("save_every_turns must be a positive integer")
    if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or minutes <= 0:
        raise ValueError("save_every_minutes must be positive")
    if (
        not isinstance(thresholds, list)
        or not thresholds
        or any(not isinstance(item, int) or isinstance(item, bool) or item < 1 or item > 99 for item in thresholds)
    ):
        raise ValueError("context_thresholds must contain integers from 1 through 99")
    config["context_thresholds"] = sorted(set(thresholds))
    return config


def automation_setting() -> str:
    return os.environ.get(AUTOMATION_ENV, "on").strip().lower()


def automation_is_off() -> bool:
    return automation_setting() in {"0", "false", "no", "off"}


def automation_is_forced() -> bool:
    return automation_setting() == "force"


def router_is_managed(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return False
    if not lines or lines[0].strip() != "---":
        return False
    values: dict[str, str] = {}
    closed = False
    for line in lines[1:]:
        if line.strip() == "---":
            closed = True
            break
        key, separator, value = line.partition(":")
        if separator:
            values[key.strip()] = value.strip().strip("\"'")
    return (
        closed
        and values.get("managed_by") == "conversation-continuity"
        and values.get("conversation_version") == "1"
    )


def scope_for_root(root: Path) -> ProjectScope:
    router = root / ".scratch" / "CONVERSATIONS.md"
    if router.exists():
        return ProjectScope(root, router, router.is_file() and router_is_managed(router))
    return ProjectScope(root, None, True)


def primary_worktree_root(worktree_root: Path) -> Path:
    marker = worktree_root / ".git"
    if not marker.is_file():
        return worktree_root
    try:
        marker_line = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return worktree_root
    prefix = "gitdir:"
    if not marker_line.lower().startswith(prefix):
        return worktree_root
    git_directory = Path(marker_line[len(prefix) :].strip()).expanduser()
    if not git_directory.is_absolute():
        git_directory = worktree_root / git_directory
    try:
        git_directory = git_directory.resolve()
        common_value = (git_directory / "commondir").read_text(encoding="utf-8").strip()
        common_directory = Path(common_value).expanduser()
        if not common_directory.is_absolute():
            common_directory = git_directory / common_directory
        common_directory = common_directory.resolve()
        primary = common_directory.parent
        if common_directory.name == ".git" and primary.is_dir() and (primary / ".git").resolve() == common_directory:
            return primary
    except (OSError, UnicodeError):
        pass
    return worktree_root


def find_project_scope(cwd: str) -> Optional[ProjectScope]:
    if not cwd:
        return None
    try:
        current = Path(cwd).expanduser().resolve()
    except OSError:
        return None
    if not current.is_dir():
        return None

    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return scope_for_root(primary_worktree_root(candidate))
        router = candidate / ".scratch" / "CONVERSATIONS.md"
        if router.exists():
            return scope_for_root(candidate)
        conventional_main = candidate / "main"
        if conventional_main.is_dir() and (conventional_main / ".git").exists():
            return scope_for_root(primary_worktree_root(conventional_main))
    return None


def state_root(home: Path) -> Path:
    override = os.environ.get(STATE_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return home / ".agents" / "state" / "conversation-continuity"


def state_path(root: Path, client: str, session_id: str, project_root: Path) -> Path:
    identity = "\0".join((client, session_id, str(project_root))).encode("utf-8")
    return root / f"{hashlib.sha256(identity).hexdigest()}.json"


@contextlib.contextmanager
def state_lock(path: Path) -> Iterator[None]:
    lock = path.with_suffix(".lock")
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            lock.mkdir(mode=0o700)
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
                if age > LOCK_STALE_SECONDS:
                    lock.rmdir()
                    continue
            except (FileNotFoundError, NotADirectoryError, OSError):
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for continuity state lock: {lock}")
            time.sleep(0.02)
    try:
        yield
    finally:
        try:
            lock.rmdir()
        except FileNotFoundError:
            pass


def new_state(now: float) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "turns_since_evaluation": 0,
        "last_evaluation_at": now,
        "force_evaluation": True,
        "context_thresholds_seen": [],
        "last_context_percentage": None,
    }


def load_state(path: Path, now: float) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return new_state(now)
    valid = (
        isinstance(value, dict)
        and value.get("version") == STATE_VERSION
        and isinstance(value.get("turns_since_evaluation"), int)
        and not isinstance(value.get("turns_since_evaluation"), bool)
        and value["turns_since_evaluation"] >= 0
        and isinstance(value.get("last_evaluation_at"), (int, float))
        and not isinstance(value.get("last_evaluation_at"), bool)
        and isinstance(value.get("force_evaluation"), bool)
        and isinstance(value.get("context_thresholds_seen"), list)
        and all(
            isinstance(item, int) and not isinstance(item, bool) and 1 <= item <= 99
            for item in value["context_thresholds_seen"]
        )
        and (
            value.get("last_context_percentage") is None
            or (
                isinstance(value.get("last_context_percentage"), (int, float))
                and not isinstance(value.get("last_context_percentage"), bool)
            )
        )
    )
    if not valid:
        return new_state(now)
    return value


def write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def additional_context(event: str, message: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": message,
        }
    }


def resume_message(source: str) -> str:
    if source == "compact":
        return (
            "[conversation-continuity:resume-check] Context was compacted. Use the resume-conversation skill in its "
            "automatic post-compaction path when the active Project Conversation is identifiable from the compacted "
            "context. Treat saved state as supplemental, reconcile it with newer visible facts, and do not execute a "
            "saved next step unless the current request authorizes it."
        )
    return (
        "[conversation-continuity:resume-check] This project has saved Project Conversations. On the first user "
        "prompt, use the resume-conversation skill in its automatic startup path only when the prompt appears to "
        "continue saved work. Skip unrelated work silently, and do not choose among several plausible conversations "
        "merely because records exist."
    )


def save_message() -> str:
    return (
        "[conversation-continuity:save-check] Before ending this turn, use the save-conversation skill in its "
        "automatic path to decide whether durable Project Conversation state materially changed. Write nothing when "
        "a later agent's next action, constraints, decisions, evidence, and understanding of side effects would stay "
        "the same. Otherwise save at a safe boundary. Continue only work already authorized by the current request."
    )


def process_session_start(
    event: dict[str, Any],
    client: str,
    home: Path,
    now: float,
) -> dict[str, Any]:
    source = event.get("source")
    if source not in {"startup", "clear", "compact"}:
        return {}
    scope = find_project_scope(str(event.get("cwd", "")))
    session_id = event.get("session_id")
    if scope is None or not isinstance(session_id, str) or not session_id or not scope.managed:
        return {}

    path = state_path(state_root(home), client, session_id, scope.root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with state_lock(path):
        if source in {"startup", "clear"}:
            state = new_state(now)
        else:
            state = load_state(path, now)
            state["turns_since_evaluation"] = 0
            state["force_evaluation"] = True
            state["context_thresholds_seen"] = []
            state["last_context_percentage"] = None
        write_state(path, state)

    if scope.router is None:
        return {}
    return additional_context("SessionStart", resume_message(source))


def reset_after_evaluation(state: dict[str, Any], now: float) -> None:
    state["turns_since_evaluation"] = 0
    state["last_evaluation_at"] = now
    state["force_evaluation"] = False


def process_stop(
    event: dict[str, Any],
    client: str,
    home: Path,
    now: float,
    config: dict[str, Any],
) -> dict[str, Any]:
    scope = find_project_scope(str(event.get("cwd", "")))
    session_id = event.get("session_id")
    if scope is None or not scope.managed or not isinstance(session_id, str) or not session_id:
        return {}

    path = state_path(state_root(home), client, session_id, scope.root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    response: dict[str, Any] = {}
    with state_lock(path):
        state = load_state(path, now)

        if event.get("stop_hook_active") is True:
            write_state(path, state)
            return {}

        state["turns_since_evaluation"] = int(state.get("turns_since_evaluation", 0)) + 1
        elapsed = now - float(state.get("last_evaluation_at", now))
        due = (
            bool(state.get("force_evaluation"))
            or state["turns_since_evaluation"] >= config["save_every_turns"]
            or elapsed >= float(config["save_every_minutes"]) * 60
        )

        if event.get("permission_mode") == "plan":
            if due:
                state["force_evaluation"] = True
            write_state(path, state)
            return {}

        if due:
            reset_after_evaluation(state, now)
            response = {"decision": "block", "reason": save_message()}
        write_state(path, state)
    return response


def process_hook(event: dict[str, Any], client: str, home: Path, now: float) -> dict[str, Any]:
    if automation_is_off():
        return {}
    if client not in {"claude", "codex"}:
        raise ValueError(f"unsupported client: {client}")
    if event.get("permission_mode") == "bypassPermissions" and not automation_is_forced():
        return {}
    config = load_config(home)
    event_name = event.get("hook_event_name")
    if event_name == "SessionStart":
        return process_session_start(event, client, home, now)
    if event_name == "Stop":
        return process_stop(event, client, home, now, config)
    return {}


def statusline_cwd(event: dict[str, Any]) -> str:
    cwd = event.get("cwd")
    if isinstance(cwd, str) and cwd:
        return cwd
    workspace = event.get("workspace")
    if isinstance(workspace, dict):
        current = workspace.get("current_dir")
        if isinstance(current, str):
            return current
    return ""


def context_threshold_crossed(state: dict[str, Any], percentage: float, thresholds: list[int]) -> bool:
    seen = {
        int(item)
        for item in state.get("context_thresholds_seen", [])
        if isinstance(item, int) and not isinstance(item, bool)
    }
    previous = state.get("last_context_percentage")
    changed = False
    if isinstance(previous, (int, float)) and not isinstance(previous, bool) and percentage + 25 < previous:
        seen.clear()
        changed = True
    for threshold in thresholds:
        if percentage >= threshold and threshold not in seen:
            seen.add(threshold)
            changed = True
    if not changed:
        return False
    state["force_evaluation"] = True
    state["context_thresholds_seen"] = sorted(seen)
    state["last_context_percentage"] = percentage
    return True


def process_statusline(event: dict[str, Any], home: Path, now: float) -> str:
    if automation_is_off():
        return ""
    context = event.get("context_window")
    percentage = context.get("used_percentage") if isinstance(context, dict) else None
    if not isinstance(percentage, (int, float)) or isinstance(percentage, bool):
        return ""

    scope = find_project_scope(statusline_cwd(event))
    session_id = event.get("session_id")
    if scope is not None and scope.managed and isinstance(session_id, str) and session_id:
        config = load_config(home)
        path = state_path(state_root(home), "claude", session_id, scope.root)
        initial_state = load_state(path, now)
        if context_threshold_crossed(initial_state, percentage, config["context_thresholds"]):
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with state_lock(path):
                state = load_state(path, now)
                if context_threshold_crossed(state, percentage, config["context_thresholds"]):
                    write_state(path, state)

    return f"ctx {percentage:.0f}%"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    hook = subparsers.add_parser("hook", help="Process one Claude or Codex lifecycle hook")
    hook.add_argument("--client", choices=("claude", "codex"), required=True)
    subparsers.add_parser("statusline", help="Record Claude context use and render its percentage")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    home = home_directory()
    if home is None:
        if args.command == "hook":
            print("{}")
        return 0
    try:
        event = read_json(sys.stdin)
        if args.command == "hook":
            print(json.dumps(process_hook(event, args.client, home, time.time()), separators=(",", ":")))
        else:
            rendered = process_statusline(event, home, time.time())
            if rendered:
                print(rendered)
    except Exception as error:
        print(f"conversation continuity skipped: {error}", file=sys.stderr)
        if args.command == "hook":
            print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
