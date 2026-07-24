#!/usr/bin/env python3

"""Update and diagnose the shared AI-agent installation.

Skill manifests define the installed and retired names owned by each source repository. Updates verify every current skill before retiring an old one. The doctor performs the same checks without changing the installation.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


CONFIGURATION_DIRECTORY = Path(__file__).resolve().parent
REPOSITORY_ROOT = CONFIGURATION_DIRECTORY.parent
DEFAULT_SKILL_MANIFEST = CONFIGURATION_DIRECTORY / "skills.json"
SKILL_LOCK_VERSION = 3
SAVE_PROTOCOL = "publisher-v1"
IGNORED_SKILL_PATH_PARTS = {"__pycache__", ".DS_Store"}
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SOURCE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
EXPERTISE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
RESERVED_EXPERTISE_NAMES = {"index.md", "index.md.sources", "index.md.sha256"}


class InstallationError(ValueError):
    """An invalid source or unsafe installed state."""


@dataclass(frozen=True)
class Skill:
    name: str
    relative_path: PurePosixPath


@dataclass(frozen=True)
class RetiredSkill:
    name: str
    relative_path: PurePosixPath
    replacement: str
    owned_sources: frozenset[str]


@dataclass(frozen=True)
class SkillManifest:
    path: Path
    repository_root: Path
    source: str
    ref: str
    skills: tuple[Skill, ...]
    retired: tuple[RetiredSkill, ...]


@dataclass(frozen=True)
class Configuration:
    home: Path
    codex_home: Path
    conversation_continuity: bool
    expertise_sources: tuple[tuple[str, Path], ...]
    instruction_sources: tuple[Path, ...]
    manifests: tuple[SkillManifest, ...]


class Report:
    def __init__(self) -> None:
        self.errors: list[tuple[str, str]] = []
        self.notes: list[str] = []

    def error(self, message: str, remediation: str) -> None:
        self.errors.append((message, remediation))

    def note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)

    def print(self) -> None:
        for message, remediation in self.errors:
            print(f"ERROR: {message}")
            print(f"  Fix: {remediation}")
        for message in self.notes:
            print(f"NOTE: {message}")
        if not self.errors:
            print("OK: The installed skills and configuration match their sources.")


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def lexists(path: Path) -> bool:
    return os.path.lexists(path)


def regular_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def load_json_object(path: Path, description: str) -> dict[str, Any]:
    if not regular_file(path):
        raise InstallationError(f"{description} must be a regular file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstallationError(
            f"{description} is not valid JSON: {path}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise InstallationError(f"{description} must contain a JSON object: {path}")
    return value


def require_string(value: Any, field: str, path: Path) -> str:
    if not isinstance(value, str) or not value:
        raise InstallationError(f"{field} must be a nonempty string in {path}")
    return value


def parse_skill_name(value: Any, field: str, path: Path) -> str:
    name = require_string(value, field, path)
    if not SKILL_NAME_PATTERN.fullmatch(name):
        raise InstallationError(
            f"{field} is not a lowercase skill name in {path}: {name}"
        )
    return name


def parse_skill_path(value: Any, field: str, path: Path) -> PurePosixPath:
    raw_path = require_string(value, field, path)
    relative_path = PurePosixPath(raw_path)
    if (
        relative_path.is_absolute()
        or not relative_path.parts
        or any(part in {"", ".", ".."} for part in relative_path.parts)
    ):
        raise InstallationError(
            f"{field} must be a safe relative path in {path}: {raw_path}"
        )
    return relative_path


def frontmatter_skill_name(path: Path) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if not lines or lines[0] != "---":
        return None
    for line in lines[1:]:
        if line == "---":
            return None
        match = re.fullmatch(r"name:\s*([a-z0-9][a-z0-9-]*)\s*", line)
        if match:
            return match.group(1)
    return None


def load_skill_manifest(path: Path) -> SkillManifest:
    resolved_path = path.expanduser().resolve()
    value = load_json_object(resolved_path, "skill manifest")
    if value.get("schema_version") != 1:
        raise InstallationError(
            f"skill manifest has an unsupported schema_version: {resolved_path}"
        )
    source = require_string(value.get("source"), "source", resolved_path)
    if not SOURCE_PATTERN.fullmatch(source):
        raise InstallationError(
            f"source must use owner/repository form in {resolved_path}: {source}"
        )
    ref = require_string(value.get("ref"), "ref", resolved_path)
    if (
        not REF_PATTERN.fullmatch(ref)
        or ".." in PurePosixPath(ref).parts
        or ref.endswith(("/", ".", ".lock"))
        or "@{" in ref
    ):
        raise InstallationError(
            f"ref is not a safe Git branch or tag in {resolved_path}: {ref}"
        )

    repository_root = resolved_path.parent.parent
    raw_skills = value.get("skills")
    raw_retired = value.get("retired")
    if not isinstance(raw_skills, list):
        raise InstallationError(f"skills must be an array in {resolved_path}")
    if not isinstance(raw_retired, list):
        raise InstallationError(f"retired must be an array in {resolved_path}")

    skills: list[Skill] = []
    seen_skills: set[str] = set()
    for index, raw_skill in enumerate(raw_skills):
        if not isinstance(raw_skill, dict):
            raise InstallationError(
                f"skills[{index}] must be an object in {resolved_path}"
            )
        name = parse_skill_name(
            raw_skill.get("name"), f"skills[{index}].name", resolved_path
        )
        relative_path = parse_skill_path(
            raw_skill.get("path"), f"skills[{index}].path", resolved_path
        )
        if name in seen_skills:
            raise InstallationError(
                f"duplicate managed skill in {resolved_path}: {name}"
            )
        seen_skills.add(name)
        skill_file = repository_root.joinpath(*relative_path.parts, "SKILL.md")
        if not regular_file(skill_file):
            raise InstallationError(f"managed skill is missing SKILL.md: {skill_file}")
        declared_name = frontmatter_skill_name(skill_file)
        if declared_name != name:
            raise InstallationError(
                f"managed skill name does not match its manifest: expected {name}, found {declared_name!r} at {skill_file}"
            )
        skills.append(Skill(name=name, relative_path=relative_path))

    retired: list[RetiredSkill] = []
    seen_retired: set[str] = set()
    for index, raw_skill in enumerate(raw_retired):
        if not isinstance(raw_skill, dict):
            raise InstallationError(
                f"retired[{index}] must be an object in {resolved_path}"
            )
        name = parse_skill_name(
            raw_skill.get("name"), f"retired[{index}].name", resolved_path
        )
        relative_path = parse_skill_path(
            raw_skill.get("path"),
            f"retired[{index}].path",
            resolved_path,
        )
        replacement = parse_skill_name(
            raw_skill.get("replacement"),
            f"retired[{index}].replacement",
            resolved_path,
        )
        raw_sources = raw_skill.get("owned_sources")
        if not isinstance(raw_sources, list) or not raw_sources:
            raise InstallationError(
                f"retired[{index}].owned_sources must be a nonempty array in {resolved_path}"
            )
        owned_sources: set[str] = set()
        for source_index, raw_source in enumerate(raw_sources):
            owned_source = require_string(
                raw_source,
                f"retired[{index}].owned_sources[{source_index}]",
                resolved_path,
            )
            if not SOURCE_PATTERN.fullmatch(owned_source):
                raise InstallationError(
                    f"retired[{index}].owned_sources[{source_index}] must use owner/repository form in "
                    f"{resolved_path}: {owned_source}"
                )
            owned_sources.add(owned_source)
        if name in seen_retired or name in seen_skills:
            raise InstallationError(
                f"duplicate or active retired skill in {resolved_path}: {name}"
            )
        seen_retired.add(name)
        retired.append(
            RetiredSkill(
                name=name,
                relative_path=relative_path,
                replacement=replacement,
                owned_sources=frozenset(owned_sources),
            )
        )

    return SkillManifest(
        path=resolved_path,
        repository_root=repository_root,
        source=source,
        ref=ref,
        skills=tuple(skills),
        retired=tuple(retired),
    )


def load_manifests(paths: Sequence[Path]) -> tuple[SkillManifest, ...]:
    manifests = tuple(load_skill_manifest(path) for path in paths)
    seen_active: dict[str, Path] = {}
    seen_retired: dict[str, Path] = {}
    for manifest in manifests:
        for skill in manifest.skills:
            if skill.name in seen_active:
                raise InstallationError(
                    f"managed skill appears in more than one manifest: {skill.name}: "
                    f"{seen_active[skill.name]} and {manifest.path}"
                )
            if skill.name in seen_retired:
                raise InstallationError(
                    f"skill is both managed and retired: {skill.name}: "
                    f"{seen_retired[skill.name]} and {manifest.path}"
                )
            seen_active[skill.name] = manifest.path
        for skill in manifest.retired:
            if skill.name in seen_retired:
                raise InstallationError(
                    f"retired skill appears in more than one manifest: {skill.name}: "
                    f"{seen_retired[skill.name]} and {manifest.path}"
                )
            if skill.name in seen_active:
                raise InstallationError(
                    f"skill is both managed and retired: {skill.name}: "
                    f"{seen_active[skill.name]} and {manifest.path}"
                )
            seen_retired[skill.name] = manifest.path
    for manifest in manifests:
        for skill in manifest.retired:
            if skill.replacement not in seen_active:
                raise InstallationError(
                    f"retired skill replacement is not managed by any manifest: "
                    f"{skill.name} -> {skill.replacement}: {manifest.path}"
                )
    return manifests


def resolve_nonempty_file(path: Path, description: str) -> Path:
    resolved = path.expanduser().resolve()
    if not regular_file(resolved) or resolved.stat().st_size == 0:
        raise InstallationError(f"{description} is missing or empty: {resolved}")
    return resolved


def resolve_expertise_source(name: str, directory: Path) -> tuple[str, Path]:
    if not EXPERTISE_NAME_PATTERN.fullmatch(name) or name in RESERVED_EXPERTISE_NAMES:
        raise InstallationError(f"invalid or reserved expertise source name: {name}")
    resolved = directory.expanduser().resolve()
    index = resolved / "INDEX.md"
    if not resolved.is_dir() or not regular_file(index) or index.stat().st_size == 0:
        raise InstallationError(
            f"expertise source must contain a nonempty INDEX.md: {resolved}"
        )
    return name, resolved


def build_configuration(args: argparse.Namespace) -> Configuration:
    home_value = os.environ.get("HOME")
    if not home_value:
        raise InstallationError("HOME must be set.")
    home = Path(home_value).expanduser().resolve()
    codex_value = os.environ.get("CODEX_HOME")
    codex_home = (
        Path(codex_value).expanduser().resolve() if codex_value else home / ".codex"
    )

    public_instructions = resolve_nonempty_file(
        CONFIGURATION_DIRECTORY / "agent-instructions.md",
        "public instruction source",
    )
    instruction_sources = [public_instructions]
    instruction_sources.extend(
        resolve_nonempty_file(Path(path), "instruction source")
        for path in args.instruction_source
    )
    instruction_sources.extend(
        resolve_nonempty_file(Path(path), "instruction source")
        for path in args.additional_instructions
    )

    expertise_sources = [
        resolve_expertise_source("ai", REPOSITORY_ROOT / "expertise"),
    ]
    seen_expertise = {"ai"}
    for name, directory in args.expertise_source:
        if name in seen_expertise:
            raise InstallationError(f"duplicate expertise source name: {name}")
        expertise_sources.append(resolve_expertise_source(name, Path(directory)))
        seen_expertise.add(name)

    manifest_paths = [DEFAULT_SKILL_MANIFEST]
    manifest_paths.extend(Path(path) for path in args.skill_manifest)
    manifests = load_manifests(manifest_paths)

    return Configuration(
        home=home,
        codex_home=codex_home,
        conversation_continuity=args.conversation_continuity,
        expertise_sources=tuple(expertise_sources),
        instruction_sources=tuple(instruction_sources),
        manifests=manifests,
    )


def read_json_for_doctor(
    path: Path, description: str, report: Report
) -> dict[str, Any] | None:
    try:
        return load_json_object(path, description)
    except InstallationError as error:
        report.error(
            str(error),
            "Run configuration/update after resolving any unmanaged file at that path.",
        )
        return None


def expected_instruction_files(
    configuration: Configuration,
) -> tuple[bytes, bytes, bytes]:
    content = b"\n".join(
        path.read_bytes() for path in configuration.instruction_sources
    )
    sources = "".join(
        f"{path}\n" for path in configuration.instruction_sources
    ).encode()
    digest = f"{sha256(content)}\n".encode()
    return content, sources, digest


def expected_expertise_files(
    configuration: Configuration,
) -> tuple[bytes, bytes, bytes, str]:
    lines = [
        "# Expertise Sources",
        "",
        "Before starting work, read each source index listed below. Load only the files whose “Read when” "
        "condition matches the task.",
        "",
        "| Source | Index |",
        "|--------|-------|",
    ]
    for name, _ in configuration.expertise_sources:
        lines.append(f"| {name} | [{name}/INDEX.md]({name}/INDEX.md) |")
    index = ("\n".join(lines) + "\n").encode()
    sources = "".join(
        f"{name}\t{directory}\n" for name, directory in configuration.expertise_sources
    ).encode()
    digest = sha256(index + b"\n-- expertise sources --\n" + sources)
    return index, sources, f"{digest}\n".encode(), digest


def check_regular_bytes(
    path: Path,
    expected: bytes,
    description: str,
    remediation: str,
    report: Report,
) -> None:
    if not regular_file(path):
        report.error(
            f"{description} is missing or is not a regular file: {path}", remediation
        )
        return
    try:
        actual = path.read_bytes()
    except OSError as error:
        report.error(f"{description} cannot be read: {path}: {error}", remediation)
        return
    if actual != expected:
        report.error(f"{description} does not match its source: {path}", remediation)


def resolved_link_target(path: Path) -> Path | None:
    if not path.is_symlink():
        return None
    target = Path(os.readlink(path))
    if not target.is_absolute():
        target = path.parent / target
    return target.resolve(strict=False)


def check_instructions(configuration: Configuration, report: Report) -> None:
    agents_root = configuration.home / ".agents"
    installed = agents_root / "AGENTS.md"
    sources = agents_root / "AGENTS.md.sources"
    digest = agents_root / "AGENTS.md.sha256"
    expected_content, expected_sources, expected_digest = expected_instruction_files(
        configuration
    )
    remediation = "Run configuration/update with the same instruction sources."
    check_regular_bytes(
        installed, expected_content, "installed agent instructions", remediation, report
    )
    check_regular_bytes(
        sources, expected_sources, "instruction source manifest", remediation, report
    )
    check_regular_bytes(
        digest, expected_digest, "instruction digest", remediation, report
    )

    entrypoints = (
        (configuration.home / ".claude" / "CLAUDE.md", "Claude instruction entrypoint"),
        (configuration.codex_home / "AGENTS.md", "Codex instruction entrypoint"),
    )
    for entrypoint, description in entrypoints:
        target = resolved_link_target(entrypoint)
        if target != installed.resolve(strict=False):
            report.error(
                f"{description} does not point to the managed instructions: {entrypoint}",
                remediation,
            )


def check_expertise(configuration: Configuration, report: Report) -> None:
    agents_root = configuration.home / ".agents"
    installed = agents_root / "expertise"
    generations = agents_root / ".expertise-generations"
    expected_index, expected_sources, expected_digest, digest = (
        expected_expertise_files(configuration)
    )
    generation = generations / digest
    remediation = "Run configuration/update with the same expertise sources."

    target = resolved_link_target(installed)
    if target != generation.resolve(strict=False):
        report.error(
            f"installed expertise does not point to the expected generation: {installed}",
            remediation,
        )
    check_regular_bytes(
        generation / "INDEX.md", expected_index, "expertise index", remediation, report
    )
    check_regular_bytes(
        generation / "INDEX.md.sources",
        expected_sources,
        "expertise source manifest",
        remediation,
        report,
    )
    check_regular_bytes(
        generation / "INDEX.md.sha256",
        expected_digest,
        "expertise digest",
        remediation,
        report,
    )
    for name, directory in configuration.expertise_sources:
        link = generation / name
        if resolved_link_target(link) != directory.resolve(strict=False):
            report.error(
                f"expertise source link is wrong or missing: {link}", remediation
            )


def ignored_skill_path(relative_path: Path) -> bool:
    return any(
        part in IGNORED_SKILL_PATH_PARTS or part.endswith(".pyc")
        for part in relative_path.parts
    )


def skill_tree(root: Path) -> dict[str, tuple[str, str, int]]:
    if root.is_symlink() or not root.is_dir():
        raise InstallationError(
            f"skill directory is missing or is not a regular directory: {root}"
        )
    result: dict[str, tuple[str, str, int]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ignored_skill_path(relative):
            continue
        key = relative.as_posix()
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            result[key] = ("directory", "", 0)
        elif stat.S_ISREG(metadata.st_mode):
            result[key] = ("file", sha256(path.read_bytes()), metadata.st_mode & 0o111)
        elif stat.S_ISLNK(metadata.st_mode):
            result[key] = ("symlink", os.readlink(path), 0)
        else:
            result[key] = ("other", "", stat.S_IFMT(metadata.st_mode))
    return result


def first_tree_difference(
    expected: dict[str, tuple[str, str, int]],
    actual: dict[str, tuple[str, str, int]],
) -> str:
    for key in sorted(set(expected) | set(actual)):
        if key not in actual:
            return f"missing {key}"
        if key not in expected:
            return f"unexpected {key}"
        if expected[key] != actual[key]:
            return f"changed {key}"
    return "unknown difference"


def skill_lock_path(home: Path) -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home).expanduser().resolve() / "skills" / ".skill-lock.json"
    return home / ".agents" / ".skill-lock.json"


def load_skill_lock(home: Path, report: Report) -> dict[str, Any]:
    lock_path = skill_lock_path(home)
    value = read_json_for_doctor(lock_path, "skills lock", report)
    if value is None:
        return {}
    skills = value.get("skills")
    if value.get("version") != SKILL_LOCK_VERSION or not isinstance(skills, dict):
        report.error(
            f"skills lock does not use schema version {SKILL_LOCK_VERSION}: {lock_path}",
            "Reinstall the managed skills with configuration/update.",
        )
        return {}
    return skills


def check_client_skill_path(
    path: Path,
    canonical: Path,
    client: str,
    remediation: str,
    report: Report,
) -> None:
    if not lexists(path):
        if client == "Claude":
            report.error(
                f"{client} skill entrypoint is missing: {path}",
                remediation,
            )
        return
    if path.is_symlink():
        if resolved_link_target(path) != canonical.resolve(strict=False):
            report.error(
                f"{client} skill entrypoint points somewhere unexpected: {path}",
                remediation,
            )
        return
    try:
        if skill_tree(path) != skill_tree(canonical):
            report.error(
                f"{client} has a divergent copy of the managed skill: {path}",
                remediation,
            )
    except (InstallationError, OSError) as error:
        report.error(
            f"{client} skill entrypoint is invalid: {path}: {error}",
            remediation,
        )


def check_managed_skills(
    configuration: Configuration, lock: dict[str, Any], report: Report
) -> None:
    for manifest in configuration.manifests:
        for skill in manifest.skills:
            source = manifest.repository_root.joinpath(*skill.relative_path.parts)
            installed = configuration.home / ".agents" / "skills" / skill.name
            claude_path = configuration.home / ".claude" / "skills" / skill.name
            codex_path = configuration.codex_home / "skills" / skill.name
            entry = lock.get(skill.name)
            expected_path = f"{skill.relative_path.as_posix()}/SKILL.md"
            owned_entry = (
                isinstance(entry, dict)
                and entry.get("source") == manifest.source
                and entry.get("sourceType") == "github"
                and entry.get("skillPath") == expected_path
            )
            fresh_install = entry is None and not any(
                lexists(path) for path in (installed, claude_path, codex_path)
            )
            if owned_entry or fresh_install:
                remediation = "Run configuration/update to reinstall managed skills."
            else:
                remediation = (
                    "Remove, rename, or adopt the unmanaged skill paths deliberately before running "
                    "configuration/update."
                )
            try:
                expected_tree = skill_tree(source)
            except (InstallationError, OSError) as error:
                report.error(
                    str(error),
                    "Restore the source repository before updating the installation.",
                )
                continue
            try:
                installed_tree = skill_tree(installed)
            except (InstallationError, OSError) as error:
                report.error(str(error), remediation)
            else:
                if installed_tree != expected_tree:
                    difference = first_tree_difference(expected_tree, installed_tree)
                    report.error(
                        f"installed skill differs from its source ({difference}): {skill.name}",
                        remediation,
                    )

            if not isinstance(entry, dict):
                report.error(
                    f"managed skill is absent from the skills lock: {skill.name}",
                    remediation,
                )
            else:
                source_value = entry.get("source")
                if (
                    source_value != manifest.source
                    or entry.get("sourceType") != "github"
                ):
                    report.error(
                        f"managed skill has unexpected lock ownership: {skill.name}: {source_value!r}",
                        remediation,
                    )
                if entry.get("ref") != manifest.ref:
                    report.error(
                        f"managed skill has an unexpected lock ref: {skill.name}: {entry.get('ref')!r}",
                        remediation,
                    )
                if entry.get("skillPath") != expected_path:
                    report.error(
                        f"managed skill has an unexpected lock path: {skill.name}: {entry.get('skillPath')!r}",
                        remediation,
                    )

            check_client_skill_path(
                claude_path,
                installed,
                "Claude",
                remediation,
                report,
            )
            check_client_skill_path(
                codex_path,
                installed,
                "Codex",
                remediation,
                report,
            )


def installed_plugin_paths(home: Path, report: Report) -> tuple[Path, ...]:
    registry = home / ".claude" / "plugins" / "installed_plugins.json"
    if not lexists(registry):
        return ()
    value = read_json_for_doctor(registry, "Claude installed-plugin registry", report)
    if value is None:
        return ()
    found: set[Path] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            install_path = node.get("installPath")
            if isinstance(install_path, str) and install_path:
                found.add(Path(install_path).expanduser().resolve())
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return tuple(sorted(found))


def plugin_declares_skill(plugin_path: Path, name: str) -> Path | None:
    if not plugin_path.is_dir():
        return None
    skills_root = plugin_path / "skills"
    if not skills_root.is_dir():
        return None
    direct = skills_root / name / "SKILL.md"
    if regular_file(direct):
        return direct
    try:
        candidates = skills_root.rglob("SKILL.md")
        for candidate in candidates:
            if frontmatter_skill_name(candidate) == name:
                return candidate
    except OSError:
        return None
    return None


def retired_skill_paths(configuration: Configuration, name: str) -> tuple[Path, ...]:
    return (
        configuration.home / ".agents" / "skills" / name,
        configuration.home / ".claude" / "skills" / name,
        configuration.codex_home / "skills" / name,
    )


def retired_entry_is_owned(skill: RetiredSkill, entry: Any) -> bool:
    expected_path = f"{skill.relative_path.as_posix()}/SKILL.md"
    return (
        isinstance(entry, dict)
        and entry.get("source") in skill.owned_sources
        and entry.get("sourceType") == "github"
        and entry.get("skillPath") == expected_path
    )


def check_retired_skills(
    configuration: Configuration, lock: dict[str, Any], report: Report
) -> None:
    retired = {
        skill.name: skill
        for manifest in configuration.manifests
        for skill in manifest.retired
    }
    if not retired:
        return
    plugin_paths = installed_plugin_paths(configuration.home, report)
    for name, skill in retired.items():
        entry = lock.get(name)
        if entry is not None:
            source = entry.get("source") if isinstance(entry, dict) else None
            if retired_entry_is_owned(skill, entry):
                remediation = "Run configuration/update to remove the retired skill."
            else:
                remediation = "Remove or rename the skill deliberately; its lock provenance is not owned by this installation."
            report.error(
                f"retired skill remains in the skills lock: {name}: {source!r}",
                remediation,
            )
        for path in retired_skill_paths(configuration, name):
            if lexists(path):
                report.error(
                    f"retired skill remains discoverable: {path}",
                    "Run configuration/update if the skills lock has recognized ownership; otherwise remove it deliberately.",
                )
        for plugin_path in plugin_paths:
            declaration = plugin_declares_skill(plugin_path, name)
            if declaration is not None:
                report.error(
                    f"an installed Claude plugin still declares retired skill {name}: {declaration}",
                    "Update or remove the plugin that owns the retired skill.",
                )

    usage_path = configuration.home / ".claude.json"
    if regular_file(usage_path):
        try:
            usage = json.loads(usage_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            usage = None
        skill_usage = usage.get("skillUsage") if isinstance(usage, dict) else None
        remembered = sorted(
            name
            for name in retired
            if isinstance(skill_usage, dict) and name in skill_usage
        )
        if remembered:
            report.note(
                "Claude usage history still mentions retired skill(s) "
                f"{', '.join(remembered)}. This history is not an installed skill."
            )
    report.note(
        "A running Claude session can retain a skill it invoked before retirement. Save the conversation, "
        "start a new session, and resume it; do not rewrite the old transcript."
    )


def read_literal_assignment(path: Path, name: str) -> str:
    if not regular_file(path):
        raise InstallationError(
            f"protocol source is missing or is not a regular file: {path}"
        )
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError) as error:
        raise InstallationError(
            f"protocol source is not valid Python: {path}"
        ) from error
    values: list[Any] = []
    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if isinstance(target, ast.Name) and target.id == name:
            values.append(
                statement.value.value
                if isinstance(statement.value, ast.Constant)
                else None
            )
    if len(values) != 1 or not isinstance(values[0], str):
        raise InstallationError(f"{path} does not declare one literal {name}")
    return values[0]


def hook_handlers(
    settings: dict[str, Any],
    event: str,
    report: Report,
    description: str,
) -> list[tuple[dict[str, Any], Any, bool]]:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        report.error(
            f"{description} hooks are missing or invalid",
            "Run configuration/update --conversation-continuity.",
        )
        return []
    groups = hooks.get(event)
    if not isinstance(groups, list):
        report.error(
            f"{description} {event} hooks are missing or invalid",
            "Run configuration/update --conversation-continuity.",
        )
        return []
    result: list[tuple[dict[str, Any], Any, bool]] = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            report.error(
                f"{description} {event} contains an invalid hook group",
                "Repair the JSON structure, then run configuration/update --conversation-continuity.",
            )
            continue
        matcher = group.get("matcher")
        has_matcher = "matcher" in group
        for handler in group["hooks"]:
            if not isinstance(handler, dict) or not isinstance(
                handler.get("command"), str
            ):
                report.error(
                    f"{description} {event} contains an invalid hook handler",
                    "Repair the JSON structure, then run configuration/update --conversation-continuity.",
                )
                continue
            result.append((handler, matcher, has_matcher))
    return result


def check_client_hooks(
    settings: dict[str, Any],
    event: str,
    expected_command: str,
    expected_matcher: str | None,
    description: str,
    report: Report,
) -> None:
    handlers = hook_handlers(settings, event, report, description)
    matching = [
        (handler, matcher, has_matcher)
        for handler, matcher, has_matcher in handlers
        if handler.get("command") == expected_command
    ]
    expected_handler = {
        "type": "command",
        "command": expected_command,
        "timeout": 5,
    }
    expected = [
        (
            expected_handler,
            expected_matcher,
            expected_matcher is not None,
        )
    ]
    if matching != expected:
        report.error(
            f"{description} must contain exactly one managed {event} hook with the expected matcher",
            "Run configuration/update --conversation-continuity.",
        )


def check_no_misplaced_managed_hooks(
    settings: dict[str, Any],
    expected_command: str,
    other_managed_commands: set[str],
    description: str,
    report: Report,
) -> None:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return
    expected_events = {"SessionStart", "Stop"}
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            for handler in group["hooks"]:
                if not isinstance(handler, dict):
                    continue
                command = handler.get("command")
                if command == expected_command and event not in expected_events:
                    report.error(
                        f"{description} has a managed conversation hook under the wrong event: {event}",
                        "Run configuration/update --conversation-continuity.",
                    )
                if command in other_managed_commands:
                    report.error(
                        f"{description} contains a conversation hook for the wrong client: {command}",
                        "Run configuration/update --conversation-continuity.",
                    )


def find_manifest_skill(
    configuration: Configuration, name: str
) -> tuple[SkillManifest, Skill] | None:
    for manifest in configuration.manifests:
        for skill in manifest.skills:
            if skill.name == name:
                return manifest, skill
    return None


def continuity_command(destination: Path, operation: str) -> str:
    return f"{shlex.quote(str(destination))} {operation}"


def check_conversation_continuity(configuration: Configuration, report: Report) -> None:
    if not configuration.conversation_continuity:
        return
    source = CONFIGURATION_DIRECTORY / "conversation_continuity.py"
    destination = configuration.home / ".agents" / "bin" / "conversation-continuity"
    digest_path = destination.with_suffix(".sha256")
    remediation = "Run configuration/update --conversation-continuity."
    source_bytes = source.read_bytes()
    check_regular_bytes(
        destination,
        source_bytes,
        "conversation continuity controller",
        remediation,
        report,
    )
    check_regular_bytes(
        digest_path,
        f"{sha256(source_bytes)}\n".encode(),
        "conversation continuity controller digest",
        remediation,
        report,
    )
    if regular_file(destination) and not destination.stat().st_mode & 0o111:
        report.error(
            f"conversation continuity controller is not executable: {destination}",
            remediation,
        )

    save_skill = find_manifest_skill(configuration, "save-conversation")
    if save_skill is None:
        report.error("no manifest manages save-conversation", remediation)
    else:
        manifest, skill = save_skill
        publisher = (
            configuration.home
            / ".agents"
            / "skills"
            / skill.name
            / "scripts"
            / "publish_conversation.py"
        )
        declarations = (
            (source, "SAVE_PROTOCOL", "continuity controller"),
            (publisher, "PUBLISHER_PROTOCOL", "installed conversation publisher"),
        )
        for path, assignment, description in declarations:
            try:
                protocol = read_literal_assignment(path, assignment)
            except InstallationError as error:
                report.error(str(error), remediation)
                continue
            if protocol != SAVE_PROTOCOL:
                report.error(
                    f"{description} uses {protocol}, expected {SAVE_PROTOCOL}: {path}",
                    remediation,
                )
        marker = f"Publication protocol: `{SAVE_PROTOCOL}`."
        skill_file = manifest.repository_root.joinpath(
            *skill.relative_path.parts, "SKILL.md"
        )
        if marker not in skill_file.read_text(encoding="utf-8").splitlines():
            report.error(
                f"save-conversation does not declare {SAVE_PROTOCOL}: {skill_file}",
                remediation,
            )

    claude_path = configuration.home / ".claude" / "settings.json"
    codex_path = configuration.codex_home / "hooks.json"
    claude = read_json_for_doctor(claude_path, "Claude settings", report)
    codex = read_json_for_doctor(codex_path, "Codex hooks", report)
    if claude is None or codex is None:
        return
    claude_command = continuity_command(destination, "hook --client claude")
    codex_command = continuity_command(destination, "hook --client codex")
    for settings, command, description in (
        (claude, claude_command, "Claude"),
        (codex, codex_command, "Codex"),
    ):
        check_client_hooks(
            settings,
            "SessionStart",
            command,
            "startup|clear|compact",
            description,
            report,
        )
        check_client_hooks(settings, "Stop", command, None, description, report)
        other_commands = {claude_command, codex_command} - {command}
        other_commands.add(continuity_command(destination, "statusline"))
        check_no_misplaced_managed_hooks(
            settings, command, other_commands, description, report
        )

    expected_status_line = {
        "type": "command",
        "command": continuity_command(destination, "statusline"),
    }
    status_line = claude.get("statusLine")
    if status_line is None:
        report.error(
            "Claude does not have the managed conversation status line",
            "Run configuration/update --conversation-continuity.",
        )
    elif status_line != expected_status_line:
        report.note(
            "Claude has a custom status line. Context-percentage conversation checks are disabled, while turn, "
            "time, compaction, and stop checks remain active."
        )


def run_doctor(configuration: Configuration) -> int:
    report = Report()
    check_instructions(configuration, report)
    check_expertise(configuration, report)
    lock = load_skill_lock(configuration.home, report)
    check_managed_skills(configuration, lock, report)
    check_retired_skills(configuration, lock, report)
    check_conversation_continuity(configuration, report)
    report.print()
    return 1 if report.errors else 0


def load_lock_for_update(home: Path) -> dict[str, Any]:
    path = skill_lock_path(home)
    if not lexists(path):
        return {}
    value = load_json_object(path, "skills lock")
    skills = value.get("skills")
    if value.get("version") != SKILL_LOCK_VERSION or not isinstance(skills, dict):
        raise InstallationError(
            f"skills lock does not use schema version {SKILL_LOCK_VERSION}: {path}; repair it before updating"
        )
    return skills


def plugin_retired_collisions(
    configuration: Configuration, retired: Iterable[RetiredSkill]
) -> list[str]:
    report = Report()
    plugin_paths = installed_plugin_paths(configuration.home, report)
    if report.errors:
        raise InstallationError(report.errors[0][0])
    collisions: list[str] = []
    for skill in retired:
        for plugin_path in plugin_paths:
            declaration = plugin_declares_skill(plugin_path, skill.name)
            if declaration is not None:
                collisions.append(f"{skill.name}: {declaration}")
    return collisions


def retired_preflight(
    configuration: Configuration, lock: dict[str, Any]
) -> tuple[RetiredSkill, ...]:
    removable: list[RetiredSkill] = []
    for manifest in configuration.manifests:
        for skill in manifest.retired:
            paths = retired_skill_paths(configuration, skill.name)
            entry = lock.get(skill.name)
            source = entry.get("source") if isinstance(entry, dict) else None
            if entry is not None and not retired_entry_is_owned(skill, entry):
                raise InstallationError(
                    f"refusing to remove retired skill {skill.name}: its lock provenance is not owned: "
                    f"{source!r}, {entry.get('sourceType') if isinstance(entry, dict) else None!r}, "
                    f"{entry.get('skillPath') if isinstance(entry, dict) else None!r}"
                )
            if entry is None and any(lexists(path) for path in paths):
                found = ", ".join(str(path) for path in paths if lexists(path))
                raise InstallationError(
                    f"refusing to remove retired skill {skill.name} without owned lock provenance: {found}"
                )
            if entry is not None:
                removable.append(skill)
    all_retired = tuple(
        skill for manifest in configuration.manifests for skill in manifest.retired
    )
    collisions = plugin_retired_collisions(configuration, all_retired)
    if collisions:
        raise InstallationError(
            "refusing to remove retired skills supplied by installed Claude plugins: "
            + ", ".join(collisions)
        )
    return tuple(removable)


def skills_executable() -> str:
    executable = os.environ.get("AI_SKILLS_EXECUTABLE", "npx")
    if not executable:
        raise InstallationError("AI_SKILLS_EXECUTABLE must name one executable")
    return executable


def run_git(
    manifest: SkillManifest,
    arguments: Sequence[str],
    description: str,
    *,
    allowed_returncodes: frozenset[int] = frozenset({0}),
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(manifest.repository_root), *arguments],
            check=False,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as error:
        raise InstallationError(f"git is required to {description}") from error
    if result.returncode not in allowed_returncodes:
        detail = result.stderr.strip() or result.stdout.strip()
        raise InstallationError(
            f"could not {description} for {manifest.source}: {detail or manifest.repository_root}"
        )
    return result


def validate_skill_sources(manifest: SkillManifest) -> None:
    if not manifest.skills:
        return
    paths = [skill.relative_path.as_posix() for skill in manifest.skills]
    run_git(
        manifest,
        ["rev-parse", "--verify", f"{manifest.ref}^{{commit}}"],
        f"resolve ref {manifest.ref}",
    )
    result = run_git(
        manifest,
        ["status", "--short", "--untracked-files=all", "--", *paths],
        "check the managed skill sources",
    )
    if result.stdout.strip():
        raise InstallationError(
            f"managed skill sources have uncommitted changes in {manifest.source}; commit them before updating"
        )
    comparison = run_git(
        manifest,
        ["diff", "--quiet", manifest.ref, "--", *paths],
        f"compare managed skill sources with ref {manifest.ref}",
        allowed_returncodes=frozenset({0, 1}),
    )
    if comparison.returncode == 1:
        raise InstallationError(
            f"managed skill sources differ from local ref {manifest.ref} in {manifest.source}; "
            "land them on that ref before updating"
        )


def validate_existing_client_path(path: Path, canonical: Path, skill_name: str) -> None:
    if not lexists(path):
        return
    if path.is_symlink():
        if resolved_link_target(path) == canonical.resolve(strict=False):
            return
        raise InstallationError(
            f"refusing to overwrite divergent client entrypoint for managed skill {skill_name}: {path}"
        )
    if path.is_dir() and canonical.is_dir() and not canonical.is_symlink():
        try:
            if skill_tree(path) == skill_tree(canonical):
                return
        except OSError:
            pass
    raise InstallationError(
        f"refusing to overwrite unmanaged client entrypoint for managed skill {skill_name}: {path}"
    )


def active_skill_preflight(configuration: Configuration, lock: dict[str, Any]) -> None:
    for manifest in configuration.manifests:
        for skill in manifest.skills:
            canonical = configuration.home / ".agents" / "skills" / skill.name
            claude = configuration.home / ".claude" / "skills" / skill.name
            codex = configuration.codex_home / "skills" / skill.name
            paths = (canonical, claude, codex)
            entry = lock.get(skill.name)
            if entry is None:
                if any(lexists(path) for path in paths):
                    found = ", ".join(str(path) for path in paths if lexists(path))
                    raise InstallationError(
                        f"refusing to overwrite managed skill {skill.name} without lock provenance: {found}"
                    )
                continue
            expected_path = f"{skill.relative_path.as_posix()}/SKILL.md"
            if (
                not isinstance(entry, dict)
                or entry.get("source") != manifest.source
                or entry.get("sourceType") != "github"
                or entry.get("skillPath") != expected_path
            ):
                raise InstallationError(
                    f"refusing to overwrite managed skill {skill.name}: its lock provenance is not owned"
                )
            if lexists(canonical) and (
                canonical.is_symlink() or not canonical.is_dir()
            ):
                raise InstallationError(
                    f"refusing to overwrite invalid canonical path for managed skill {skill.name}: {canonical}"
                )
            if not canonical.is_dir() and (lexists(claude) or lexists(codex)):
                raise InstallationError(
                    f"refusing to overwrite client entrypoint without a canonical managed skill: {skill.name}"
                )
            validate_existing_client_path(claude, canonical, skill.name)
            validate_existing_client_path(codex, canonical, skill.name)


def verify_client_path(
    path: Path, canonical: Path, skill_name: str, required: bool
) -> None:
    if not lexists(path):
        if required:
            raise InstallationError(
                f"skill installation did not create a client entrypoint: {path}"
            )
        return
    if path.is_symlink():
        if resolved_link_target(path) != canonical.resolve(strict=False):
            raise InstallationError(
                f"skill installation created a wrong client entrypoint: {path}"
            )
        return
    try:
        if skill_tree(path) != skill_tree(canonical):
            raise InstallationError(
                f"skill installation created a divergent client copy: {path}"
            )
    except OSError as error:
        raise InstallationError(
            f"skill installation created an invalid client path: {path}"
        ) from error


def verify_active_installation(configuration: Configuration) -> None:
    lock = load_lock_for_update(configuration.home)
    for manifest in configuration.manifests:
        for skill in manifest.skills:
            source = manifest.repository_root.joinpath(*skill.relative_path.parts)
            canonical = configuration.home / ".agents" / "skills" / skill.name
            try:
                expected_tree = skill_tree(source)
                installed_tree = skill_tree(canonical)
            except (InstallationError, OSError) as error:
                raise InstallationError(
                    f"skill installation is incomplete for {skill.name}: {error}"
                ) from error
            if installed_tree != expected_tree:
                difference = first_tree_difference(expected_tree, installed_tree)
                raise InstallationError(
                    f"skill installation does not match the checked-out source for {skill.name}: {difference}"
                )
            entry = lock.get(skill.name)
            expected_path = f"{skill.relative_path.as_posix()}/SKILL.md"
            if (
                not isinstance(entry, dict)
                or entry.get("source") != manifest.source
                or entry.get("sourceType") != "github"
                or entry.get("ref") != manifest.ref
                or entry.get("skillPath") != expected_path
            ):
                raise InstallationError(
                    f"skill installation did not record the expected lock provenance for {skill.name}"
                )
            verify_client_path(
                configuration.home / ".claude" / "skills" / skill.name,
                canonical,
                skill.name,
                required=True,
            )
            verify_client_path(
                configuration.codex_home / "skills" / skill.name,
                canonical,
                skill.name,
                required=False,
            )


def verify_retired_skills_removed(
    configuration: Configuration, removed: Iterable[RetiredSkill]
) -> None:
    lock = load_lock_for_update(configuration.home)
    for skill in removed:
        if skill.name in lock:
            raise InstallationError(
                f"retirement did not remove {skill.name} from the skills lock"
            )
        remaining = [
            path
            for path in retired_skill_paths(configuration, skill.name)
            if lexists(path)
        ]
        if remaining:
            raise InstallationError(
                f"retirement did not remove every entrypoint for {skill.name}: "
                + ", ".join(str(path) for path in remaining)
            )


def run_command(command: Sequence[str], description: str) -> None:
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as error:
        raise InstallationError(
            f"{description} executable was not found: {command[0]}"
        ) from error
    except subprocess.CalledProcessError as error:
        raise InstallationError(
            f"{description} failed with exit status {error.returncode}"
        ) from error


def installer_arguments(args: argparse.Namespace) -> list[str]:
    result: list[str] = []
    if args.conversation_continuity:
        result.append("--conversation-continuity")
    for name, directory in args.expertise_source:
        result.extend(("--expertise-source", name, directory))
    for path in args.instruction_source:
        result.extend(("--instruction-source", path))
    if args.additional_instructions:
        result.append("--")
        result.extend(args.additional_instructions)
    return result


def run_update(configuration: Configuration, args: argparse.Namespace) -> int:
    lock = load_lock_for_update(configuration.home)
    for manifest in configuration.manifests:
        validate_skill_sources(manifest)
    active_skill_preflight(configuration, lock)
    removable = retired_preflight(configuration, lock)
    executable = skills_executable()

    for manifest in configuration.manifests:
        if not manifest.skills:
            continue
        command = [
            executable,
            "skills",
            "add",
            f"{manifest.source}#{manifest.ref}",
        ]
        for skill in manifest.skills:
            command.extend(("--skill", skill.name))
        command.extend(("--agent", "claude-code", "codex", "--global", "--yes"))
        run_command(command, f"skill installation from {manifest.source}")

    verify_active_installation(configuration)

    for skill in removable:
        run_command(
            [
                executable,
                "skills",
                "remove",
                skill.name,
                "--agent",
                "claude-code",
                "codex",
                "--global",
                "--yes",
            ],
            f"retirement of {skill.name}",
        )

    verify_retired_skills_removed(configuration, removable)

    run_command(
        [str(CONFIGURATION_DIRECTORY / "install"), *installer_arguments(args)],
        "configuration installation",
    )
    return run_doctor(configuration)


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--conversation-continuity",
        action="store_true",
        help="check or install automatic conversation save and resume hooks",
    )
    parser.add_argument(
        "--expertise-source",
        action="append",
        default=[],
        nargs=2,
        metavar=("NAME", "DIRECTORY"),
        help="add a named expertise source after the public source",
    )
    parser.add_argument(
        "--instruction-source",
        action="append",
        default=[],
        metavar="FILE",
        help="append an instruction source after the public source",
    )
    parser.add_argument(
        "--skill-manifest",
        action="append",
        default=[],
        metavar="FILE",
        help="manage another repository's skills after the public manifest",
    )
    parser.add_argument(
        "additional_instructions",
        nargs="*",
        metavar="additional-instructions",
        help="additional instruction sources, appended in order",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for operation, help_text in (
        (
            "update",
            "install skills and configuration, retire owned skills, then verify",
        ),
        ("doctor", "verify the installation without changing it"),
    ):
        subparser = subparsers.add_parser(operation, help=help_text)
        add_common_arguments(subparser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        configuration = build_configuration(args)
        if args.operation == "doctor":
            return run_doctor(configuration)
        return run_update(configuration, args)
    except (InstallationError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
