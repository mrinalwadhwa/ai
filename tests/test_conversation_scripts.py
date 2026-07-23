from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from typing import Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_NAME = "2026-07-21T120000-0700-codex.md"
SESSION_RELATIVE_PATH = Path(".scratch/_conversations/sessions") / SESSION_NAME


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collect_git_state = load_module(
    "collect_git_state",
    REPO_ROOT / "skills/save-conversation/scripts/collect_git_state.py",
)
validate_conversations = load_module(
    "validate_conversations",
    REPO_ROOT / "skills/save-conversation/scripts/validate_conversations.py",
)


SESSION = """---
managed_by: conversation-continuity
conversation_version: 1
created_at: 2026-07-21T12:00:00-07:00
agent: codex
project_root: {project}
reason: manual
coverage: full-visible-context
conversations:
  - eval
---

# Session checkpoint: eval choice

## Resume

Waiting for user: choose the next experiment.

## User direction

Choose before implementation.

## What happened

The agent measured the current behavior.

## Decisions

No implementation decision yet.

## Changes and side effects

- Conversation files: wrote the session record, `eval/CONVERSATION.md`, and `CONVERSATIONS.md` under `.scratch`.
- Non-conversation project work: no changes are represented by this synthetic fixture.

## Verification

- Claim: The measurement result was discussed but cannot be reproduced.
  - Basis: reported.
  - Source: visible conversation.
  - Reproduce: unavailable: inline labels and command were not preserved.
  - Result: a result was reported without durable inputs.
  - Limits: this does not verify the reported measurement.

## Open loops

The experiment choice remains open.

## State snapshot

Checked at 2026-07-21T12:00:00-07:00.

## Coverage gaps

The inline labels are unavailable.
"""

CONVERSATION = f"""---
managed_by: conversation-continuity
conversation_version: 1
conversation: eval
status: waiting-user
mode: standalone
updated_at: 2026-07-21T12:00:00-07:00
latest_checkpoint: ../_conversations/sessions/{SESSION_NAME}
---

# Conversation: Eval

## Resume

Waiting for user: choose the next experiment.

## Intent

Improve the evaluator.

## Current state

- [waiting-user] Experiment choice: choose the next experiment.

## Decisions

No implementation decision yet.

## Changes

- Conversation files: this projection and its linked session record were written under `.scratch`.
- Non-conversation project work: no changes are represented by this synthetic fixture.

## Evidence

- Claim: The prior measurement is not reproducible from durable artifacts.
  - Basis: reported.
  - Source: visible conversation.
  - Reproduce: unavailable: inline labels and command were not preserved.
  - Result: no durable measurement artifact exists.
  - Limits: the numerical result remains unverified.

## Open questions and risks

Which experiment should run next?

## Artifacts

No durable measurement artifact exists.

## History

- [Latest checkpoint](../_conversations/sessions/{SESSION_NAME})
"""

ROUTER = (
    """---
managed_by: conversation-continuity
conversation_version: 1
updated_at: 2026-07-21T12:00:00-07:00
---

# Conversations

| Conversation | Status | Mode | Updated | Resume |
|--------------|--------|------|---------|--------|
"""
    "| [eval](eval/CONVERSATION.md) | waiting-user | standalone | 2026-07-21T12:00:00-07:00 | "
    "Waiting for user: choose the next experiment. |\n"
)


class ConversationValidatorTests(unittest.TestCase):
    def make_project(self, directory: Path) -> Path:
        project = directory / "project"
        session = project / SESSION_RELATIVE_PATH
        conversation = project / ".scratch/eval/CONVERSATION.md"
        session.parent.mkdir(parents=True)
        conversation.parent.mkdir(parents=True)
        session.write_text(SESSION.format(project=project), encoding="utf-8")
        conversation.write_text(CONVERSATION, encoding="utf-8")
        (project / ".scratch/CONVERSATIONS.md").write_text(ROUTER, encoding="utf-8")
        return project

    def validate(
        self,
        project: Path,
        selected: Optional[set[str]] = None,
    ) -> tuple[list[str], list[str]]:
        return validate_conversations.validate_project(project, SESSION_RELATIVE_PATH, selected)

    def assert_has_error(self, errors: list[str], fragment: str) -> None:
        self.assertTrue(
            any(fragment in error for error in errors),
            f"expected an error containing {fragment!r}, got {errors!r}",
        )

    def test_valid_version_one_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            errors, warnings = self.validate(project)

        self.assertEqual([], errors)
        self.assertEqual([], warnings)

    def test_scoped_validation_ignores_an_unrelated_malformed_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            other = project / ".scratch/other/CONVERSATION.md"
            other.parent.mkdir()
            other.write_text("# malformed unrelated conversation\n", encoding="utf-8")
            router = project / ".scratch/CONVERSATIONS.md"
            router.write_text(
                router.read_text(encoding="utf-8")
                + "| [other](other/CONVERSATION.md) | closed | standalone | "
                "2026-07-20T12:00:00-07:00 | Closed: malformed fixture. |\n",
                encoding="utf-8",
            )

            full_errors, _ = self.validate(project)
            scoped_errors, scoped_warnings = self.validate(project, {"eval"})

        self.assertNotEqual([], full_errors)
        self.assertEqual([], scoped_errors)
        self.assertEqual([], scoped_warnings)

    def test_scoped_validation_ignores_an_unrelated_malformed_router_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            router = project / ".scratch/CONVERSATIONS.md"
            router.write_text(
                router.read_text(encoding="utf-8") + "| [other](other/CONVERSATION.md) | malformed |\n",
                encoding="utf-8",
            )

            full_errors, _ = self.validate(project)
            scoped_errors, scoped_warnings = self.validate(project, {"eval"})

        self.assert_has_error(full_errors, "router row must contain five columns")
        self.assertEqual([], scoped_errors)
        self.assertEqual([], scoped_warnings)

    def test_scoped_resume_deeply_validates_the_latest_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            session = project / SESSION_RELATIVE_PATH
            session.write_text(
                "\n".join(SESSION.format(project=project).splitlines()[:13]) + "\n",
                encoding="utf-8",
            )

            errors, _ = validate_conversations.validate_project(project, selected={"eval"})

        self.assert_has_error(errors, "missing section: ## Resume")

    def test_scoped_resume_accepts_a_subset_of_checkpoint_conversations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            session = project / SESSION_RELATIVE_PATH
            session.write_text(
                session.read_text(encoding="utf-8").replace(
                    "conversations:\n  - eval",
                    "conversations:\n  - eval\n  - other",
                ),
                encoding="utf-8",
            )

            errors, warnings = validate_conversations.validate_project(project, selected={"eval"})

        self.assertEqual([], errors)
        self.assertEqual([], warnings)

    def test_invalid_current_state_shape_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            conversation_path = project / ".scratch/eval/CONVERSATION.md"
            conversation_path.write_text(
                conversation_path.read_text(encoding="utf-8").replace(
                    "- [waiting-user] Experiment choice: choose the next experiment.",
                    "- **Blocked on:** the user's experiment choice.",
                ),
                encoding="utf-8",
            )

            errors, _ = self.validate(project)

        self.assert_has_error(errors, "invalid Current state item")

    def test_current_state_rejects_duplicate_bounded_subjects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            conversation_path = project / ".scratch/eval/CONVERSATION.md"
            conversation_path.write_text(
                conversation_path.read_text(encoding="utf-8").replace(
                    "- [waiting-user] Experiment choice: choose the next experiment.",
                    "- [waiting-user] Experiment choice: choose the next experiment.\n"
                    "- [done] Experiment choice: an earlier choice was completed.",
                ),
                encoding="utf-8",
            )

            errors, _ = self.validate(project)

        self.assert_has_error(errors, "Current state repeats bounded item: 'Experiment choice'")

    def test_resume_instruction_must_match_current_state_detail_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            conversation_path = project / ".scratch/eval/CONVERSATION.md"
            conversation_path.write_text(
                conversation_path.read_text(encoding="utf-8").replace(
                    "- [waiting-user] Experiment choice: choose the next experiment.",
                    "- [waiting-user] Experiment choice: choose a different experiment.",
                ),
                encoding="utf-8",
            )

            errors, _ = self.validate(project)

        self.assert_has_error(errors, "first Resume instruction must match")

    def test_long_question_can_use_bounded_subject_in_resume(self) -> None:
        exact_question = (
            "Which path should the next agent take: prototype and inspect twenty outputs, refine the detector first, "
            "or shelve the idea until a different mechanism is available?"
        )
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            conversation_path = project / ".scratch/eval/CONVERSATION.md"
            conversation_text = conversation_path.read_text(encoding="utf-8")
            conversation_text = conversation_text.replace(
                "Waiting for user: choose the next experiment.",
                "Waiting for user: Experiment choice.",
                1,
            ).replace(
                "- [waiting-user] Experiment choice: choose the next experiment.",
                f"- [waiting-user] Experiment choice: {exact_question}",
                1,
            )
            conversation_path.write_text(conversation_text, encoding="utf-8")
            router_path = project / ".scratch/CONVERSATIONS.md"
            router_path.write_text(
                router_path.read_text(encoding="utf-8").replace(
                    "Waiting for user: choose the next experiment.",
                    "Waiting for user: Experiment choice.",
                    1,
                ),
                encoding="utf-8",
            )

            errors, warnings = self.validate(project)

        self.assertGreater(len(exact_question), 120)
        self.assertEqual([], errors)
        self.assertEqual([], warnings)

    def test_router_schema_version_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            router_path = project / ".scratch/CONVERSATIONS.md"
            router_path.write_text(
                router_path.read_text(encoding="utf-8").replace(
                    "conversation_version: 1",
                    "conversation_version: 2",
                    1,
                ),
                encoding="utf-8",
            )

            errors, _ = self.validate(project)

        self.assert_has_error(errors, "conversation_version must be 1")

    def test_canonical_projection_rejects_legacy_version_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            conversation_path = project / ".scratch/eval/CONVERSATION.md"
            conversation_path.write_text(
                conversation_path.read_text(encoding="utf-8").replace(
                    "conversation_version: 1",
                    "handoff_version: 2",
                    1,
                ),
                encoding="utf-8",
            )

            errors, _ = self.validate(project)

        self.assert_has_error(errors, "conversation_version must be 1")

    def test_legacy_files_can_coexist_without_being_modified(self) -> None:
        legacy_router = b"legacy router bytes\n"
        legacy_projection = b"legacy projection bytes\n"
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            router_path = project / ".scratch/HANDOFFS.md"
            projection_path = project / ".scratch/eval/HANDOFF.md"
            router_path.write_bytes(legacy_router)
            projection_path.write_bytes(legacy_projection)

            errors, warnings = self.validate(project)

            self.assertEqual(legacy_router, router_path.read_bytes())
            self.assertEqual(legacy_projection, projection_path.read_bytes())

        self.assertEqual([], errors)
        self.assertEqual([], warnings)

    def test_marked_missing_legacy_link_warns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            router_path = project / ".scratch/CONVERSATIONS.md"
            router_path.write_text(
                router_path.read_text(encoding="utf-8")
                + "\n## Legacy records\n\n"
                + "- [Old record](legacy/missing.md) — [missing] as of 2026-07-21T12:00:00-07:00\n",
                encoding="utf-8",
            )

            errors, warnings = self.validate(project)

        self.assertEqual([], errors)
        self.assertTrue(any("legacy link is marked missing" in warning for warning in warnings))

    def test_unmarked_missing_legacy_link_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            router_path = project / ".scratch/CONVERSATIONS.md"
            router_path.write_text(
                router_path.read_text(encoding="utf-8")
                + "\n## Legacy records\n\n- [Old record](legacy/missing.md)\n",
                encoding="utf-8",
            )

            errors, _ = self.validate(project)

        self.assert_has_error(errors, "broken link: legacy/missing.md")

    def test_required_session_metadata_is_validated(self) -> None:
        fields_and_errors = {
            "agent": "invalid agent",
            "project_root": "project_root must be an absolute path",
            "reason": "invalid reason",
            "coverage": "invalid coverage",
        }

        for field, expected_error in fields_and_errors.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                project = self.make_project(Path(directory))
                session_path = project / SESSION_RELATIVE_PATH
                lines = session_path.read_text(encoding="utf-8").splitlines()
                session_path.write_text(
                    "\n".join(line for line in lines if not line.startswith(f"{field}:")) + "\n",
                    encoding="utf-8",
                )

                errors, _ = self.validate(project)

                self.assert_has_error(errors, expected_error)

    def test_conversation_latest_checkpoint_must_stay_in_session_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            conversation_path = project / ".scratch/eval/CONVERSATION.md"
            conversation_path.write_text(
                conversation_path.read_text(encoding="utf-8").replace(
                    f"latest_checkpoint: ../_conversations/sessions/{SESSION_NAME}",
                    "latest_checkpoint: ../../outside.md",
                ),
                encoding="utf-8",
            )
            (project / ".scratch/outside.md").write_text(
                SESSION.format(project=project),
                encoding="utf-8",
            )

            errors, _ = validate_conversations.validate_project(project)

        self.assert_has_error(errors, "latest_checkpoint must resolve inside .scratch/_conversations/sessions")

    def test_new_checkpoint_argument_must_stay_in_session_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            external_session = project / SESSION_NAME
            external_session.write_text(SESSION.format(project=project), encoding="utf-8")

            errors, _ = validate_conversations.validate_project(project, external_session)

        self.assert_has_error(errors, "new checkpoint must be inside .scratch/_conversations/sessions")

    def test_duplicate_required_section_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            session_path = project / SESSION_RELATIVE_PATH
            session_path.write_text(
                session_path.read_text(encoding="utf-8")
                + "\n## Resume\n\nWaiting for user: choose a conflicting experiment.\n",
                encoding="utf-8",
            )

            errors, _ = self.validate(project)

        self.assert_has_error(errors, "duplicate section: ## Resume")

    def test_empty_required_section_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            session_path = project / SESSION_RELATIVE_PATH
            session_path.write_text(
                session_path.read_text(encoding="utf-8").replace(
                    "## Coverage gaps\n\nThe inline labels are unavailable.\n",
                    "## Coverage gaps\n",
                ),
                encoding="utf-8",
            )

            errors, _ = self.validate(project)

        self.assert_has_error(errors, "empty section: ## Coverage gaps")

    def test_verified_now_evidence_requires_checked_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            session_path = project / SESSION_RELATIVE_PATH
            session_path.write_text(
                session_path.read_text(encoding="utf-8").replace(
                    "  - Basis: reported.\n",
                    "  - Basis: verified-now.\n",
                    1,
                ),
                encoding="utf-8",
            )

            errors, _ = self.validate(project)

        self.assert_has_error(errors, "verified-now claim lacks a valid Checked timestamp")

    def test_evidence_claim_requires_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            conversation_path = project / ".scratch/eval/CONVERSATION.md"
            conversation_path.write_text(
                conversation_path.read_text(encoding="utf-8").replace(
                    "  - Result: no durable measurement artifact exists.\n",
                    "",
                    1,
                ),
                encoding="utf-8",
            )

            errors, _ = self.validate(project)

        self.assert_has_error(errors, "claim lacks a Result in ## Evidence")

    def test_heading_inside_fence_does_not_satisfy_required_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            session_path = project / SESSION_RELATIVE_PATH
            text = session_path.read_text(encoding="utf-8").replace(
                "## Coverage gaps\n\nThe inline labels are unavailable.\n",
                "```markdown\n## Coverage gaps\nThe inline labels are unavailable.\n```\n",
            )
            session_path.write_text(text, encoding="utf-8")

            errors, _ = self.validate(project)

        self.assert_has_error(errors, "missing section: ## Coverage gaps")

    def test_fluent_linked_conversation_accepts_mapping_and_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            self.make_conversation_fluent_linked(project, include_mapping=True, include_section=True)

            errors, warnings = self.validate(project)

        self.assertEqual([], errors)
        self.assertEqual([], warnings)

    def test_fluent_linked_conversation_requires_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            self.make_conversation_fluent_linked(project, include_mapping=False, include_section=True)

            errors, _ = self.validate(project)

        self.assert_has_error(errors, "fluent-linked conversation requires at least one Fluent identifier")

    def test_fluent_linked_conversation_requires_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            self.make_conversation_fluent_linked(project, include_mapping=True, include_section=False)

            errors, _ = self.validate(project)

        self.assert_has_error(errors, "fluent-linked conversation requires a nonempty ## Fluent section")

    def make_conversation_fluent_linked(self, project: Path, *, include_mapping: bool, include_section: bool) -> None:
        conversation_path = project / ".scratch/eval/CONVERSATION.md"
        text = conversation_path.read_text(encoding="utf-8").replace("mode: standalone", "mode: fluent-linked", 1)
        if include_mapping:
            text = text.replace(
                f"latest_checkpoint: ../_conversations/sessions/{SESSION_NAME}\n---",
                f"latest_checkpoint: ../_conversations/sessions/{SESSION_NAME}\n"
                "fluent:\n"
                "  draft_ids:\n"
                "    - draft-7\n"
                "  work_item_ids:\n"
                "    - work-item-9\n"
                "---",
                1,
            )
        if include_section:
            text = text.replace(
                "## History\n",
                "## Fluent\n\n"
                "Draft `draft-7` and Work Item `work-item-9` are authoritative in Fluent.\n\n"
                "## History\n",
                1,
            )
        conversation_path.write_text(text, encoding="utf-8")

        router_path = project / ".scratch/CONVERSATIONS.md"
        router_path.write_text(
            router_path.read_text(encoding="utf-8").replace(
                "| standalone |",
                "| fluent-linked |",
                1,
            ),
            encoding="utf-8",
        )

    def test_resume_first_line_cannot_contain_pipe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            conversation_path = project / ".scratch/eval/CONVERSATION.md"
            conversation_path.write_text(
                conversation_path.read_text(encoding="utf-8").replace(
                    "Waiting for user: choose the next experiment.",
                    "Waiting for user: choose A | B.",
                    1,
                ),
                encoding="utf-8",
            )

            errors, _ = validate_conversations.validate_project(project)

        self.assert_has_error(errors, "Resume first line cannot contain a pipe")

    def test_wrapped_blanket_completion_claim_warns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            session_path = project / SESSION_RELATIVE_PATH
            session_path.write_text(
                session_path.read_text(encoding="utf-8").replace(
                    "The experiment choice remains open.",
                    "Everything else is\nDONE and pushed. The experiment choice remains open.",
                ),
                encoding="utf-8",
            )

            errors, warnings = self.validate(project)

        self.assertEqual([], errors)
        self.assertTrue(any("blanket completion" in warning for warning in warnings))


class GitCollectorTests(unittest.TestCase):
    def make_repo(self, directory: Path) -> Path:
        repo = directory / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-qm", "Initial"], check=True)
        return repo

    def test_comparison_ref_is_not_inferred_as_configured_upstream(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.make_repo(Path(directory))
            subprocess.run(
                ["git", "-C", str(repo), "remote", "add", "origin", str(repo)],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", "HEAD"],
                check=True,
            )

            snapshot, had_error = collect_git_state.collect(repo, ["origin/main"])

        self.assertFalse(had_error)
        self.assertIsNone(snapshot["configured_upstream"])
        self.assertEqual("origin/main", snapshot["comparisons"][0]["ref"])
        self.assertEqual(0, snapshot["comparisons"][0]["ahead"])
        self.assertEqual(0, snapshot["comparisons"][0]["behind"])

    def test_configured_upstream_stays_distinct_from_comparison_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.make_repo(Path(directory))
            subprocess.run(
                ["git", "-C", str(repo), "remote", "add", "origin", str(repo)],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", "HEAD"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "update-ref", "refs/remotes/origin/candidate", "HEAD"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "branch", "--set-upstream-to=origin/main"],
                check=True,
                capture_output=True,
                text=True,
            )

            snapshot, had_error = collect_git_state.collect(repo, ["origin/candidate"])

        self.assertFalse(had_error)
        self.assertEqual("origin/main", snapshot["configured_upstream"]["ref"])
        self.assertEqual("origin/candidate", snapshot["comparisons"][0]["ref"])

    def test_missing_comparison_ref_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.make_repo(Path(directory))

            snapshot, had_error = collect_git_state.collect(repo, ["origin/missing"])

        self.assertTrue(had_error)
        self.assertEqual("origin/missing", snapshot["comparisons"][0]["ref"])
        self.assertIn("error", snapshot["comparisons"][0])
        self.assertTrue(any("could not compare origin/missing" in warning for warning in snapshot["warnings"]))

    def test_unborn_repository_reports_null_head_without_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            subprocess.run(["git", "init", "-q", str(repo)], check=True)

            snapshot, had_error = collect_git_state.collect(repo, [])

        self.assertFalse(had_error)
        self.assertIsNone(snapshot["head"])
        self.assertIsNone(snapshot["configured_upstream"])
        self.assertEqual([], snapshot["warnings"])

    def test_malformed_configured_upstream_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.make_repo(Path(directory))
            branch = subprocess.run(
                ["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(repo)], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", f"branch.{branch}.remote", "origin"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "config", f"branch.{branch}.merge", "refs/heads/missing"],
                check=True,
            )

            snapshot, had_error = collect_git_state.collect(repo, [])

        self.assertTrue(had_error)
        self.assertIsNone(snapshot["configured_upstream"])
        self.assertIsNotNone(snapshot["configured_upstream_error"])
        self.assertTrue(any("configured upstream" in warning for warning in snapshot["warnings"]))


class SkillPackageTests(unittest.TestCase):
    SKILLS = ("save-conversation", "resume-conversation")
    SHARED_RESOURCES = (
        "references/evidence.md",
        "references/fluent.md",
        "scripts/collect_git_state.py",
        "scripts/validate_conversations.py",
    )

    def test_each_skill_is_self_contained(self) -> None:
        for skill_name in self.SKILLS:
            with self.subTest(skill=skill_name):
                skill_root = REPO_ROOT / "skills" / skill_name
                skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
                self.assertNotIn("../save-conversation", skill_text)
                self.assertNotIn("../resume-conversation", skill_text)
                for relative_path in self.SHARED_RESOURCES:
                    self.assertTrue((skill_root / relative_path).is_file(), relative_path)
                for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", skill_text):
                    if target.startswith(("references/", "scripts/")):
                        self.assertTrue((skill_root / target).resolve().is_file(), target)

    def test_shared_runtime_resources_match(self) -> None:
        save_root = REPO_ROOT / "skills/save-conversation"
        resume_root = REPO_ROOT / "skills/resume-conversation"
        for relative_path in self.SHARED_RESOURCES:
            with self.subTest(resource=relative_path):
                self.assertEqual(
                    (save_root / relative_path).read_bytes(),
                    (resume_root / relative_path).read_bytes(),
                )

    def test_skill_files_stay_under_progressive_disclosure_limit(self) -> None:
        for skill_name in self.SKILLS:
            with self.subTest(skill=skill_name):
                lines = (REPO_ROOT / "skills" / skill_name / "SKILL.md").read_text(encoding="utf-8").splitlines()
                self.assertLessEqual(len(lines), 500)

    def test_resume_distinguishes_loading_from_execution_authority(self) -> None:
        resume_text = (REPO_ROOT / "skills/resume-conversation/SKILL.md").read_text(encoding="utf-8")

        self.assertIn("A bare request to resume, restore, or load supplies no execution authorization", resume_text)
        self.assertIn("asks both to load the conversation and continue the work", resume_text)

    def test_automatic_save_can_be_a_no_op_and_continue_authorized_work(self) -> None:
        save_root = REPO_ROOT / "skills/save-conversation"
        save_text = (save_root / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("a successful save and a no-op are completely silent", save_text)
        self.assertIn("cannot be reconstructed from Git", save_text)
        self.assertIn("Do not checkpoint an unfinished diagnosis, design discussion", save_text)
        self.assertIn("Stop the skill without inspecting saved conversations", save_text)
        self.assertIn("Continue or finish only work already authorized", save_text)
        self.assertTrue((save_root / "scripts/conversation_lock.py").is_file())

    def test_save_recovers_failed_publication_and_preserves_user_owned_files(self) -> None:
        save_text = (REPO_ROOT / "skills/save-conversation/SKILL.md").read_text(encoding="utf-8")

        self.assertIn("Never replace it in this skill", save_text)
        self.assertIn(".scratch/_conversations/.staging/<token>/", save_text)
        self.assertIn(".scratch/_conversations/RECOVERY_REQUIRED.json", save_text)
        self.assertIn("Any failure after staging begins enters cleanup", save_text)
        self.assertIn("Report only a publication or restoration failure", save_text)

    def test_automatic_resume_does_not_select_unrelated_work(self) -> None:
        resume_text = (REPO_ROOT / "skills/resume-conversation/SKILL.md").read_text(encoding="utf-8")

        self.assertIn("Do not fall back to the sole nonterminal conversation", resume_text)
        self.assertIn("an automatic check skips the load silently", resume_text)
        self.assertIn(".scratch/_conversations/RECOVERY_REQUIRED.json", resume_text)
        self.assertIn("do not read the in-flight or possibly mixed canonical projection", resume_text)


if __name__ == "__main__":
    unittest.main()
