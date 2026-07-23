#!/usr/bin/env python3
"""Publish one Project Conversation checkpoint through a constrained transaction."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
from typing import Any, Optional


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

import conversation_lock  # noqa: E402
import validate_conversations  # noqa: E402


MANAGER = "conversation-continuity"
PUBLISHER_PROTOCOL = "publisher-v1"
TRANSACTION_VERSION = 1
REQUEST_VERSION = 1
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
MAX_CONVERSATIONS = 32
CHECKPOINT_PLACEHOLDER = "@CHECKPOINT@"
INDEX_RELATIVE_PATH = Path("CONVERSATIONS.md")
MARKER_RELATIVE_PATH = Path("_conversations/RECOVERY_REQUIRED.json")
STAGING_RELATIVE_PATH = Path("_conversations/.staging")
SESSION_DIRECTORY = Path("_conversations/sessions")
TABLE_HEADER = "| Conversation | Status | Mode | Updated | Resume |"
TABLE_SEPARATOR = "|--------------|--------|------|---------|--------|"
BASE_RE = re.compile(r"^(absent|sha256:([0-9a-f]{64}))$")
BOUNDARY_RE = re.compile(r"^[0-9a-f]{16,64}$")
PART_RE_TEMPLATE = r"^--{boundary} (checkpoint|conversation ([a-z0-9]+(?:-[a-z0-9]+)*))--$"
SESSION_STEM_RE = re.compile(
    r"^(?P<stem>\d{4}-\d{2}-\d{2}T\d{6}[+-]\d{4}-(?:claude|codex|other))(?:-(?P<number>\d+))?\.md$"
)


class PublishError(Exception):
    def __init__(self, status: str, message: str, details: Optional[list[str]] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details or []


@dataclass(frozen=True)
class BaseExpectation:
    state: str
    value: Optional[str] = None


@dataclass(frozen=True)
class ConversationCandidate:
    conversation_id: str
    base: BaseExpectation
    content: str


@dataclass(frozen=True)
class PublishRequest:
    checkpoint_name: str
    index_base: BaseExpectation
    checkpoint_content: str
    conversations: tuple[ConversationCandidate, ...]


@dataclass(frozen=True)
class RecoveryTransaction:
    token: str
    stage: Path
    manifest_path: Path
    existing: dict[str, str]
    absent: tuple[str, ...]
    candidate: dict[str, str]
    new_checkpoint: str
    created_directories: tuple[str, ...]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def json_output(status: str, **values: object) -> dict[str, object]:
    return {"status": status, "protocol": PUBLISHER_PROTOCOL, **values}


def parse_base(value: str, field: str) -> BaseExpectation:
    match = BASE_RE.match(value)
    if not match:
        raise PublishError("invalid-request", f"{field} must be `absent` or `sha256:<64 lowercase hex>`")
    if match.group(1) == "absent":
        return BaseExpectation("absent")
    return BaseExpectation("sha256", match.group(2))


def validate_document(content: str, field: str) -> None:
    encoded = content.encode("utf-8")
    if not encoded:
        raise PublishError("invalid-request", f"{field} is empty")
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise PublishError("invalid-request", f"{field} exceeds {MAX_DOCUMENT_BYTES} bytes")
    if "\x00" in content:
        raise PublishError("invalid-request", f"{field} contains a NUL byte")
    if "\r" in content:
        raise PublishError("invalid-request", f"{field} must use LF line endings")
    if not content.endswith("\n"):
        raise PublishError("invalid-request", f"{field} must end with a newline")


def parse_request(raw: bytes) -> PublishRequest:
    if len(raw) > MAX_REQUEST_BYTES:
        raise PublishError("invalid-request", f"request exceeds {MAX_REQUEST_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PublishError("invalid-request", "request is not valid UTF-8") from error
    if "\x00" in text:
        raise PublishError("invalid-request", "request contains a NUL byte")
    if "\r" in text:
        raise PublishError("invalid-request", "request must use LF line endings")

    header_text, separator, body = text.partition("\n\n")
    if not separator:
        raise PublishError("invalid-request", "request header must end with one blank line")

    scalar_fields: dict[str, str] = {}
    conversation_bases: dict[str, BaseExpectation] = {}
    for line in header_text.splitlines():
        if line.startswith("conversation: "):
            parts = line.split(" ", 2)
            if len(parts) != 3:
                raise PublishError("invalid-request", f"invalid conversation header: {line!r}")
            conversation_id, base_text = parts[1], parts[2]
            if not validate_conversations.CONVERSATION_NAME_RE.match(conversation_id):
                raise PublishError("invalid-request", f"invalid conversation ID: {conversation_id!r}")
            if conversation_id in conversation_bases:
                raise PublishError("invalid-request", f"duplicate conversation ID: {conversation_id}")
            conversation_bases[conversation_id] = parse_base(
                base_text,
                f"conversation {conversation_id} base",
            )
            continue
        key, delimiter, value = line.partition(": ")
        if not delimiter or key not in {
            "conversation_continuity_request",
            "boundary",
            "checkpoint_name",
            "index_base",
        }:
            raise PublishError("invalid-request", f"unknown or malformed request header: {line!r}")
        if key in scalar_fields:
            raise PublishError("invalid-request", f"duplicate request header: {key}")
        scalar_fields[key] = value

    required = {
        "conversation_continuity_request",
        "boundary",
        "checkpoint_name",
        "index_base",
    }
    missing = sorted(required - set(scalar_fields))
    if missing:
        raise PublishError("invalid-request", f"missing request headers: {', '.join(missing)}")
    if scalar_fields["conversation_continuity_request"] != str(REQUEST_VERSION):
        raise PublishError("invalid-request", f"conversation_continuity_request must be {REQUEST_VERSION}")
    boundary = scalar_fields["boundary"]
    if not BOUNDARY_RE.match(boundary):
        raise PublishError("invalid-request", "boundary must contain 16 to 64 lowercase hexadecimal characters")
    checkpoint_name = scalar_fields["checkpoint_name"]
    if not validate_conversations.SESSION_NAME_RE.match(checkpoint_name):
        raise PublishError("invalid-request", "checkpoint_name does not match the version 1 filename schema")
    if not conversation_bases:
        raise PublishError("invalid-request", "request must contain at least one conversation")
    if len(conversation_bases) > MAX_CONVERSATIONS:
        raise PublishError("invalid-request", f"request contains more than {MAX_CONVERSATIONS} conversations")

    marker_re = re.compile(PART_RE_TEMPLATE.format(boundary=re.escape(boundary)))
    end_marker = f"--{boundary} end--"
    parts: list[tuple[str, Optional[str], str]] = []
    current_kind: Optional[str] = None
    current_id: Optional[str] = None
    current_lines: list[str] = []
    found_end = False
    for line in body.splitlines(keepends=True):
        marker_text = line.rstrip("\n")
        if marker_text == end_marker:
            if current_kind is None:
                raise PublishError("invalid-request", "request end marker appears before a document part")
            parts.append((current_kind, current_id, "".join(current_lines)))
            current_kind = None
            found_end = True
            continue
        marker_match = marker_re.match(marker_text)
        if marker_match:
            if found_end:
                raise PublishError("invalid-request", "request contains a part after the end marker")
            if current_kind is not None:
                parts.append((current_kind, current_id, "".join(current_lines)))
            if marker_match.group(1) == "checkpoint":
                current_kind = "checkpoint"
                current_id = None
            else:
                current_kind = "conversation"
                current_id = marker_match.group(2)
            current_lines = []
            continue
        if found_end:
            if line.strip():
                raise PublishError("invalid-request", "request contains content after the end marker")
            continue
        if current_kind is None:
            if line.strip():
                raise PublishError("invalid-request", "request body must start with the checkpoint marker")
            continue
        current_lines.append(line)

    if not found_end:
        raise PublishError("invalid-request", "request is missing its end marker")
    checkpoint_parts = [content for kind, _, content in parts if kind == "checkpoint"]
    if len(checkpoint_parts) != 1:
        raise PublishError("invalid-request", "request must contain exactly one checkpoint part")

    candidate_by_id: dict[str, str] = {}
    for kind, conversation_id, content in parts:
        if kind != "conversation":
            continue
        assert conversation_id is not None
        if conversation_id in candidate_by_id:
            raise PublishError("invalid-request", f"duplicate conversation part: {conversation_id}")
        candidate_by_id[conversation_id] = content
    if set(candidate_by_id) != set(conversation_bases):
        missing_parts = sorted(set(conversation_bases) - set(candidate_by_id))
        extra_parts = sorted(set(candidate_by_id) - set(conversation_bases))
        details = []
        if missing_parts:
            details.append(f"missing parts: {', '.join(missing_parts)}")
        if extra_parts:
            details.append(f"undeclared parts: {', '.join(extra_parts)}")
        raise PublishError("invalid-request", "conversation headers and parts differ", details)

    checkpoint_content = checkpoint_parts[0]
    validate_document(checkpoint_content, "checkpoint content")
    conversations = []
    for conversation_id in sorted(candidate_by_id):
        content = candidate_by_id[conversation_id]
        validate_document(content, f"conversation {conversation_id} content")
        if CHECKPOINT_PLACEHOLDER not in content:
            raise PublishError(
                "invalid-request",
                f"conversation {conversation_id} must contain {CHECKPOINT_PLACEHOLDER}",
            )
        conversations.append(
            ConversationCandidate(
                conversation_id,
                conversation_bases[conversation_id],
                content,
            )
        )

    return PublishRequest(
        checkpoint_name=checkpoint_name,
        index_base=parse_base(scalar_fields["index_base"], "index_base"),
        checkpoint_content=checkpoint_content,
        conversations=tuple(conversations),
    )


def read_request(source: str) -> bytes:
    if source == "-":
        return sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    path = Path(source).expanduser()
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PublishError("invalid-request", f"cannot open request file: {path}") from error
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise PublishError("invalid-request", f"request path is not a regular file: {path}")
        if details.st_size > MAX_REQUEST_BYTES:
            raise PublishError("invalid-request", f"request exceeds {MAX_REQUEST_BYTES} bytes")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read(MAX_REQUEST_BYTES + 1)
    finally:
        os.close(descriptor)


def require_real_directory(path: Path) -> None:
    details = path.lstat()
    if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
        raise PublishError("conflict", f"path is not a real directory: {path}")


def require_regular_file(path: Path) -> None:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode):
        raise PublishError("conflict", f"path is not a regular non-symlink file: {path}")


def safe_mkdir(path: Path, created: Optional[list[Path]] = None) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        require_real_directory(path)
    else:
        if created is not None:
            created.append(path)
        fsync_directory(path.parent)


def safe_store_check(project: Path, conversation_ids: tuple[str, ...] = ()) -> None:
    require_real_directory(project)
    scratch = project / ".scratch"
    if scratch.exists() or scratch.is_symlink():
        require_real_directory(scratch)
    else:
        return

    for relative in (
        Path("_conversations"),
        STAGING_RELATIVE_PATH,
        SESSION_DIRECTORY,
    ):
        path = scratch / relative
        if path.exists() or path.is_symlink():
            require_real_directory(path)

    lock = scratch / Path(conversation_lock.LOCK_RELATIVE_PATH).relative_to(".scratch")
    if lock.exists() or lock.is_symlink():
        require_real_directory(lock)

    for relative in (INDEX_RELATIVE_PATH, MARKER_RELATIVE_PATH):
        path = scratch / relative
        if path.exists() or path.is_symlink():
            require_regular_file(path)

    for conversation_id in conversation_ids:
        directory = scratch / conversation_id
        if directory.exists() or directory.is_symlink():
            require_real_directory(directory)
        current = directory / "CONVERSATION.md"
        if current.exists() or current.is_symlink():
            require_regular_file(current)


def fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    fsync_directory(path.parent)


def atomic_replace_bytes(path: Path, content: bytes, mode: int = 0o600) -> None:
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def exclusive_install(path: Path, content: bytes, mode: int = 0o644) -> None:
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        os.link(temporary, path)
        fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def strict_json_load(path: Path) -> dict[str, Any]:
    require_regular_file(path)

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise PublishError("recovery-required", f"invalid recovery JSON: {path}") from error
    if not isinstance(value, dict):
        raise PublishError("recovery-required", f"recovery JSON is not an object: {path}")
    return value


def durable_json(path: Path, value: dict[str, object], *, replace: bool = False) -> None:
    content = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if replace:
        atomic_replace_bytes(path, content)
    else:
        durable_write(path, content)


def parse_managed(path: Path) -> dict[str, Any]:
    try:
        frontmatter, _ = validate_conversations.parse_frontmatter(path)
    except (OSError, UnicodeError, ValueError) as error:
        raise PublishError("ownership-conflict", f"file is not a managed conversation file: {path}") from error
    if frontmatter.get("managed_by") != MANAGER:
        raise PublishError("ownership-conflict", f"file is not managed by {MANAGER}: {path}")
    return frontmatter


def current_bytes(path: Path) -> Optional[bytes]:
    if not path.exists() and not path.is_symlink():
        return None
    require_regular_file(path)
    return path.read_bytes()


def check_expectation(path: Path, expected: BaseExpectation) -> Optional[bytes]:
    actual = current_bytes(path)
    if expected.state == "absent":
        if actual is not None:
            raise PublishError("conflict", f"expected an absent file: {path}")
        return None
    if actual is None:
        raise PublishError("conflict", f"expected an existing file: {path}")
    actual_hash = sha256_bytes(actual)
    if actual_hash != expected.value:
        raise PublishError("conflict", f"file changed since it was inspected: {path}")
    return actual


def base_value(content: Optional[bytes]) -> dict[str, str]:
    if content is None:
        return {"state": "absent"}
    return {"state": "sha256", "value": sha256_bytes(content)}


def snapshot(
    project: Path,
    conversation_ids: tuple[str, ...],
    wait_seconds: float = 30,
) -> dict[str, object]:
    project = project.expanduser().resolve()
    for conversation_id in conversation_ids:
        if not validate_conversations.CONVERSATION_NAME_RE.match(conversation_id):
            raise PublishError("invalid-request", f"invalid conversation ID: {conversation_id!r}")
    safe_store_check(project, conversation_ids)
    scratch = project / ".scratch"
    marker = scratch / MARKER_RELATIVE_PATH
    if marker.exists() or marker.is_symlink():
        raise PublishError("recovery-required", f"recovery marker exists: {marker}")

    created_directories: list[Path] = []
    safe_mkdir(scratch, created_directories)
    safe_mkdir(scratch / "_conversations", created_directories)
    token: Optional[str] = None
    private_warnings: list[str] = []
    try:
        token = conversation_lock.acquire(project, wait_seconds=wait_seconds, stale_seconds=1800)
        safe_store_check(project, conversation_ids)
        if marker.exists() or marker.is_symlink():
            raise PublishError("recovery-required", f"recovery marker exists: {marker}")
        private_warnings = private_artifact_warnings(scratch)

        index_path = scratch / INDEX_RELATIVE_PATH
        index = current_bytes(index_path)
        if index is not None:
            parse_managed(index_path)
        currents: dict[str, Optional[bytes]] = {}
        for conversation_id in conversation_ids:
            path = scratch / conversation_id / "CONVERSATION.md"
            content = current_bytes(path)
            if content is not None:
                parse_managed(path)
            currents[conversation_id] = content

        if marker.exists() or marker.is_symlink():
            raise PublishError("conflict", "conversation state changed while taking the snapshot")
        if current_bytes(index_path) != index:
            raise PublishError("conflict", f"Conversation Index changed while it was inspected: {index_path}")
        for conversation_id, content in currents.items():
            path = scratch / conversation_id / "CONVERSATION.md"
            if current_bytes(path) != content:
                raise PublishError("conflict", f"Current Conversation changed while it was inspected: {path}")
    finally:
        if token is not None:
            conversation_lock.release(project, token)
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass

    index_base = base_value(index)
    conversation_bases = {
        conversation_id: base_value(currents[conversation_id])
        for conversation_id in sorted(currents)
    }

    def request_base(value: dict[str, str]) -> str:
        if value["state"] == "absent":
            return "absent"
        return f"sha256:{value['value']}"

    return json_output(
        "snapshot",
        project=str(project),
        index=index_base,
        conversations=conversation_bases,
        request_headers=[
            f"index_base: {request_base(index_base)}",
            *(
                f"conversation: {conversation_id} {request_base(conversation_bases[conversation_id])}"
                for conversation_id in sorted(conversation_bases)
            ),
        ],
        warnings=private_warnings,
    )


def reserve_checkpoint_name(sessions: Path, requested: str) -> str:
    match = SESSION_STEM_RE.match(requested)
    if not match:
        raise PublishError("invalid-request", "checkpoint_name does not match the version 1 filename schema")
    if not (sessions / requested).exists() and not (sessions / requested).is_symlink():
        return requested
    next_number = int(match.group("number") or "1") + 1
    while True:
        candidate = f"{match.group('stem')}-{next_number}.md"
        path = sessions / candidate
        if not path.exists() and not path.is_symlink():
            return candidate
        next_number += 1


def update_frontmatter_timestamp(text: str, timestamp: str) -> str:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\n") != "---":
        raise PublishError("conflict", "Conversation Index has invalid frontmatter")
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.rstrip("\n") == "---")
    except StopIteration as error:
        raise PublishError("conflict", "Conversation Index has invalid frontmatter") from error
    indexes = [index for index, line in enumerate(lines[1:end], 1) if line.startswith("updated_at:")]
    if len(indexes) != 1:
        raise PublishError("conflict", "Conversation Index must contain one updated_at field")
    ending = "\n" if lines[indexes[0]].endswith("\n") else ""
    lines[indexes[0]] = f"updated_at: {timestamp}{ending}"
    return "".join(lines)


def parse_router_row(line: str) -> Optional[dict[str, str]]:
    parts = [part.strip() for part in line.strip().strip("|").split("|")]
    if len(parts) != 5:
        return None
    conversation_match = validate_conversations.CONVERSATION_LINK_RE.match(parts[0])
    if not conversation_match:
        return None
    return {
        "conversation": conversation_match.group(1),
        "link": conversation_match.group(2),
        "status": parts[1],
        "mode": parts[2],
        "updated_at": parts[3],
        "resume": parts[4],
        "raw": line,
    }


def candidate_row(path: Path) -> tuple[dict[str, str], str]:
    try:
        frontmatter, body = validate_conversations.parse_frontmatter(path)
    except (OSError, UnicodeError, ValueError) as error:
        raise PublishError("invalid-request", f"cannot parse candidate Current Conversation: {path.name}") from error
    sections, _ = validate_conversations.section_map(body)
    resume = validate_conversations.first_content_line(sections.get("Resume", []))
    values = {
        "conversation": str(frontmatter.get("conversation", "")),
        "status": str(frontmatter.get("status", "")),
        "mode": str(frontmatter.get("mode", "")),
        "updated_at": str(frontmatter.get("updated_at", "")),
        "resume": resume,
    }
    row = (
        f"| [{values['conversation']}]({values['conversation']}/CONVERSATION.md) "
        f"| {values['status']} | {values['mode']} | {values['updated_at']} | {values['resume']} |\n"
    )
    return values, row


def router_sort_key(row: dict[str, str]) -> tuple[int, float, str]:
    order = {status: index for index, status in enumerate(validate_conversations.STATUSES)}
    try:
        timestamp = dt.datetime.fromisoformat(row["updated_at"]).timestamp()
    except ValueError:
        timestamp = 0
    return (order.get(row["status"], len(order)), -timestamp, row["conversation"])


def build_index(
    existing: Optional[bytes],
    current_paths: dict[str, Path],
    scratch: Path,
) -> bytes:
    candidates: dict[str, tuple[dict[str, str], str]] = {}
    for conversation_id, path in sorted(current_paths.items()):
        values, row = candidate_row(path)
        if values["conversation"] != conversation_id:
            raise PublishError(
                "invalid-request",
                f"candidate conversation frontmatter does not match {conversation_id}",
            )
        candidates[conversation_id] = (values, row)

    timestamps = []
    for values, _ in candidates.values():
        if not validate_conversations.valid_timestamp(values["updated_at"]):
            raise PublishError("invalid-request", "candidate Current Conversation has an invalid updated_at")
        timestamps.append((dt.datetime.fromisoformat(values["updated_at"]), values["updated_at"]))
    updated_at = max(timestamps)[1]

    if existing is None:
        rows = [row for _, row in candidates.values()]
        rows.sort(key=lambda row: router_sort_key(parse_router_row(row) or {}))
        text = (
            "---\n"
            f"managed_by: {MANAGER}\n"
            f"conversation_version: {validate_conversations.SCHEMA_VERSION}\n"
            f"updated_at: {updated_at}\n"
            "---\n\n"
            "# Conversations\n\n"
            f"{TABLE_HEADER}\n"
            f"{TABLE_SEPARATOR}\n"
            + "".join(rows)
        )
        legacy = scratch / "HANDOFFS.md"
        if legacy.exists() and not legacy.is_symlink():
            text += "\n## Legacy records\n\n- [Previous conversation records](HANDOFFS.md)\n"
        return text.encode("utf-8")

    try:
        text = existing.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PublishError("conflict", "Conversation Index is not valid UTF-8") from error
    text = update_frontmatter_timestamp(text, updated_at)
    lines = text.splitlines(keepends=True)
    try:
        header_index = next(index for index, line in enumerate(lines) if line.rstrip("\n") == TABLE_HEADER)
    except StopIteration as error:
        raise PublishError("conflict", "Conversation Index is missing its router table") from error
    if header_index + 1 >= len(lines) or lines[header_index + 1].rstrip("\n") != TABLE_SEPARATOR:
        raise PublishError("conflict", "Conversation Index has an invalid router separator")
    row_start = header_index + 2
    row_end = row_start
    while row_end < len(lines) and lines[row_end].lstrip().startswith("|"):
        row_end += 1
    existing_rows = lines[row_start:row_end]

    seen_affected: set[str] = set()
    all_parseable = True
    merged_rows: list[str] = []
    for line in existing_rows:
        parsed = parse_router_row(line)
        if parsed is None:
            all_parseable = False
            merged_rows.append(line)
            continue
        conversation_id = parsed["conversation"]
        if conversation_id in candidates:
            if conversation_id in seen_affected:
                raise PublishError("conflict", f"Conversation Index repeats affected row: {conversation_id}")
            merged_rows.append(candidates[conversation_id][1])
            seen_affected.add(conversation_id)
        else:
            merged_rows.append(line)
        if (
            parsed["status"] not in validate_conversations.STATUSES
            or not validate_conversations.valid_timestamp(parsed["updated_at"])
        ):
            all_parseable = False

    for conversation_id in sorted(set(candidates) - seen_affected):
        merged_rows.append(candidates[conversation_id][1])

    if all_parseable:
        parsed_rows = [parse_router_row(line) for line in merged_rows]
        if all(row is not None for row in parsed_rows):
            merged_rows = [
                str(row["raw"])
                for row in sorted(
                    (row for row in parsed_rows if row is not None),
                    key=router_sort_key,
                )
            ]

    lines[row_start:row_end] = merged_rows
    return "".join(lines).encode("utf-8")


def canonical_relative_paths(conversation_ids: tuple[str, ...], checkpoint_name: str) -> tuple[str, ...]:
    return (
        f"{SESSION_DIRECTORY.as_posix()}/{checkpoint_name}",
        *(f"{conversation_id}/CONVERSATION.md" for conversation_id in sorted(conversation_ids)),
        INDEX_RELATIVE_PATH.as_posix(),
    )


def managed_target(relative: str) -> bool:
    if relative == INDEX_RELATIVE_PATH.as_posix():
        return True
    if re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*/CONVERSATION\.md$", relative):
        return True
    session_prefix = f"{SESSION_DIRECTORY.as_posix()}/"
    return relative.startswith(session_prefix) and bool(
        validate_conversations.SESSION_NAME_RE.match(relative.removeprefix(session_prefix))
    )


def stage_file(stage_section: Path, relative: str, content: bytes) -> Path:
    path = stage_section / Path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    durable_write(path, content)
    return path


def install_marker(scratch: Path, token: str, manifest_relative: str) -> Path:
    marker = scratch / MARKER_RELATIVE_PATH
    content = (
        json.dumps(
            {
                "managed_by": MANAGER,
                "transaction_version": TRANSACTION_VERSION,
                "token": token,
                "manifest": manifest_relative,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    exclusive_install(marker, content, mode=0o600)
    return marker


def remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
        fsync_directory(path.parent)


def private_artifact_warnings(scratch: Path) -> list[str]:
    warnings = []
    staging_root = scratch / STAGING_RELATIVE_PATH
    if staging_root.exists():
        require_real_directory(staging_root)
        for path in sorted(staging_root.iterdir(), key=lambda value: value.name):
            warnings.append(f"unreclaimed private staging path: {path}")

    conversations_root = scratch / "_conversations"
    if conversations_root.exists():
        require_real_directory(conversations_root)
        for path in sorted(
            conversations_root.glob(".write-lock.stale-*"),
            key=lambda value: value.name,
        ):
            warnings.append(f"archived stale publication lock: {path}")
    return warnings


def preflight_candidates(
    project: Path,
    candidate_root: Path,
    checkpoint_relative: str,
    conversation_ids: tuple[str, ...],
) -> None:
    checkpoint = candidate_root / checkpoint_relative
    errors, warnings = validate_conversations.validate_project(
        project,
        checkpoint,
        set(conversation_ids),
        conversation_root=candidate_root,
    )
    relevant_paths = {
        checkpoint,
        *(candidate_root / conversation_id / "CONVERSATION.md" for conversation_id in conversation_ids),
    }
    relevant_warnings = [
        warning
        for warning in warnings
        if any(str(path) in warning for path in relevant_paths)
    ]
    if errors:
        raise PublishError("invalid-request", "candidate validation failed", errors)
    if relevant_warnings:
        raise PublishError("request-review-required", "candidate warnings require review", relevant_warnings)


def verify_bases(
    scratch: Path,
    request: PublishRequest,
) -> tuple[Optional[bytes], dict[str, Optional[bytes]]]:
    index_path = scratch / INDEX_RELATIVE_PATH
    index = check_expectation(index_path, request.index_base)
    if index is not None:
        parse_managed(index_path)
    currents: dict[str, Optional[bytes]] = {}
    for candidate in request.conversations:
        path = scratch / candidate.conversation_id / "CONVERSATION.md"
        content = check_expectation(path, candidate.base)
        if content is not None:
            parse_managed(path)
        currents[candidate.conversation_id] = content
    return index, currents


def prepare_stage(
    project: Path,
    scratch: Path,
    stage: Path,
    token: str,
    request: PublishRequest,
    checkpoint_name: str,
    index_previous: Optional[bytes],
    current_previous: dict[str, Optional[bytes]],
) -> tuple[RecoveryTransaction, dict[str, bytes]]:
    candidate_root = stage / "candidate"
    previous_root = stage / "previous"
    safe_mkdir(stage)
    safe_mkdir(candidate_root)
    safe_mkdir(previous_root)

    checkpoint_relative = f"{SESSION_DIRECTORY.as_posix()}/{checkpoint_name}"
    checkpoint_content = request.checkpoint_content.replace(CHECKPOINT_PLACEHOLDER, checkpoint_name).encode("utf-8")
    candidate_bytes: dict[str, bytes] = {checkpoint_relative: checkpoint_content}
    current_paths: dict[str, Path] = {}
    for candidate in request.conversations:
        relative = f"{candidate.conversation_id}/CONVERSATION.md"
        content = candidate.content.replace(CHECKPOINT_PLACEHOLDER, checkpoint_name).encode("utf-8")
        candidate_bytes[relative] = content
        current_paths[candidate.conversation_id] = stage_file(candidate_root, relative, content)
    stage_file(candidate_root, checkpoint_relative, checkpoint_content)

    index_content = build_index(index_previous, current_paths, scratch)
    candidate_bytes[INDEX_RELATIVE_PATH.as_posix()] = index_content
    stage_file(candidate_root, INDEX_RELATIVE_PATH.as_posix(), index_content)

    previous_by_relative: dict[str, Optional[bytes]] = {
        INDEX_RELATIVE_PATH.as_posix(): index_previous,
        **{
            f"{conversation_id}/CONVERSATION.md": content
            for conversation_id, content in current_previous.items()
        },
        checkpoint_relative: None,
    }
    existing: dict[str, str] = {}
    absent: list[str] = []
    for relative in canonical_relative_paths(
        tuple(candidate.conversation_id for candidate in request.conversations),
        checkpoint_name,
    ):
        previous = previous_by_relative[relative]
        if previous is None:
            absent.append(relative)
        else:
            existing[relative] = sha256_bytes(previous)
            stage_file(previous_root, relative, previous)

    candidate_hashes = {
        relative: sha256_bytes(content)
        for relative, content in candidate_bytes.items()
    }
    created_directories = []
    sessions = scratch / SESSION_DIRECTORY
    if not sessions.exists():
        created_directories.append(SESSION_DIRECTORY.as_posix())
    for candidate in request.conversations:
        directory = scratch / candidate.conversation_id
        if not directory.exists():
            created_directories.append(candidate.conversation_id)

    manifest = {
        "managed_by": MANAGER,
        "transaction_version": TRANSACTION_VERSION,
        "token": token,
        "new_checkpoint": checkpoint_relative,
        "existing": existing,
        "absent": sorted(absent),
        "candidate": candidate_hashes,
        "created_directories": sorted(created_directories),
    }
    manifest_path = stage / "manifest.json"
    durable_json(manifest_path, manifest)
    preflight_candidates(
        project,
        candidate_root,
        checkpoint_relative,
        tuple(candidate.conversation_id for candidate in request.conversations),
    )
    return (
        RecoveryTransaction(
            token=token,
            stage=stage,
            manifest_path=manifest_path,
            existing=existing,
            absent=tuple(sorted(absent)),
            candidate=candidate_hashes,
            new_checkpoint=checkpoint_relative,
            created_directories=tuple(sorted(created_directories)),
        ),
        candidate_bytes,
    )


def safe_target_path(scratch: Path, relative: str) -> Path:
    if not managed_target(relative) or "\\" in relative:
        raise PublishError("recovery-required", f"unexpected canonical path in recovery data: {relative!r}")
    path = scratch / Path(relative)
    if path.resolve(strict=False) != (scratch.resolve() / Path(relative)):
        raise PublishError("recovery-required", f"recovery path escapes .scratch: {relative!r}")
    return path


def recovery_file(stage: Path, section: str, relative: str) -> Path:
    section_root = stage / section
    require_real_directory(section_root)
    path = section_root / Path(relative)
    if path.resolve(strict=False) != section_root.resolve() / Path(relative):
        raise PublishError("recovery-required", f"staged path escapes its transaction: {path}")
    parent = section_root
    for component in Path(relative).parts[:-1]:
        parent = parent / component
        require_real_directory(parent)
    require_regular_file(path)
    return path


def validate_recovery(scratch: Path) -> RecoveryTransaction:
    marker_path = scratch / MARKER_RELATIVE_PATH
    marker = strict_json_load(marker_path)
    expected_marker_keys = {"managed_by", "transaction_version", "token", "manifest"}
    if set(marker) != expected_marker_keys:
        raise PublishError("recovery-required", f"recovery marker has unexpected fields: {marker_path}")
    token = marker.get("token")
    if (
        marker.get("managed_by") != MANAGER
        or marker.get("transaction_version") != TRANSACTION_VERSION
        or not isinstance(token, str)
        or not re.fullmatch(r"[0-9a-f]{32}", token)
    ):
        raise PublishError("recovery-required", f"recovery marker metadata is invalid: {marker_path}")
    expected_manifest_relative = f"{STAGING_RELATIVE_PATH.as_posix()}/{token}/manifest.json"
    if marker.get("manifest") != expected_manifest_relative:
        raise PublishError("recovery-required", f"recovery marker manifest path is invalid: {marker_path}")

    stage = scratch / STAGING_RELATIVE_PATH / token
    require_real_directory(stage)
    manifest_path = stage / "manifest.json"
    manifest = strict_json_load(manifest_path)
    expected_manifest_keys = {
        "managed_by",
        "transaction_version",
        "token",
        "new_checkpoint",
        "existing",
        "absent",
        "candidate",
        "created_directories",
    }
    if set(manifest) != expected_manifest_keys:
        raise PublishError("recovery-required", f"manifest has unexpected fields: {manifest_path}")
    if (
        manifest.get("managed_by") != MANAGER
        or manifest.get("transaction_version") != TRANSACTION_VERSION
        or manifest.get("token") != token
    ):
        raise PublishError("recovery-required", f"manifest metadata is invalid: {manifest_path}")

    existing = manifest.get("existing")
    absent = manifest.get("absent")
    candidate = manifest.get("candidate")
    new_checkpoint = manifest.get("new_checkpoint")
    created_directories = manifest.get("created_directories")
    if (
        not isinstance(existing, dict)
        or not isinstance(absent, list)
        or not isinstance(candidate, dict)
        or not isinstance(new_checkpoint, str)
        or not isinstance(created_directories, list)
    ):
        raise PublishError("recovery-required", f"manifest collections are invalid: {manifest_path}")
    if (
        any(not isinstance(key, str) or not isinstance(value, str) for key, value in existing.items())
        or any(not isinstance(value, str) for value in absent)
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in candidate.items())
        or any(not isinstance(value, str) for value in created_directories)
    ):
        raise PublishError("recovery-required", f"manifest collection values are invalid: {manifest_path}")
    if len(absent) != len(set(absent)):
        raise PublishError("recovery-required", f"manifest contains duplicate absent paths: {manifest_path}")
    if set(existing) & set(absent) or set(candidate) != set(existing) | set(absent):
        raise PublishError("recovery-required", f"manifest target collections disagree: {manifest_path}")
    if new_checkpoint not in absent or not new_checkpoint.startswith(f"{SESSION_DIRECTORY.as_posix()}/"):
        raise PublishError("recovery-required", f"manifest checkpoint is invalid: {manifest_path}")
    checkpoint_targets = [
        relative
        for relative in candidate
        if relative.startswith(f"{SESSION_DIRECTORY.as_posix()}/")
    ]
    current_targets = [
        relative
        for relative in candidate
        if re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*/CONVERSATION\.md$", relative)
    ]
    if (
        len(checkpoint_targets) != 1
        or checkpoint_targets[0] != new_checkpoint
        or INDEX_RELATIVE_PATH.as_posix() not in candidate
        or not current_targets
        or any(not managed_target(relative) for relative in candidate)
    ):
        raise PublishError("recovery-required", f"manifest target scope is invalid: {manifest_path}")

    allowed_created = {SESSION_DIRECTORY.as_posix()} | {
        relative.removesuffix("/CONVERSATION.md") for relative in current_targets
    }
    if (
        len(created_directories) != len(set(created_directories))
        or any(relative not in allowed_created for relative in created_directories)
    ):
        raise PublishError("recovery-required", f"manifest created directories are invalid: {manifest_path}")

    for relative, expected_hash in candidate.items():
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise PublishError("recovery-required", f"invalid candidate hash in manifest: {relative}")
        path = recovery_file(stage, "candidate", relative)
        if sha256_file(path) != expected_hash:
            raise PublishError("recovery-required", f"candidate hash mismatch: {path}")
        parse_managed(path)
    for relative, expected_hash in existing.items():
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise PublishError("recovery-required", f"invalid previous hash in manifest: {relative}")
        path = recovery_file(stage, "previous", relative)
        if sha256_file(path) != expected_hash:
            raise PublishError("recovery-required", f"previous hash mismatch: {path}")
        parse_managed(path)

    transaction = RecoveryTransaction(
        token=token,
        stage=stage,
        manifest_path=manifest_path,
        existing={str(key): str(value) for key, value in existing.items()},
        absent=tuple(str(value) for value in absent),
        candidate={str(key): str(value) for key, value in candidate.items()},
        new_checkpoint=new_checkpoint,
        created_directories=tuple(str(value) for value in created_directories),
    )
    preflight_recovery_targets(scratch, transaction)
    return transaction


def preflight_recovery_targets(scratch: Path, transaction: RecoveryTransaction) -> None:
    for relative in transaction.candidate:
        target = safe_target_path(scratch, relative)
        if target.exists() or target.is_symlink():
            require_regular_file(target)
            live_hash = sha256_file(target)
        else:
            live_hash = None
        permitted = {transaction.candidate[relative]}
        if relative in transaction.existing:
            permitted.add(transaction.existing[relative])
            permitted.add(None)
        else:
            permitted.add(None)
        if live_hash not in permitted:
            raise PublishError("recovery-required", f"canonical file has unexpected bytes: {target}")


def rollback_transaction(scratch: Path, transaction: RecoveryTransaction) -> list[str]:
    preflight_recovery_targets(scratch, transaction)
    for relative in sorted(transaction.existing):
        target = safe_target_path(scratch, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        previous = recovery_file(transaction.stage, "previous", relative)
        atomic_replace_bytes(target, previous.read_bytes(), mode=0o644)
    for relative in sorted(transaction.absent, reverse=True):
        target = safe_target_path(scratch, relative)
        if target.exists():
            if sha256_file(target) != transaction.candidate[relative]:
                raise PublishError("recovery-required", f"canonical file changed during recovery: {target}")
            target.unlink()
            fsync_directory(target.parent)

    for relative, expected_hash in transaction.existing.items():
        target = safe_target_path(scratch, relative)
        if not target.is_file() or sha256_file(target) != expected_hash:
            raise PublishError("recovery-required", f"restored file does not match its backup: {target}")
    for relative in transaction.absent:
        target = safe_target_path(scratch, relative)
        if target.exists() or target.is_symlink():
            raise PublishError("recovery-required", f"new target remains after recovery: {target}")

    for relative in sorted(transaction.created_directories, key=lambda value: value.count("/"), reverse=True):
        directory = scratch / Path(relative)
        try:
            directory.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    warnings = []
    preserve_stage = True
    marker = scratch / MARKER_RELATIVE_PATH
    marker.unlink()
    try:
        fsync_directory(marker.parent)
    except OSError as error:
        warnings.append(
            "could not confirm durable removal of the recovery marker; "
            f"preserved staging directory {transaction.stage}: {error}"
        )
    else:
        preserve_stage = False
    if not preserve_stage:
        try:
            remove_tree(transaction.stage)
        except OSError as error:
            warnings.append(f"could not remove recovered staging directory {transaction.stage}: {error}")
    return warnings


def publish(project: Path, request: PublishRequest, wait_seconds: float = 30) -> dict[str, object]:
    project = project.expanduser().resolve()
    conversation_ids = tuple(candidate.conversation_id for candidate in request.conversations)
    safe_store_check(project, conversation_ids)
    scratch = project / ".scratch"
    created_private_directories: list[Path] = []
    safe_mkdir(scratch, created_private_directories)
    conversations_root = scratch / "_conversations"
    safe_mkdir(conversations_root, created_private_directories)
    staging_root = scratch / STAGING_RELATIVE_PATH
    safe_mkdir(staging_root, created_private_directories)
    marker = scratch / MARKER_RELATIVE_PATH

    token: Optional[str] = None
    stage: Optional[Path] = None
    marker_installed = False
    committed = False
    preserve_stage = False
    prior_private_warnings: list[str] = []
    try:
        token = conversation_lock.acquire(project, wait_seconds=wait_seconds, stale_seconds=1800)
        safe_store_check(project, conversation_ids)
        if marker.exists() or marker.is_symlink():
            raise PublishError("recovery-required", f"recovery marker exists: {marker}")
        prior_private_warnings = private_artifact_warnings(scratch)

        index_previous, current_previous = verify_bases(scratch, request)
        sessions = scratch / SESSION_DIRECTORY
        checkpoint_name = reserve_checkpoint_name(sessions, request.checkpoint_name)
        checkpoint_path = sessions / checkpoint_name

        stage = staging_root / token
        transaction, candidate_bytes = prepare_stage(
            project,
            scratch,
            stage,
            token,
            request,
            checkpoint_name,
            index_previous,
            current_previous,
        )

        conversation_lock.refresh(project, token)
        verify_bases(scratch, request)
        if checkpoint_path.exists() or checkpoint_path.is_symlink():
            raise PublishError("conflict", f"reserved checkpoint path is no longer absent: {checkpoint_path}")

        install_marker(
            scratch,
            token,
            f"{STAGING_RELATIVE_PATH.as_posix()}/{token}/manifest.json",
        )
        marker_installed = True
        conversation_lock.refresh(project, token)

        created_canonical: list[Path] = []
        safe_mkdir(sessions, created_canonical)
        exclusive_install(checkpoint_path, candidate_bytes[transaction.new_checkpoint])
        for conversation_id in sorted(conversation_ids):
            directory = scratch / conversation_id
            safe_mkdir(directory, created_canonical)
            relative = f"{conversation_id}/CONVERSATION.md"
            atomic_replace_bytes(
                directory / "CONVERSATION.md",
                candidate_bytes[relative],
                mode=0o644,
            )
        atomic_replace_bytes(
            scratch / INDEX_RELATIVE_PATH,
            candidate_bytes[INDEX_RELATIVE_PATH.as_posix()],
            mode=0o644,
        )

        errors, warnings = validate_conversations.validate_project(
            project,
            checkpoint_path,
            set(conversation_ids),
        )
        relevant_paths = {
            checkpoint_path,
            *(scratch / conversation_id / "CONVERSATION.md" for conversation_id in conversation_ids),
        }
        relevant_warnings = [
            warning
            for warning in warnings
            if any(str(path) in warning for path in relevant_paths)
        ]
        if errors or relevant_warnings:
            raise PublishError(
                "publication-failed",
                "published candidates failed canonical validation",
                [*errors, *relevant_warnings],
            )
        for relative, expected_hash in transaction.candidate.items():
            target = scratch / Path(relative)
            if not target.is_file() or sha256_file(target) != expected_hash:
                raise PublishError("publication-failed", f"published bytes do not match candidate: {target}")

        cleanup_warnings = list(prior_private_warnings)
        durability_warnings = []
        preserve_stage = True
        marker.unlink()
        marker_installed = False
        committed = True
        try:
            fsync_directory(marker.parent)
        except OSError as error:
            durability_warnings.append(
                "could not confirm durable removal of the recovery marker; "
                f"preserved staging directory {stage}: {error}"
            )
        else:
            preserve_stage = False
        if not durability_warnings:
            try:
                remove_tree(stage)
                stage = None
            except OSError as error:
                cleanup_warnings.append(f"could not remove staging directory {stage}: {error}")
        try:
            conversation_lock.release(project, token)
            token = None
        except (OSError, ValueError) as error:
            cleanup_warnings.append(f"could not release publication lock: {error}")

        if durability_warnings:
            status = "published-with-durability-warning"
        elif cleanup_warnings:
            status = "published-with-cleanup-warning"
        else:
            status = "published"
        return json_output(
            status,
            checkpoint=f".scratch/{transaction.new_checkpoint}",
            conversations=list(sorted(conversation_ids)),
            warnings=[*durability_warnings, *cleanup_warnings],
        )
    except (OSError, TimeoutError, ValueError, PublishError) as caught:
        error = (
            caught
            if isinstance(caught, PublishError)
            else PublishError("publication-failed", str(caught))
        )
        marker_installed = marker.exists() or marker.is_symlink()
        if marker_installed:
            preserve_stage = True
            try:
                transaction = validate_recovery(scratch)
                recovery_warnings = rollback_transaction(scratch, transaction)
                stage = None
                preserve_stage = False
                marker_installed = False
                if token is not None:
                    try:
                        conversation_lock.release(project, token)
                    except (OSError, ValueError) as release_error:
                        recovery_warnings.append(f"could not release publication lock: {release_error}")
                    token = None
                rollback_status = (
                    "rolled-back-with-cleanup-warning"
                    if recovery_warnings
                    else "rolled-back"
                )
                return json_output(
                    rollback_status,
                    error=error.message,
                    details=error.details,
                    warnings=recovery_warnings,
                )
            except (OSError, PublishError, ValueError) as recovery_error:
                if token is not None:
                    try:
                        conversation_lock.release(project, token)
                    except (OSError, ValueError):
                        pass
                    token = None
                details = list(error.details)
                details.append(str(recovery_error))
                raise PublishError(
                    "recovery-required",
                    f"publication failed and automatic recovery could not finish: {error.message}",
                    details,
                ) from recovery_error
        raise
    finally:
        marker_present = marker.exists() or marker.is_symlink()
        if not committed and not marker_present and not preserve_stage:
            if stage is not None:
                try:
                    remove_tree(stage)
                except OSError:
                    pass
            if token is not None:
                try:
                    conversation_lock.release(project, token)
                except (OSError, ValueError):
                    pass
            for directory in reversed(created_private_directories):
                try:
                    directory.rmdir()
                except OSError:
                    pass


def recover(project: Path, wait_seconds: float = 30) -> dict[str, object]:
    project = project.expanduser().resolve()
    safe_store_check(project)
    scratch = project / ".scratch"
    marker = scratch / MARKER_RELATIVE_PATH
    lock = project / conversation_lock.LOCK_RELATIVE_PATH
    if (
        not marker.exists()
        and not marker.is_symlink()
        and not lock.exists()
        and not lock.is_symlink()
    ):
        return json_output("no-recovery-needed")

    token = conversation_lock.acquire(project, wait_seconds=wait_seconds, stale_seconds=1800)
    result: Optional[dict[str, object]] = None
    failure: Optional[PublishError] = None
    try:
        safe_store_check(project)
        if not marker.exists() and not marker.is_symlink():
            result = json_output("no-recovery-needed", warnings=[])
        else:
            try:
                transaction = validate_recovery(scratch)
                warnings = rollback_transaction(scratch, transaction)
            except PublishError as error:
                if error.status == "recovery-required":
                    failure = error
                else:
                    failure = PublishError("recovery-required", error.message, error.details)
            else:
                status = (
                    "recovered-with-cleanup-warning"
                    if warnings
                    else "recovered-retry-required"
                )
                result = json_output(
                    status,
                    restored=list(sorted(transaction.existing)),
                    removed=list(sorted(transaction.absent)),
                    warnings=warnings,
                )
    except (OSError, ValueError) as error:
        failure = PublishError("recovery-required", str(error))

    release_warning = None
    try:
        conversation_lock.release(project, token)
    except (OSError, ValueError) as error:
        release_warning = f"could not release publication lock: {error}"

    if failure is not None:
        if release_warning:
            failure.details.append(release_warning)
        raise failure
    assert result is not None
    if release_warning:
        warnings = result.setdefault("warnings", [])
        assert isinstance(warnings, list)
        warnings.append(release_warning)
        if result["status"] == "recovered-retry-required":
            result["status"] = "recovered-with-cleanup-warning"
        elif result["status"] == "no-recovery-needed":
            result["status"] = "cleanup-failed"
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot_parser = subparsers.add_parser("snapshot", help="Read publication base hashes")
    snapshot_parser.add_argument("project", type=Path)
    snapshot_parser.add_argument("--conversation", action="append", required=True, dest="conversations")
    snapshot_parser.add_argument("--wait", type=float, default=30)

    publish_parser = subparsers.add_parser("publish", help="Validate and publish one request")
    publish_parser.add_argument("project", type=Path)
    publish_parser.add_argument("--request", default="-", help="Request file, or - for stdin")
    publish_parser.add_argument("--wait", type=float, default=30)

    recover_parser = subparsers.add_parser("recover", help="Restore a validated interrupted publication")
    recover_parser.add_argument("project", type=Path)
    recover_parser.add_argument("--wait", type=float, default=30)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "snapshot":
            if args.wait < 0:
                raise PublishError("invalid-request", "--wait cannot be negative")
            result = snapshot(
                args.project,
                tuple(sorted(set(args.conversations))),
                args.wait,
            )
        elif args.command == "recover":
            if args.wait < 0:
                raise PublishError("invalid-request", "--wait cannot be negative")
            result = recover(args.project, args.wait)
        else:
            if args.wait < 0:
                raise PublishError("invalid-request", "--wait cannot be negative")
            result = publish(args.project, parse_request(read_request(args.request)), args.wait)
    except PublishError as error:
        result = json_output(error.status, error=error.message, details=error.details)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1
    except (OSError, TimeoutError, ValueError) as error:
        result = json_output("failed", error=str(error), details=[])
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] in {
        "snapshot",
        "published",
        "published-with-cleanup-warning",
        "no-recovery-needed",
        "recovered-retry-required",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
