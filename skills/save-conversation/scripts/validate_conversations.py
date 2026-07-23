#!/usr/bin/env python3
"""Validate managed conversation structure and flag claims that need semantic review."""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
import re
import sys
from typing import Any, Optional
from urllib.parse import unquote


MANAGER = "conversation-continuity"
SCHEMA_VERSION = 1
STATUSES = ["waiting-user", "ready", "waiting-external", "parked", "closed"]
MODES = ["standalone", "fluent-linked"]
ITEM_STATES = ["ready", "waiting-user", "waiting-external", "deferred", "done", "abandoned"]
AGENTS = ["claude", "codex", "other"]
REASONS = ["manual", "session-end", "pre-compaction", "logical-boundary"]
COVERAGE_VALUES = ["full-visible-context", "partial"]
CONVERSATION_SECTIONS = [
    "Resume",
    "Intent",
    "Current state",
    "Decisions",
    "Changes",
    "Evidence",
    "Open questions and risks",
    "Artifacts",
    "History",
]
SESSION_SECTIONS = [
    "Resume",
    "User direction",
    "What happened",
    "Decisions",
    "Changes and side effects",
    "Verification",
    "Open loops",
    "State snapshot",
    "Coverage gaps",
]
RISKY_CLAIMS = [
    (re.compile(r"\beverything(?:\s+else)?\b.{0,80}\b(?:done|complete|pushed)\b", re.I), "blanket completion"),
    (
        re.compile(r"\ball\s+(?:work|tasks?|changes?|items?)\b.{0,80}\b(?:done|complete|pushed)\b", re.I),
        "blanket completion",
    ),
    (re.compile(r"\bthe\s+rest\b.{0,80}\b(?:done|complete|pushed)\b", re.I), "blanket completion"),
    (re.compile(r"\bfully\s+(?:done|complete)\b", re.I), "blanket completion"),
    (re.compile(r"\bfaithful\s+by\s+construction\b", re.I), "cross-boundary guarantee"),
]
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
CONVERSATION_LINK_RE = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)$")
CONVERSATION_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SESSION_NAME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{6}[+-]\d{4}-(claude|codex|other)(?:-\d+)?\.md$"
)
MISSING_LINK_RE = re.compile(r"\[missing\]\s+as of\s+(\S+)", re.I)


def issue(kind: str, path: Path, message: str) -> str:
    return f"{kind} {path}: {message}"


def parse_frontmatter(path: Path) -> tuple[dict[str, Any], list[str]]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing opening frontmatter delimiter")
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as error:
        raise ValueError("missing closing frontmatter delimiter") from error

    values: dict[str, Any] = {}
    current_list: Optional[str] = None
    for line in lines[1:end]:
        key_match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:\s*(.*))?$", line)
        if key_match:
            key, raw_value = key_match.groups()
            raw_value = (raw_value or "").strip()
            if raw_value:
                values[key] = raw_value.strip("\"'")
                current_list = None
            else:
                values[key] = []
                current_list = key
            continue
        list_match = re.match(r"^\s+-\s+(.+?)\s*$", line)
        if list_match and current_list:
            values[current_list].append(list_match.group(1).strip("\"'"))
    return values, lines[end + 1 :]


def version_of(frontmatter: dict[str, Any]) -> Optional[int]:
    try:
        return int(frontmatter.get("conversation_version"))
    except (TypeError, ValueError):
        return None


def section_map(body_lines: list[str]) -> tuple[dict[str, list[str]], set[str]]:
    sections: dict[str, list[str]] = {}
    duplicates: set[str] = set()
    current: Optional[str] = None
    fence: Optional[str] = None
    for line in body_lines:
        stripped = line.lstrip()
        fence_match = re.match(r"^(```|~~~)", stripped)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            if current:
                sections[current].append(line)
            continue
        if fence is not None:
            if current:
                sections[current].append(line)
            continue
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            current = heading.group(1)
            if current in sections:
                duplicates.add(current)
            sections[current] = []
        elif current:
            sections[current].append(line)
    return sections, duplicates


def first_content_line(lines: list[str]) -> str:
    return next((line.strip() for line in lines if line.strip()), "")


def valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def resolve_link(source: Path, target: str) -> Optional[Path]:
    target = target.strip().strip("<>")
    if not target or target.startswith("#") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target):
        return None
    target = unquote(target.split("#", 1)[0])
    candidate = Path(target)
    return candidate if candidate.is_absolute() else (source.parent / candidate).resolve()


def validate_links(path: Path, errors: list[str], warnings: list[str]) -> None:
    current_section: Optional[str] = None
    fence: Optional[str] = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        fence_match = re.match(r"^(```|~~~)", stripped)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            continue
        if fence is not None:
            continue
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            current_section = heading.group(1)
        for target in LINK_RE.findall(line):
            resolved = resolve_link(path, target)
            if resolved is None or resolved.exists():
                continue
            missing_match = MISSING_LINK_RE.search(line)
            if current_section == "Legacy records" and missing_match:
                if valid_timestamp(missing_match.group(1)):
                    warnings.append(issue("WARNING", path, f"legacy link is marked missing: {target}"))
                else:
                    errors.append(issue("ERROR", path, f"missing-link marker has an invalid timestamp: {target}"))
                continue
            errors.append(issue("ERROR", path, f"broken link: {target}"))


def validate_sections(
    path: Path,
    sections: dict[str, list[str]],
    duplicates: set[str],
    required: list[str],
    errors: list[str],
    require_content: bool,
) -> None:
    for name in sorted(duplicates):
        errors.append(issue("ERROR", path, f"duplicate section: ## {name}"))
    for name in required:
        if name not in sections:
            errors.append(issue("ERROR", path, f"missing section: ## {name}"))
        elif require_content and not first_content_line(sections[name]):
            errors.append(issue("ERROR", path, f"empty section: ## {name}; write `None known.` when appropriate"))


def scan_risky_claims(path: Path, warnings: list[str]) -> None:
    text = " ".join(path.read_text(encoding="utf-8").split())
    for pattern, label in RISKY_CLAIMS:
        for match in pattern.finditer(text):
            excerpt = " ".join(match.group(0).split())
            warnings.append(issue("WARNING", path, f"review {label} claim: {excerpt!r}"))
    if re.search(r"\bMemory index\b|\bmemory://", text, re.I):
        warnings.append(issue("WARNING", path, "client-specific memory locator may not be portable"))


def validate_evidence(path: Path, section_name: str, lines: list[str], errors: list[str]) -> None:
    content = "\n".join(lines).strip()
    if content.lower().rstrip(".") == "none known":
        return
    claim_indexes = [index for index, line in enumerate(lines) if line.startswith("- Claim:")]
    if not claim_indexes:
        errors.append(issue("ERROR", path, f"## {section_name} must use Claim/Basis/Source/Result or `None known.`"))
        return
    claim_indexes.append(len(lines))
    for start, end in zip(claim_indexes, claim_indexes[1:]):
        block = lines[start:end]
        claim = block[0].strip()
        basis = next((line.strip() for line in block if line.strip().startswith("- Basis:")), "")
        source = next((line.strip() for line in block if line.strip().startswith("- Source:")), "")
        result = next((line.strip() for line in block if line.strip().startswith("- Result:")), "")
        checked = next((line.strip() for line in block if line.strip().startswith("- Checked:")), "")
        basis_match = re.match(r"^- Basis: (verified-now|artifact-backed|reported|inferred)\.?$", basis)
        if not re.match(r"^- Claim: \S", claim):
            errors.append(issue("ERROR", path, f"claim text is empty in ## {section_name}"))
        if not basis_match:
            errors.append(issue("ERROR", path, f"claim lacks a valid Basis in ## {section_name}"))
        if not re.match(r"^- Source: \S", source):
            errors.append(issue("ERROR", path, f"claim lacks a Source in ## {section_name}"))
        if not re.match(r"^- Result: \S", result):
            errors.append(issue("ERROR", path, f"claim lacks a Result in ## {section_name}"))
        if basis_match and basis_match.group(1) == "verified-now":
            checked_value = checked.removeprefix("- Checked:").strip().rstrip(".")
            if not valid_timestamp(checked_value):
                errors.append(
                    issue("ERROR", path, f"verified-now claim lacks a valid Checked timestamp in ## {section_name}")
                )


def parse_fluent_mapping(path: Path) -> tuple[bool, dict[str, list[str]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    mapping = {"draft_ids": [], "work_item_ids": []}
    in_fluent = False
    current_list: Optional[str] = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line == "fluent:":
            in_fluent = True
            continue
        if not in_fluent:
            continue
        if line and not line.startswith(" "):
            break
        key_match = re.match(r"^  (draft_ids|work_item_ids):\s*$", line)
        if key_match:
            current_list = key_match.group(1)
            continue
        item_match = re.match(r"^    -\s+(.+?)\s*$", line)
        if item_match and current_list:
            mapping[current_list].append(item_match.group(1).strip("\"'"))
    return in_fluent, mapping


def expected_resume_prefix(status: str, first_line: str) -> bool:
    patterns = {
        "ready": r"^Ready for agent:\s+\S",
        "waiting-user": r"^Waiting for user:\s+\S",
        "waiting-external": r"^Waiting for (?!user:)[^:]+:\s+\S",
        "parked": r"^Parked:\s+\S",
        "closed": r"^Closed:\s+\S",
    }
    pattern = patterns.get(status)
    return bool(pattern and re.match(pattern, first_line))


def validate_current_items(
    path: Path,
    status: str,
    first_resume_line: str,
    lines: list[str],
    errors: list[str],
) -> None:
    item_lines = [line.strip() for line in lines if line.startswith("- ")]
    if not item_lines:
        errors.append(issue("ERROR", path, "Current state must contain state-labeled items"))
        return

    states: list[str] = []
    parsed_items: list[tuple[str, str, str]] = []
    subjects: set[str] = set()
    pattern = re.compile(r"^- \[([^]]+)\]\s+([^:]+):\s+(\S.*)$")
    for line in item_lines:
        match = pattern.match(line)
        if not match or match.group(1) not in ITEM_STATES:
            errors.append(issue("ERROR", path, f"invalid Current state item: {line}"))
            continue
        state, subject, detail = match.groups()
        normalized_subject = " ".join(subject.lower().split())
        if normalized_subject in subjects:
            errors.append(issue("ERROR", path, f"Current state repeats bounded item: {subject.strip()!r}"))
        subjects.add(normalized_subject)
        states.append(state)
        parsed_items.append((state, subject.strip(), detail))

    if status in STATUSES[:3] and status not in states:
        errors.append(issue("ERROR", path, f"Current state has no item matching conversation status {status!r}"))
    if status == "parked" and any(state in STATUSES[:3] for state in states):
        errors.append(issue("ERROR", path, "parked conversation contains an actionable or waiting item"))
    if status == "parked" and "deferred" not in states:
        errors.append(issue("ERROR", path, "parked conversation has no deferred Current state item"))
    if status == "closed" and any(state not in ("done", "abandoned") for state in states):
        errors.append(issue("ERROR", path, "closed conversation contains a nonterminal item"))

    if status in STATUSES[:3] and ": " in first_resume_line:
        resume_instruction = first_resume_line.split(": ", 1)[1]
        matching_details = [detail for state, _, detail in parsed_items if state == status]
        normalized_instruction = " ".join(resume_instruction.rstrip(".?!").lower().split())
        matching_subjects = [
            " ".join(subject.rstrip(".?!").lower().split())
            for state, subject, _ in parsed_items
            if state == status
        ]
        if resume_instruction not in matching_details and normalized_instruction not in matching_subjects:
            errors.append(
                issue(
                    "ERROR",
                    path,
                    f"first Resume instruction must match one [{status}] Current state subject or detail",
                )
            )


def validate_conversation(path: Path, errors: list[str], warnings: list[str]) -> Optional[dict[str, Any]]:
    try:
        frontmatter, body = parse_frontmatter(path)
    except (OSError, ValueError) as error:
        errors.append(issue("ERROR", path, str(error)))
        return None
    if frontmatter.get("managed_by") != MANAGER:
        return None

    version = version_of(frontmatter)
    if version != SCHEMA_VERSION:
        errors.append(issue("ERROR", path, f"conversation_version must be {SCHEMA_VERSION}"))
        return frontmatter

    status = frontmatter.get("status")
    if status not in STATUSES:
        errors.append(issue("ERROR", path, f"invalid status: {status!r}"))
    if frontmatter.get("mode") not in MODES:
        errors.append(issue("ERROR", path, f"invalid mode: {frontmatter.get('mode')!r}"))
    if frontmatter.get("conversation") != path.parent.name:
        errors.append(issue("ERROR", path, "frontmatter conversation does not match the containing directory"))
    if not CONVERSATION_NAME_RE.match(str(frontmatter.get("conversation", ""))):
        errors.append(issue("ERROR", path, "conversation must be lowercase hyphenated text"))
    if not valid_timestamp(frontmatter.get("updated_at")):
        errors.append(issue("ERROR", path, "updated_at must be ISO-8601 with an offset"))

    sections, duplicates = section_map(body)
    validate_sections(path, sections, duplicates, CONVERSATION_SECTIONS, errors, require_content=True)
    first_resume_line = first_content_line(sections.get("Resume", []))
    frontmatter["_first_resume_line"] = first_resume_line
    frontmatter["_path"] = path
    if not expected_resume_prefix(str(status), first_resume_line):
        errors.append(issue("ERROR", path, f"Resume first line does not match status {status!r}"))
    if len(first_resume_line) > 120:
        errors.append(issue("ERROR", path, "Resume first line exceeds 120 characters"))
    if "|" in first_resume_line:
        errors.append(issue("ERROR", path, "Resume first line cannot contain a pipe"))
    validate_current_items(
        path,
        str(status),
        first_resume_line,
        sections.get("Current state", []),
        errors,
    )
    validate_evidence(path, "Evidence", sections.get("Evidence", []), errors)

    has_fluent_mapping, fluent_mapping = parse_fluent_mapping(path)
    if frontmatter.get("mode") == "fluent-linked":
        if "Fluent" not in sections or not first_content_line(sections.get("Fluent", [])):
            errors.append(issue("ERROR", path, "fluent-linked conversation requires a nonempty ## Fluent section"))
        if not has_fluent_mapping or not any(fluent_mapping.values()):
            errors.append(issue("ERROR", path, "fluent-linked conversation requires at least one Fluent identifier"))
    elif has_fluent_mapping:
        errors.append(issue("ERROR", path, "standalone conversation cannot contain a Fluent mapping"))

    latest_checkpoint = frontmatter.get("latest_checkpoint")
    if not isinstance(latest_checkpoint, str):
        errors.append(issue("ERROR", path, "latest_checkpoint must be a relative path"))
    elif Path(latest_checkpoint).is_absolute():
        errors.append(issue("ERROR", path, "latest_checkpoint must be relative to CONVERSATION.md"))
    else:
        checkpoint_path = (path.parent / latest_checkpoint).resolve()
        frontmatter["_latest_checkpoint_path"] = checkpoint_path
        checkpoint_root = (path.parent.parent / "_conversations" / "sessions").resolve()
        if checkpoint_path.parent != checkpoint_root:
            errors.append(
                issue("ERROR", path, "latest_checkpoint must resolve inside .scratch/_conversations/sessions")
            )
        elif not checkpoint_path.is_file():
            errors.append(issue("ERROR", path, f"latest_checkpoint does not exist: {latest_checkpoint}"))
        else:
            try:
                checkpoint_frontmatter, _ = parse_frontmatter(checkpoint_path)
                if (
                    checkpoint_frontmatter.get("managed_by") != MANAGER
                    or version_of(checkpoint_frontmatter) != SCHEMA_VERSION
                ):
                    errors.append(issue("ERROR", checkpoint_path, "latest checkpoint is not a managed conversation record"))
                if frontmatter.get("conversation") not in checkpoint_frontmatter.get("conversations", []):
                    errors.append(issue("ERROR", path, "latest checkpoint does not list this conversation"))
            except (OSError, ValueError) as error:
                errors.append(issue("ERROR", checkpoint_path, str(error)))

    validate_links(path, errors, warnings)
    scan_risky_claims(path, warnings)
    return frontmatter


def parse_router_rows(
    path: Path,
    errors: list[str],
    selected: Optional[set[str]] = None,
) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = "| Conversation | Status | Mode | Updated | Resume |"
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == header)
    except StopIteration:
        errors.append(issue("ERROR", path, f"missing router header: {header}"))
        return []

    rows: list[dict[str, str]] = []
    for line in lines[start + 2 :]:
        if not line.strip().startswith("|"):
            break
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        label_match = re.match(r"^\[([^\]]+)\]", parts[0]) if parts else None
        applies_to_scope = selected is None or (
            label_match is not None and label_match.group(1) in selected
        )
        if len(parts) != 5:
            if applies_to_scope:
                errors.append(issue("ERROR", path, f"router row must contain five columns: {line.strip()}"))
            continue
        conversation_match = CONVERSATION_LINK_RE.match(parts[0])
        if not conversation_match:
            if applies_to_scope:
                errors.append(issue("ERROR", path, f"invalid conversation link: {parts[0]}"))
            continue
        if selected is not None and conversation_match.group(1) not in selected:
            continue
        rows.append(
            {
                "conversation": conversation_match.group(1),
                "link": conversation_match.group(2),
                "status": parts[1],
                "mode": parts[2],
                "updated_at": parts[3],
                "resume": parts[4],
            }
        )
    return rows


def validate_router(
    path: Path,
    conversations: dict[str, dict[str, Any]],
    errors: list[str],
    warnings: list[str],
    selected: Optional[set[str]] = None,
) -> Optional[int]:
    try:
        frontmatter, _ = parse_frontmatter(path)
    except (OSError, ValueError) as error:
        errors.append(issue("ERROR", path, str(error)))
        return None
    if frontmatter.get("managed_by") != MANAGER:
        errors.append(issue("ERROR", path, f"router is not managed by {MANAGER}"))
        return None
    version = version_of(frontmatter)
    if version != SCHEMA_VERSION:
        errors.append(issue("ERROR", path, f"conversation_version must be {SCHEMA_VERSION}"))
        return version
    if not valid_timestamp(frontmatter.get("updated_at")):
        errors.append(issue("ERROR", path, "updated_at must be ISO-8601 with an offset"))

    if any(version_of(conversation) != SCHEMA_VERSION for conversation in conversations.values()):
        errors.append(issue("ERROR", path, "router and Current Conversations must use the same schema version"))

    rows = parse_router_rows(path, errors, selected)
    rows_by_conversation = {row["conversation"]: row for row in rows}
    if selected is None and len(rows_by_conversation) != len(rows):
        errors.append(issue("ERROR", path, "router contains a duplicate conversation row"))
    elif selected is not None:
        for conversation_name in selected:
            if sum(row["conversation"] == conversation_name for row in rows) > 1:
                errors.append(issue("ERROR", path, f"router contains duplicate rows for: {conversation_name}"))
    if selected is None and set(rows_by_conversation) != set(conversations):
        errors.append(issue("ERROR", path, "router rows do not match managed conversation projections"))

    for conversation_name, conversation in conversations.items():
        row = rows_by_conversation.get(conversation_name)
        if not row:
            continue
        expected_link = str(Path(conversation_name) / "CONVERSATION.md")
        if row["link"] != expected_link:
            errors.append(issue("ERROR", path, f"{conversation_name}: expected link {expected_link!r}"))
        for field in ("status", "mode", "updated_at"):
            if row[field] != conversation.get(field):
                errors.append(issue("ERROR", path, f"{conversation_name}: router {field} differs from conversation frontmatter"))
        if row["resume"] != conversation.get("_first_resume_line"):
            errors.append(issue("ERROR", path, f"{conversation_name}: Resume must copy the first Resume line verbatim"))

    if selected is not None:
        for conversation_name in selected:
            if conversation_name not in rows_by_conversation:
                errors.append(issue("ERROR", path, f"router has no row for selected conversation: {conversation_name}"))
    else:
        order = {status: index for index, status in enumerate(STATUSES)}
        expected = sorted(
            rows,
            key=lambda row: (
                order.get(row["status"], len(order)),
                -dt.datetime.fromisoformat(row["updated_at"]).timestamp() if valid_timestamp(row["updated_at"]) else 0,
            ),
        )
        if rows != expected:
            errors.append(issue("ERROR", path, "router rows are not in status and newest-first order"))
        validate_links(path, errors, warnings)
    scan_risky_claims(path, warnings)
    return version


def validate_checkpoint(
    project: Path,
    session_arg: Path,
    conversations: dict[str, dict[str, Any]],
    errors: list[str],
    warnings: list[str],
    selected: Optional[set[str]] = None,
    exact_scope: bool = True,
) -> None:
    checkpoint_path = session_arg if session_arg.is_absolute() else project / session_arg
    checkpoint_path = checkpoint_path.resolve()
    checkpoint_root = (project / ".scratch" / "_conversations" / "sessions").resolve()
    if checkpoint_path.parent != checkpoint_root:
        errors.append(issue("ERROR", checkpoint_path, "new checkpoint must be inside .scratch/_conversations/sessions"))
    name_match = SESSION_NAME_RE.match(checkpoint_path.name)
    if not name_match:
        errors.append(issue("ERROR", checkpoint_path, "new checkpoint filename does not match the schema"))
    if not checkpoint_path.is_file():
        errors.append(issue("ERROR", checkpoint_path, "new checkpoint does not exist"))
        return
    try:
        frontmatter, body = parse_frontmatter(checkpoint_path)
    except (OSError, ValueError) as error:
        errors.append(issue("ERROR", checkpoint_path, str(error)))
        return
    if frontmatter.get("managed_by") != MANAGER or version_of(frontmatter) != SCHEMA_VERSION:
        errors.append(issue("ERROR", checkpoint_path, "new checkpoint must be a managed conversation record"))
    if not valid_timestamp(frontmatter.get("created_at")):
        errors.append(issue("ERROR", checkpoint_path, "created_at must be ISO-8601 with an offset"))
    agent = frontmatter.get("agent")
    if agent not in AGENTS:
        errors.append(issue("ERROR", checkpoint_path, f"invalid agent: {agent!r}"))
    elif name_match and name_match.group(1) != agent:
        errors.append(issue("ERROR", checkpoint_path, "checkpoint filename agent differs from frontmatter"))
    project_root = frontmatter.get("project_root")
    if not isinstance(project_root, str) or not Path(project_root).is_absolute():
        errors.append(issue("ERROR", checkpoint_path, "project_root must be an absolute path"))
    elif Path(project_root).expanduser().resolve() != project:
        errors.append(issue("ERROR", checkpoint_path, "project_root does not match the validated project"))
    if frontmatter.get("reason") not in REASONS:
        errors.append(issue("ERROR", checkpoint_path, f"invalid reason: {frontmatter.get('reason')!r}"))
    if frontmatter.get("coverage") not in COVERAGE_VALUES:
        errors.append(issue("ERROR", checkpoint_path, f"invalid coverage: {frontmatter.get('coverage')!r}"))

    sections, duplicates = section_map(body)
    validate_sections(checkpoint_path, sections, duplicates, SESSION_SECTIONS, errors, require_content=True)
    validate_evidence(checkpoint_path, "Verification", sections.get("Verification", []), errors)

    session_conversations = frontmatter.get("conversations")
    if not isinstance(session_conversations, list) or not session_conversations:
        errors.append(issue("ERROR", checkpoint_path, "conversations must contain at least one conversation"))
    else:
        if selected is not None:
            recorded = set(session_conversations)
            if exact_scope and recorded != selected:
                errors.append(issue("ERROR", checkpoint_path, "conversations must match the selected conversation scope"))
            elif not exact_scope and not selected.issubset(recorded):
                errors.append(issue("ERROR", checkpoint_path, "selected conversation is absent from the checkpoint"))
        if len(set(session_conversations)) != len(session_conversations):
            errors.append(issue("ERROR", checkpoint_path, "conversations contains a duplicate conversation"))
        for conversation_name in session_conversations:
            if not CONVERSATION_NAME_RE.match(conversation_name):
                errors.append(issue("ERROR", checkpoint_path, f"invalid conversation name: {conversation_name!r}"))
        conversations_to_check = selected if selected is not None else set(session_conversations)
        for conversation_name in conversations_to_check:
            conversation = conversations.get(conversation_name)
            if not conversation:
                errors.append(issue("ERROR", checkpoint_path, f"missing managed conversation projection: {conversation_name}"))
            elif conversation.get("_latest_checkpoint_path") != checkpoint_path:
                errors.append(
                    issue("ERROR", checkpoint_path, f"{conversation_name} does not link back as latest_checkpoint")
                )
    validate_links(checkpoint_path, errors, warnings)
    scan_risky_claims(checkpoint_path, warnings)


def validate_project(
    project: Path,
    session: Optional[Path] = None,
    selected: Optional[set[str]] = None,
) -> tuple[list[str], list[str]]:
    project = project.expanduser().resolve()
    root = project / ".scratch"
    errors: list[str] = []
    warnings: list[str] = []
    if not root.is_dir():
        return [issue("ERROR", root, "conversation root does not exist")], warnings

    conversations: dict[str, dict[str, Any]] = {}
    if selected is None:
        conversation_paths = sorted(root.glob("*/CONVERSATION.md"))
    else:
        for conversation_name in selected:
            if not CONVERSATION_NAME_RE.match(conversation_name):
                errors.append(issue("ERROR", root, f"invalid selected conversation name: {conversation_name!r}"))
        conversation_paths = [
            root / name / "CONVERSATION.md"
            for name in sorted(selected)
            if CONVERSATION_NAME_RE.match(name)
        ]
    for conversation_path in conversation_paths:
        if not conversation_path.is_file():
            errors.append(issue("ERROR", conversation_path, "selected Current Conversation does not exist"))
            continue
        conversation = validate_conversation(conversation_path, errors, warnings)
        if conversation is not None:
            conversations[str(conversation.get("conversation", conversation_path.parent.name))] = conversation
        elif selected is not None:
            errors.append(issue("ERROR", conversation_path, "selected Current Conversation is not managed"))

    router = root / "CONVERSATIONS.md"
    router_version: Optional[int] = None
    if not router.is_file():
        errors.append(issue("ERROR", router, "managed router does not exist"))
    else:
        router_version = validate_router(router, conversations, errors, warnings, selected)
    if session is not None:
        if router_version != SCHEMA_VERSION:
            errors.append(issue("ERROR", router, "publishing a checkpoint requires a valid Conversation Index"))
        validate_checkpoint(project, session, conversations, errors, warnings, selected)
    else:
        checkpoints: dict[Path, set[str]] = {}
        for conversation_name, conversation in conversations.items():
            checkpoint_path = conversation.get("_latest_checkpoint_path")
            if isinstance(checkpoint_path, Path):
                checkpoints.setdefault(checkpoint_path, set()).add(conversation_name)
        for checkpoint_path, checkpoint_conversations in checkpoints.items():
            validate_checkpoint(
                project,
                checkpoint_path,
                conversations,
                errors,
                warnings,
                checkpoint_conversations,
                exact_scope=False,
            )
    return errors, warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="Project root that contains .scratch")
    parser.add_argument("--session", type=Path, help="New checkpoint path, absolute or relative to the project root")
    parser.add_argument(
        "--conversation",
        action="append",
        dest="conversations",
        help="Validate only this Current Conversation; repeat for a multi-conversation checkpoint",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected = set(args.conversations) if args.conversations else None
    errors, warnings = validate_project(args.project, args.session, selected)
    for message in warnings:
        print(message, file=sys.stderr)
    for message in errors:
        print(message, file=sys.stderr)
    if errors:
        print(f"conversation validation failed: {len(errors)} error(s), {len(warnings)} warning(s)", file=sys.stderr)
        return 1
    print(f"conversation validation passed: {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
