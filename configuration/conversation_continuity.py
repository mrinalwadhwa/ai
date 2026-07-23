#!/usr/bin/env python3

"""Schedule Project Conversation save and resume checks for agent clients."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import secrets
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional


STATE_VERSION = 2
EVENT_VERSION = 1
SAVE_PROTOCOL = "publisher-v1"
LOCK_STALE_SECONDS = 30
LOCK_WAIT_SECONDS = 1
DUPLICATE_STOP_SECONDS = 5
PENDING_STALE_SECONDS = 60 * 60
DEFAULT_CONFIG = {
    "save_every_turns": 8,
    "save_every_minutes": 45,
    "context_thresholds": [70, 85],
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


def event_path(path: Path, evaluation_id: str) -> Path:
    return path.parent / "events" / f"{path.stem}-{evaluation_id}.json"


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


def new_state(now: float, cause: str = "initial") -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "turns_since_completion": 0,
        "last_completed_at": now,
        "force_causes": [cause],
        "context_thresholds_seen": [],
        "last_context_percentage": None,
        "pending_evaluation": None,
        "last_completion": None,
    }


def valid_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def valid_pending(value: Any) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, dict)
        and isinstance(value.get("evaluation_id"), str)
        and len(value["evaluation_id"]) == 24
        and all(character in "0123456789abcdef" for character in value["evaluation_id"])
        and valid_number(value.get("triggered_at"))
        and isinstance(value.get("causes"), list)
        and all(isinstance(item, str) and item for item in value["causes"])
        and isinstance(value.get("attempt"), int)
        and not isinstance(value.get("attempt"), bool)
        and value["attempt"] >= 1
        and isinstance(value.get("baseline_index"), str)
        and (
            value.get("finalization") is None
            or (
                isinstance(value.get("finalization"), dict)
                and valid_number(value["finalization"].get("completed_at"))
                and value["finalization"].get("outcome")
                in {"index-changed", "index-unchanged", "expired"}
                and value["finalization"].get("completion_source")
                in {"stop-hook", "stale-recovery", "session-reset"}
                and isinstance(value["finalization"].get("index_changed"), bool)
                and (
                    value["finalization"].get("retry") is None
                    or (
                        isinstance(value["finalization"].get("retry"), dict)
                        and isinstance(value["finalization"]["retry"].get("causes"), list)
                        and all(
                            isinstance(item, str) and item
                            for item in value["finalization"]["retry"]["causes"]
                        )
                        and isinstance(value["finalization"]["retry"].get("attempt"), int)
                        and not isinstance(value["finalization"]["retry"].get("attempt"), bool)
                        and value["finalization"]["retry"]["attempt"] >= 1
                        and valid_number(value["finalization"]["retry"].get("triggered_at"))
                        and isinstance(value["finalization"]["retry"].get("baseline_index"), str)
                    )
                )
            )
        )
    )


def valid_v2_state(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("version") == STATE_VERSION
        and isinstance(value.get("turns_since_completion"), int)
        and not isinstance(value.get("turns_since_completion"), bool)
        and value["turns_since_completion"] >= 0
        and isinstance(value.get("last_completed_at"), (int, float))
        and not isinstance(value.get("last_completed_at"), bool)
        and isinstance(value.get("force_causes"), list)
        and all(isinstance(item, str) and item for item in value["force_causes"])
        and isinstance(value.get("context_thresholds_seen"), list)
        and all(
            isinstance(item, int) and not isinstance(item, bool) and 1 <= item <= 99
            for item in value["context_thresholds_seen"]
        )
        and (
            value.get("last_context_percentage") is None
            or (
                valid_number(value.get("last_context_percentage"))
            )
        )
        and valid_pending(value.get("pending_evaluation"))
        and (
            value.get("last_completion") is None
            or (
                isinstance(value.get("last_completion"), dict)
                and isinstance(value["last_completion"].get("evaluation_id"), str)
                and valid_number(value["last_completion"].get("completed_at"))
                and value["last_completion"].get("outcome")
                in {"index-changed", "index-unchanged", "expired"}
            )
        )
    )


def valid_v1_state(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("version") == 1
        and isinstance(value.get("turns_since_evaluation"), int)
        and not isinstance(value.get("turns_since_evaluation"), bool)
        and value["turns_since_evaluation"] >= 0
        and valid_number(value.get("last_evaluation_at"))
        and isinstance(value.get("force_evaluation"), bool)
        and isinstance(value.get("context_thresholds_seen"), list)
        and all(
            isinstance(item, int) and not isinstance(item, bool) and 1 <= item <= 99
            for item in value["context_thresholds_seen"]
        )
        and (
            value.get("last_context_percentage") is None
            or valid_number(value.get("last_context_percentage"))
        )
    )


def migrate_v1_state(value: dict[str, Any], now: float) -> dict[str, Any]:
    state = new_state(now, "legacy-pending")
    state["force_causes"] = ["legacy-pending"] if value["force_evaluation"] else []
    state["context_thresholds_seen"] = list(value["context_thresholds_seen"])
    state["last_context_percentage"] = value["last_context_percentage"]
    return state


def load_state(path: Path, now: float) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return new_state(now)
    if valid_v2_state(value):
        return value
    if valid_v1_state(value):
        return migrate_v1_state(value, now)
    return new_state(now)


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


def index_revision(router: Path) -> str:
    try:
        if router.is_symlink() or not router.is_file():
            return "absent" if not router.exists() else "non-regular"
        return f"sha256:{hashlib.sha256(router.read_bytes()).hexdigest()}"
    except OSError:
        return "unreadable"


def write_event(path: Path, event: dict[str, Any]) -> None:
    destination = event_path(path, event["evaluation_id"])
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = (json.dumps(event, indent=2, sort_keys=True) + "\n").encode("utf-8")
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
        temporary.chmod(0o600)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.read_bytes() != content:
                raise ValueError(f"continuity event conflicts with existing event: {destination}")
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def add_cause(state: dict[str, Any], cause: str) -> None:
    causes = state.setdefault("force_causes", [])
    if cause not in causes:
        causes.append(cause)


def merge_causes(first: list[str], second: list[str]) -> list[str]:
    merged = []
    for cause in (*first, *second):
        if cause not in merged:
            merged.append(cause)
    return merged


def reset_after_evaluation(state: dict[str, Any], now: float) -> None:
    state["turns_since_completion"] = 0
    state["last_completed_at"] = now
    state["pending_evaluation"] = None


def begin_evaluation(
    state: dict[str, Any],
    now: float,
    causes: list[str],
    router: Path,
    *,
    attempt: int = 1,
) -> dict[str, Any]:
    pending = {
        "evaluation_id": secrets.token_hex(12),
        "triggered_at": now,
        "causes": causes,
        "attempt": attempt,
        "baseline_index": index_revision(router),
        "finalization": None,
    }
    state["pending_evaluation"] = pending
    state["force_causes"] = []
    return pending


def prepare_finalization(
    state: dict[str, Any],
    router: Path,
    now: float,
    source: str,
    *,
    forced_outcome: Optional[str] = None,
    retry: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    pending = state["pending_evaluation"]
    current_revision = index_revision(router)
    changed = current_revision != pending["baseline_index"]
    outcome = forced_outcome or (
        "index-changed" if changed else "index-unchanged"
    )
    if retry is not None:
        retry = {
            **retry,
            "triggered_at": now,
            "baseline_index": current_revision,
        }
    finalization = {
        "completed_at": now,
        "outcome": outcome,
        "completion_source": source,
        "index_changed": changed,
        "retry": retry,
    }
    pending["finalization"] = finalization
    return finalization


def evaluation_event(
    state: dict[str, Any],
    client: str,
    session_id: str,
    project_root: Path,
) -> dict[str, Any]:
    pending = state["pending_evaluation"]
    finalization = pending["finalization"]
    triggered_at = float(pending["triggered_at"])
    completed_at = float(finalization["completed_at"])
    return {
        "version": EVENT_VERSION,
        "evaluation_id": pending["evaluation_id"],
        "client": client,
        "session_id": session_id,
        "project_root": str(project_root),
        "triggered_at": triggered_at,
        "completed_at": completed_at,
        "duration_seconds": max(0, completed_at - triggered_at),
        "causes": pending["causes"],
        "outcome": finalization["outcome"],
        "index_changed": finalization["index_changed"],
        "completion_source": finalization["completion_source"],
        "attempt": pending["attempt"],
    }


def finish_finalization(
    path: Path,
    state: dict[str, Any],
    client: str,
    session_id: str,
    project_root: Path,
) -> Optional[dict[str, Any]]:
    event = evaluation_event(state, client, session_id, project_root)
    retry = state["pending_evaluation"]["finalization"].get("retry")
    write_event(path, event)
    state["last_completion"] = {
        "evaluation_id": event["evaluation_id"],
        "completed_at": event["completed_at"],
        "outcome": event["outcome"],
    }
    reset_after_evaluation(state, event["completed_at"])
    pending = None
    if isinstance(retry, dict):
        retry_causes = merge_causes(
            retry["causes"],
            list(state.get("force_causes", [])),
        )
        pending = {
            "evaluation_id": secrets.token_hex(12),
            "triggered_at": retry["triggered_at"],
            "causes": retry_causes,
            "attempt": retry["attempt"],
            "baseline_index": retry["baseline_index"],
            "finalization": None,
        }
        state["pending_evaluation"] = pending
        state["force_causes"] = []
    write_state(path, state)
    return pending


def finalize_pending(
    path: Path,
    state: dict[str, Any],
    client: str,
    session_id: str,
    scope: ProjectScope,
    now: float,
    source: str,
    *,
    forced_outcome: Optional[str] = None,
    retry: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    pending = state.get("pending_evaluation")
    if not isinstance(pending, dict):
        return None
    if pending.get("finalization") is None:
        router = scope.root / ".scratch" / "CONVERSATIONS.md"
        prepare_finalization(
            state,
            router,
            now,
            source,
            forced_outcome=forced_outcome,
            retry=retry,
        )
        write_state(path, state)
    return finish_finalization(path, state, client, session_id, scope.root)


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


def save_message(home: Path, pending: dict[str, Any]) -> str:
    causes = ", ".join(pending["causes"])
    skill_path = home / ".agents" / "skills" / "save-conversation" / "SKILL.md"
    publisher_path = skill_path.parent / "scripts" / "publish_conversation.py"
    return (
        f"[conversation-continuity:save-check; protocol={SAVE_PROTOCOL}; causes={causes}] Reload "
        f"`{skill_path}` from disk before deciding; do not rely on a previously loaded copy of this skill. Silently "
        f"follow its automatic path only if it declares `{SAVE_PROTOCOL}`. If publication is needed, use the single "
        f"step 9 `snapshot` call from `{publisher_path}` both to capture bases and to verify that its response reports "
        f"`\"protocol\": \"{SAVE_PROTOCOL}\"`; do not run a separate protocol probe. Only that exact publisher may "
        "change canonical Project Conversation files; do not use Write or Edit on them. Keep the publication request "
        "in memory and send it with quoted stdin; do not create a request file. If either installed path is missing or "
        "its protocol does not match, report the failing path, ask the user to reinstall the current save-conversation "
        "skill, and make no conversation-file changes. "
        "Save only when unsaved context cannot be reconstructed from Git, project documentation, Fluent, artifacts, "
        "or live systems and the work reached a durable milestone, the session is intentionally pausing, or visible "
        "context is at risk. Never save recoverable live state. Save unfinished discussion only when the session is "
        "intentionally pausing or visible context is at risk. Do not announce or narrate the check, and do not "
        "mention a successful save or no-op. Report only a missing or incompatible installation, or a failure or "
        "conflict required by the current skill's guardrails. Continue only work already authorized by the current "
        "request."
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
        state = load_state(path, now)
        pending = state.get("pending_evaluation")
        if isinstance(pending, dict) and pending.get("finalization") is not None:
            recovered_retry = finish_finalization(path, state, client, session_id, scope.root)
            state = load_state(path, now)
            if recovered_retry is not None:
                state["pending_evaluation"] = None
                state["force_causes"] = merge_causes(
                    list(state.get("force_causes", [])),
                    recovered_retry["causes"],
                )
        if source in {"startup", "clear"}:
            pending = state.get("pending_evaluation")
            if isinstance(pending, dict):
                router = scope.root / ".scratch" / "CONVERSATIONS.md"
                changed = index_revision(router) != pending["baseline_index"]
                finalize_pending(
                    path,
                    state,
                    client,
                    session_id,
                    scope,
                    now,
                    "session-reset",
                    forced_outcome="index-changed" if changed else "expired",
                )
            cause = "first-session" if source == "startup" else "session-clear"
            state = new_state(now, cause)
        else:
            state["turns_since_completion"] = 0
            state["context_thresholds_seen"] = []
            state["last_context_percentage"] = None
            add_cause(state, "post-compaction")
        write_state(path, state)

    if scope.router is None:
        return {}
    return additional_context("SessionStart", resume_message(source))


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
            if isinstance(state.get("pending_evaluation"), dict):
                pending = finalize_pending(path, state, client, session_id, scope, now, "stop-hook")
                if pending is not None:
                    return {
                        "decision": "block",
                        "reason": save_message(home, pending),
                    }
                queued_causes = list(state.get("force_causes", []))
                if queued_causes:
                    router = scope.root / ".scratch" / "CONVERSATIONS.md"
                    pending = begin_evaluation(state, now, queued_causes, router)
                    write_state(path, state)
                    return {
                        "decision": "block",
                        "reason": save_message(home, pending),
                    }
            else:
                write_state(path, state)
            return {}

        pending = state.get("pending_evaluation")
        if isinstance(pending, dict) and pending.get("finalization") is not None:
            recovered_retry = finish_finalization(path, state, client, session_id, scope.root)
            if recovered_retry is not None:
                return {
                    "decision": "block",
                    "reason": save_message(home, recovered_retry),
                }
            pending = state.get("pending_evaluation")

        if isinstance(pending, dict):
            age = now - float(pending["triggered_at"])
            if age < DUPLICATE_STOP_SECONDS:
                write_state(path, state)
                return {}
            if age < PENDING_STALE_SECONDS:
                return {
                    "decision": "block",
                    "reason": save_message(home, pending),
                }
            original_causes = list(pending["causes"])
            next_attempt = int(pending["attempt"]) + 1
            router = scope.root / ".scratch" / "CONVERSATIONS.md"
            changed = index_revision(router) != pending["baseline_index"]
            stale_outcome = "index-changed" if changed else "expired"
            retry_causes = merge_causes(
                original_causes,
                merge_causes(list(state.get("force_causes", [])), ["retry"]),
            )
            pending = finalize_pending(
                path,
                state,
                client,
                session_id,
                scope,
                now,
                "stale-recovery",
                forced_outcome=stale_outcome,
                retry={"causes": retry_causes, "attempt": next_attempt},
            )
            if pending is None:
                raise RuntimeError("stale continuity evaluation did not create its retry")
            return {
                "decision": "block",
                "reason": save_message(home, pending),
            }

        state["turns_since_completion"] = int(state.get("turns_since_completion", 0)) + 1
        elapsed = now - float(state.get("last_completed_at", now))
        causes = list(state.get("force_causes", []))
        if state["turns_since_completion"] >= config["save_every_turns"]:
            causes = merge_causes(causes, ["turn-count"])
        if elapsed >= float(config["save_every_minutes"]) * 60:
            causes = merge_causes(causes, ["elapsed-time"])

        if event.get("permission_mode") == "plan":
            for cause in causes:
                add_cause(state, cause)
            write_state(path, state)
            return {}

        if causes:
            router = scope.root / ".scratch" / "CONVERSATIONS.md"
            pending = begin_evaluation(state, now, causes, router)
            response = {
                "decision": "block",
                "reason": save_message(home, pending),
            }
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
    state_changed = False
    if isinstance(previous, (int, float)) and not isinstance(previous, bool) and percentage + 25 < previous:
        seen.clear()
        state_changed = True
    crossed = []
    for threshold in thresholds:
        if percentage >= threshold and threshold not in seen:
            seen.add(threshold)
            crossed.append(threshold)
            state_changed = True
    if not state_changed:
        return False
    state["context_thresholds_seen"] = sorted(seen)
    state["last_context_percentage"] = percentage
    for threshold in crossed:
        add_cause(state, f"context-{threshold}")
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
        if args.command == "hook":
            event = read_json(sys.stdin)
            print(json.dumps(process_hook(event, args.client, home, time.time()), separators=(",", ":")))
        else:
            event = read_json(sys.stdin)
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
