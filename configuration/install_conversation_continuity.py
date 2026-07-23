#!/usr/bin/env python3

"""Install the Project Conversation lifecycle controller for Claude and Codex."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shlex
import stat
import tempfile
from pathlib import Path
from typing import Any, Optional


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError(f"configuration path must be a regular file: {path}")
    if not path.exists():
        return {}
    if not path.is_file():
        raise ValueError(f"configuration path must be a regular file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"configuration must contain a JSON object: {path}")
    hooks = value.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        raise ValueError(f"hooks must be a JSON object: {path}")
    return value


def atomic_write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def serialized_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def existing_mode(path: Path, default: int = 0o600) -> int:
    if not path.exists():
        return default
    return stat.S_IMODE(path.stat().st_mode)


def snapshot_file(path: Path) -> Optional[tuple[bytes, int]]:
    if path.is_symlink():
        raise ValueError(f"installation target must be a regular file: {path}")
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"installation target must be a regular file: {path}")
    return path.read_bytes(), stat.S_IMODE(path.stat().st_mode)


def restore_snapshot(path: Path, snapshot: Optional[tuple[bytes, int]]) -> None:
    if snapshot is None:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(f"refusing to remove unexpected rollback target: {path}")
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    content, mode = snapshot
    atomic_write(path, content, mode)


def validate_managed_executable(destination: Path, digest_path: Path) -> None:
    destination_exists = destination.exists() or destination.is_symlink()
    digest_exists = digest_path.exists() or digest_path.is_symlink()
    if destination_exists != digest_exists:
        raise ValueError(f"incomplete managed continuity executable: {destination}")
    if not destination_exists:
        return
    if destination.is_symlink() or not destination.is_file() or digest_path.is_symlink() or not digest_path.is_file():
        raise ValueError(f"managed continuity executable must use regular files: {destination}")
    recorded = digest_path.read_text(encoding="utf-8").strip()
    if not recorded or recorded != file_digest(destination):
        raise ValueError(f"refusing to replace modified continuity executable: {destination}")


def remove_owned_handlers(
    settings: dict[str, Any],
    owned_commands: set[str],
) -> dict[str, Any]:
    updated = copy.deepcopy(settings)
    hooks = updated.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be a JSON object")

    for event_name, groups in list(hooks.items()):
        if not isinstance(groups, list):
            raise ValueError(f"hook event must contain a JSON array: {event_name}")
        kept_groups: list[Any] = []
        for group in groups:
            if not isinstance(group, dict):
                raise ValueError(f"hook group must be a JSON object: {event_name}")
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                raise ValueError(f"hook group must contain a hooks array: {event_name}")
            kept_handlers = []
            for handler in handlers:
                if not isinstance(handler, dict):
                    raise ValueError(f"hook handler must be a JSON object: {event_name}")
                if handler.get("command") not in owned_commands:
                    kept_handlers.append(handler)
            if kept_handlers:
                kept_group = copy.deepcopy(group)
                kept_group["hooks"] = kept_handlers
                kept_groups.append(kept_group)
        if kept_groups:
            hooks[event_name] = kept_groups
        else:
            hooks.pop(event_name, None)
    return updated


def add_hooks(
    settings: dict[str, Any],
    command: str,
    *,
    codex: bool,
) -> None:
    hooks = settings.setdefault("hooks", {})
    handler: dict[str, Any] = {
        "type": "command",
        "command": command,
        "timeout": 5,
    }
    if codex:
        handler["statusMessage"] = "Checking conversation continuity"
    hooks.setdefault("SessionStart", []).append(
        {
            "matcher": "startup|clear|compact",
            "hooks": [copy.deepcopy(handler)],
        }
    )
    hooks.setdefault("Stop", []).append({"hooks": [copy.deepcopy(handler)]})


def command_for(executable: Path, operation: str) -> str:
    return f"{shlex.quote(str(executable))} {operation}"


def install(
    source: Path,
    home: Path,
    codex_home: Path,
) -> list[str]:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"continuity source must be a regular file: {source}")

    destination = home / ".agents" / "bin" / "conversation-continuity"
    digest_path = destination.with_suffix(".sha256")
    claude_settings_path = home / ".claude" / "settings.json"
    codex_hooks_path = codex_home / "hooks.json"
    validate_managed_executable(destination, digest_path)

    claude_settings = load_object(claude_settings_path)
    codex_hooks = load_object(codex_hooks_path)

    claude_hook_command = command_for(destination, "hook --client claude")
    codex_hook_command = command_for(destination, "hook --client codex")
    statusline_command = command_for(destination, "statusline")
    owned_commands = {claude_hook_command, codex_hook_command, statusline_command}

    new_claude_settings = remove_owned_handlers(claude_settings, owned_commands)
    new_codex_hooks = remove_owned_handlers(codex_hooks, owned_commands)
    add_hooks(new_claude_settings, claude_hook_command, codex=False)
    add_hooks(new_codex_hooks, codex_hook_command, codex=True)

    messages = []
    existing_statusline = new_claude_settings.get("statusLine")
    if existing_statusline is None or (
        isinstance(existing_statusline, dict) and existing_statusline.get("command") == statusline_command
    ):
        new_claude_settings["statusLine"] = {
            "type": "command",
            "command": statusline_command,
        }
    else:
        messages.append(
            f"Preserved existing Claude status line; context-percentage save checks are disabled: {claude_settings_path}"
        )

    source_bytes = source.read_bytes()
    source_digest = hashlib.sha256(source_bytes).hexdigest()
    writes = [
        (destination, source_bytes, 0o755),
        (digest_path, f"{source_digest}\n".encode("utf-8"), 0o600),
        (
            claude_settings_path,
            serialized_json(new_claude_settings),
            existing_mode(claude_settings_path),
        ),
        (
            codex_hooks_path,
            serialized_json(new_codex_hooks),
            existing_mode(codex_hooks_path),
        ),
    ]
    snapshots = {path: snapshot_file(path) for path, _, _ in writes}
    try:
        for path, content, mode in writes:
            atomic_write(path, content, mode)
    except Exception as error:
        rollback_errors = []
        for path, _, _ in reversed(writes):
            try:
                restore_snapshot(path, snapshots[path])
            except Exception as rollback_error:
                rollback_errors.append(f"{path}: {rollback_error}")
        if rollback_errors:
            details = "; ".join(rollback_errors)
            raise RuntimeError(f"installation failed ({error}); rollback also failed: {details}") from error
        raise

    messages.extend(
        (
            f"Installed conversation continuity controller: {destination}",
            f"Installed Claude hooks: {claude_settings_path}",
            f"Installed Codex hooks: {codex_hooks_path}",
            "Review and trust the Codex hook definitions with /hooks in each Codex installation.",
        )
    )
    return messages


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).with_name("conversation_continuity.py"),
        help="controller source file",
    )
    parser.add_argument("--home", type=Path, help="home directory; defaults to HOME")
    parser.add_argument("--codex-home", type=Path, help="Codex directory; defaults to CODEX_HOME or ~/.codex")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    home_value = args.home or (Path(os.environ["HOME"]) if os.environ.get("HOME") else None)
    if home_value is None:
        raise SystemExit("HOME must be set or --home must be provided.")
    home = home_value.expanduser().resolve()
    codex_home_value = args.codex_home or (
        Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else home / ".codex"
    )
    codex_home = codex_home_value.expanduser().resolve()
    try:
        messages = install(args.source.expanduser().resolve(), home, codex_home)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error
    for message in messages:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
