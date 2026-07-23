from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIRECTORY = REPO_ROOT / "skills/save-conversation/scripts"
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

import publish_conversation as publisher  # noqa: E402

from tests.test_conversation_scripts import (  # noqa: E402
    CONVERSATION,
    ROUTER,
    SESSION,
    SESSION_RELATIVE_PATH,
)


NEW_SESSION_NAME = "2026-07-23T120000-0700-codex.md"
BOUNDARY = "0123456789abcdef0123456789abcdef"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class ConversationPublisherTests(unittest.TestCase):
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

    def candidates(self, project: Path) -> tuple[str, str]:
        checkpoint = SESSION.format(project=project).replace(
            "created_at: 2026-07-21T12:00:00-07:00",
            "created_at: 2026-07-23T12:00:00-07:00",
            1,
        ).replace(
            "# Session checkpoint: eval choice",
            "# Session checkpoint: revised eval choice",
            1,
        )
        current = CONVERSATION.replace(
            "updated_at: 2026-07-21T12:00:00-07:00",
            "updated_at: 2026-07-23T12:00:00-07:00",
            1,
        ).replace(
            "../_conversations/sessions/2026-07-21T120000-0700-codex.md",
            "../_conversations/sessions/@CHECKPOINT@",
        )
        return checkpoint, current

    def request_bytes(
        self,
        project: Path,
        *,
        checkpoint: str | None = None,
        current: str | None = None,
        index_base: str | None = None,
        current_base: str | None = None,
        checkpoint_name: str = NEW_SESSION_NAME,
    ) -> bytes:
        default_checkpoint, default_current = self.candidates(project)
        checkpoint = default_checkpoint if checkpoint is None else checkpoint
        current = default_current if current is None else current
        index_path = project / ".scratch/CONVERSATIONS.md"
        current_path = project / ".scratch/eval/CONVERSATION.md"
        index_base = index_base or f"sha256:{digest(index_path.read_bytes())}"
        current_base = current_base or f"sha256:{digest(current_path.read_bytes())}"
        return (
            "conversation_continuity_request: 1\n"
            f"boundary: {BOUNDARY}\n"
            f"checkpoint_name: {checkpoint_name}\n"
            f"index_base: {index_base}\n"
            f"conversation: eval {current_base}\n"
            "\n"
            f"--{BOUNDARY} checkpoint--\n"
            f"{checkpoint}"
            f"--{BOUNDARY} conversation eval--\n"
            f"{current}"
            f"--{BOUNDARY} end--\n"
        ).encode("utf-8")

    def canonical_snapshot(self, project: Path) -> dict[str, tuple[bytes, int, int]]:
        result = {}
        for path in sorted((project / ".scratch").glob("**/*")):
            if path.is_file():
                details = path.stat()
                result[str(path.relative_to(project))] = (
                    path.read_bytes(),
                    details.st_ino,
                    details.st_mtime_ns,
                )
        return result

    def canonical_bytes(self, project: Path) -> dict[str, bytes]:
        return {
            path: details[0]
            for path, details in self.canonical_snapshot(project).items()
        }

    def projection_bytes(self, project: Path) -> dict[str, bytes]:
        return {
            path: content
            for path, content in self.canonical_bytes(project).items()
            if "/.write-lock/" not in path
            and "/.staging/" not in path
            and not path.endswith("/RECOVERY_REQUIRED.json")
        }

    def assert_transaction_clean(self, project: Path) -> None:
        internal = project / ".scratch/_conversations"
        self.assertFalse((internal / "RECOVERY_REQUIRED.json").exists())
        self.assertFalse((internal / ".write-lock").exists())
        staging = internal / ".staging"
        self.assertEqual([], list(staging.iterdir()) if staging.exists() else [])

    def test_publish_writes_candidates_and_derives_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            checkpoint, current = self.candidates(project)
            request = publisher.parse_request(self.request_bytes(project))

            result = publisher.publish(project, request, wait_seconds=0)

            checkpoint_path = project / ".scratch/_conversations/sessions" / NEW_SESSION_NAME
            current_path = project / ".scratch/eval/CONVERSATION.md"
            router = (project / ".scratch/CONVERSATIONS.md").read_text(encoding="utf-8")
            self.assertEqual("published", result["status"])
            self.assertEqual(checkpoint, checkpoint_path.read_text(encoding="utf-8"))
            self.assertEqual(
                current.replace("@CHECKPOINT@", NEW_SESSION_NAME),
                current_path.read_text(encoding="utf-8"),
            )
            self.assertIn("updated_at: 2026-07-23T12:00:00-07:00", router)
            self.assertIn(
                "| [eval](eval/CONVERSATION.md) | waiting-user | standalone | "
                "2026-07-23T12:00:00-07:00 | Waiting for user: choose the next experiment. |",
                router,
            )
            self.assert_transaction_clean(project)

    def test_checkpoint_collision_uses_a_new_immutable_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            collision = project / ".scratch/_conversations/sessions" / NEW_SESSION_NAME
            collision.write_text("published bytes\n", encoding="utf-8")
            request = publisher.parse_request(self.request_bytes(project))

            result = publisher.publish(project, request, wait_seconds=0)

            suffixed = collision.with_name("2026-07-23T120000-0700-codex-2.md")
            self.assertEqual("published", result["status"])
            self.assertEqual(b"published bytes\n", collision.read_bytes())
            self.assertTrue(suffixed.is_file())
            self.assertIn(
                suffixed.name,
                (project / ".scratch/eval/CONVERSATION.md").read_text(encoding="utf-8"),
            )

    def test_publish_bootstraps_an_absent_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            checkpoint, current = self.candidates(project)
            request = publisher.parse_request(
                self.request_bytes(
                    project,
                    checkpoint=checkpoint,
                    current=current,
                    index_base="absent",
                    current_base="absent",
                )
            )

            result = publisher.publish(project, request, wait_seconds=0)

            self.assertEqual("published", result["status"])
            self.assertTrue((project / ".scratch/CONVERSATIONS.md").is_file())
            self.assertTrue((project / ".scratch/eval/CONVERSATION.md").is_file())
            self.assert_transaction_clean(project)

    def test_multi_conversation_request_generates_deterministic_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            checkpoint, current = self.candidates(project)
            checkpoint = checkpoint.replace(
                "conversations:\n  - eval",
                "conversations:\n  - eval\n  - other",
                1,
            )
            other = current.replace("conversation: eval", "conversation: other", 1).replace(
                "# Conversation: Eval",
                "# Conversation: Other",
                1,
            )
            request_text = self.request_bytes(project, checkpoint=checkpoint).decode("utf-8")
            request_text = request_text.replace(
                "conversation: eval sha256:",
                "conversation: other absent\nconversation: eval sha256:",
                1,
            ).replace(
                f"--{BOUNDARY} end--",
                f"--{BOUNDARY} conversation other--\n{other}"
                f"--{BOUNDARY} end--",
                1,
            )
            request = publisher.parse_request(request_text.encode("utf-8"))

            result = publisher.publish(project, request, wait_seconds=0)

            router = (project / ".scratch/CONVERSATIONS.md").read_text(encoding="utf-8")
            eval_position = router.index("| [eval](eval/CONVERSATION.md)")
            other_position = router.index("| [other](other/CONVERSATION.md)")
            self.assertEqual("published", result["status"])
            self.assertLess(eval_position, other_position)
            self.assertTrue((project / ".scratch/other/CONVERSATION.md").is_file())

    def test_invalid_candidate_does_not_replace_canonical_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            before = self.canonical_snapshot(project)
            _, current = self.candidates(project)
            invalid_current = current.replace("## Evidence", "## Missing evidence", 1)
            request = publisher.parse_request(self.request_bytes(project, current=invalid_current))

            with self.assertRaises(publisher.PublishError) as raised:
                publisher.publish(project, request, wait_seconds=0)

            self.assertEqual("invalid-request", raised.exception.status)
            self.assertEqual(before, self.canonical_snapshot(project))
            self.assert_transaction_clean(project)

    def test_warning_requires_review_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            before = self.canonical_snapshot(project)
            checkpoint, _ = self.candidates(project)
            checkpoint = checkpoint.replace(
                "The experiment choice remains open.",
                "Everything else is done and pushed. The experiment choice remains open.",
                1,
            )
            request = publisher.parse_request(self.request_bytes(project, checkpoint=checkpoint))

            with self.assertRaises(publisher.PublishError) as raised:
                publisher.publish(project, request, wait_seconds=0)

            self.assertEqual("request-review-required", raised.exception.status)
            self.assertEqual(before, self.canonical_snapshot(project))

    def test_stale_base_does_not_replace_canonical_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            before = self.canonical_snapshot(project)
            request = publisher.parse_request(
                self.request_bytes(project, current_base=f"sha256:{'0' * 64}")
            )

            with self.assertRaises(publisher.PublishError) as raised:
                publisher.publish(project, request, wait_seconds=0)

            self.assertEqual("conflict", raised.exception.status)
            self.assertEqual(before, self.canonical_snapshot(project))

    def test_unmanaged_current_is_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            current_path = project / ".scratch/eval/CONVERSATION.md"
            current_path.write_text("# User notes\n", encoding="utf-8")
            before = current_path.read_bytes()
            request = publisher.parse_request(
                self.request_bytes(
                    project,
                    current_base=f"sha256:{digest(before)}",
                )
            )

            with self.assertRaises(publisher.PublishError) as raised:
                publisher.publish(project, request, wait_seconds=0)

            self.assertEqual("ownership-conflict", raised.exception.status)
            self.assertEqual(before, current_path.read_bytes())

    def test_symlinked_conversation_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.make_project(root)
            external = root / "external"
            external.mkdir()
            original = project / ".scratch/eval"
            backup = project / ".scratch/eval-original"
            original.rename(backup)
            original.symlink_to(external, target_is_directory=True)
            request = publisher.parse_request(
                self.request_bytes(
                    project,
                    current_base="absent",
                )
            )

            with self.assertRaises(publisher.PublishError) as raised:
                publisher.publish(project, request, wait_seconds=0)

            self.assertEqual("conflict", raised.exception.status)
            self.assertEqual([], list(external.iterdir()))

    def test_canonical_validation_failure_rolls_back_in_one_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            before = self.projection_bytes(project)
            request = publisher.parse_request(self.request_bytes(project))
            real_validate = publisher.validate_conversations.validate_project
            calls = 0

            def fail_second_validation(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return real_validate(*args, **kwargs)
                return ["ERROR injected canonical validation failure"], []

            with mock.patch.object(
                publisher.validate_conversations,
                "validate_project",
                side_effect=fail_second_validation,
            ):
                result = publisher.publish(project, request, wait_seconds=0)

            self.assertEqual("rolled-back", result["status"])
            self.assertEqual(before, self.projection_bytes(project))
            self.assert_transaction_clean(project)

    def test_rollback_reports_a_lock_release_failure_as_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            before = self.projection_bytes(project)
            request = publisher.parse_request(self.request_bytes(project))
            real_validate = publisher.validate_conversations.validate_project
            calls = 0

            def fail_second_validation(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return real_validate(*args, **kwargs)
                return ["ERROR injected canonical validation failure"], []

            with mock.patch.object(
                publisher.validate_conversations,
                "validate_project",
                side_effect=fail_second_validation,
            ), mock.patch.object(
                publisher.conversation_lock,
                "release",
                side_effect=ValueError("injected release failure"),
            ):
                result = publisher.publish(project, request, wait_seconds=0)

            self.assertEqual("rolled-back-with-cleanup-warning", result["status"])
            self.assertEqual(before, self.projection_bytes(project))
            self.assertFalse(
                (project / ".scratch/_conversations/RECOVERY_REQUIRED.json").exists()
            )
            self.assertTrue(any("release publication lock" in value for value in result["warnings"]))

    def test_scoped_publish_preserves_an_unrelated_malformed_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            router_path = project / ".scratch/CONVERSATIONS.md"
            malformed = "| [other](other/CONVERSATION.md) | malformed |\n"
            router_path.write_text(router_path.read_text(encoding="utf-8") + malformed, encoding="utf-8")
            request = publisher.parse_request(self.request_bytes(project))

            result = publisher.publish(project, request, wait_seconds=0)

            self.assertEqual("published", result["status"])
            self.assertEqual(
                1,
                (project / ".scratch/CONVERSATIONS.md").read_text(encoding="utf-8").count(malformed),
            )

    def test_candidate_validation_resolves_project_links_from_their_canonical_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            (project / "README.md").write_text("# Project\n", encoding="utf-8")
            _, current = self.candidates(project)
            current = current.replace(
                "No durable measurement artifact exists.",
                "See the [project README](../../README.md).",
                1,
            )
            request = publisher.parse_request(self.request_bytes(project, current=current))

            result = publisher.publish(project, request, wait_seconds=0)

            self.assertEqual("published", result["status"])

    def test_snapshot_returns_owned_base_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))

            result = publisher.snapshot(project, ("eval", "new-area"))

            self.assertEqual("snapshot", result["status"])
            self.assertEqual("sha256", result["index"]["state"])
            self.assertEqual("sha256", result["conversations"]["eval"]["state"])
            self.assertEqual({"state": "absent"}, result["conversations"]["new-area"])
            self.assertEqual(
                [
                    f"index_base: sha256:{result['index']['value']}",
                    f"conversation: eval sha256:{result['conversations']['eval']['value']}",
                    "conversation: new-area absent",
                ],
                result["request_headers"],
            )

    def test_snapshot_archives_a_stale_publication_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            publisher.conversation_lock.acquire(project, wait_seconds=0, stale_seconds=60)
            lock = project / publisher.conversation_lock.LOCK_RELATIVE_PATH
            owner_path = lock / publisher.conversation_lock.OWNER_FILE
            owner = json.loads(owner_path.read_text(encoding="utf-8"))
            owner["created_unix"] = 0
            owner["refreshed_unix"] = 0
            owner_path.write_text(json.dumps(owner), encoding="utf-8")

            result = publisher.snapshot(project, ("eval",), wait_seconds=0)

            self.assertEqual("snapshot", result["status"])
            self.assertFalse(lock.exists())
            self.assertEqual(1, len(list(lock.parent.glob(".write-lock.stale-*"))))
            self.assertTrue(
                any("archived stale publication lock" in value for value in result["warnings"])
            )

    def test_snapshot_reports_markerless_private_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            orphan = project / ".scratch/_conversations/.staging" / ("a" * 32)
            orphan.mkdir(parents=True)
            (orphan / "partial").write_text("private candidate\n", encoding="utf-8")

            result = publisher.snapshot(project, ("eval",), wait_seconds=0)

            self.assertEqual("snapshot", result["status"])
            self.assertTrue(orphan.is_dir())
            self.assertTrue(
                any(str(orphan) in value for value in result["warnings"])
            )

    def test_cli_publishes_the_stdin_request_in_one_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIRECTORY / "publish_conversation.py"),
                    "publish",
                    str(project),
                    "--request",
                    "-",
                    "--wait",
                    "0",
                ],
                input=self.request_bytes(project),
                capture_output=True,
                check=False,
            )

            output = json.loads(result.stdout)
            self.assertEqual(0, result.returncode, result.stderr.decode())
            self.assertEqual("published", output["status"])
            self.assertTrue(
                (project / ".scratch/_conversations/sessions" / NEW_SESSION_NAME).is_file()
            )

    def test_random_quoted_heredoc_delimiter_treats_saved_lines_as_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            checkpoint, _ = self.candidates(project)
            checkpoint = checkpoint.replace(
                "The agent measured the current behavior.",
                "The transcript contained this standalone line:\n\nCONVERSATION_SAVE\n",
                1,
            )
            request = self.request_bytes(project, checkpoint=checkpoint).decode("utf-8")
            delimiter = "CONVERSATION_SAVE_fedcba9876543210"
            self.assertNotIn(f"\n{delimiter}\n", request)
            command = (
                f"{shlex.quote(sys.executable)} "
                f"{shlex.quote(str(SCRIPT_DIRECTORY / 'publish_conversation.py'))} "
                f"publish {shlex.quote(str(project))} --request - --wait 0 "
                f"<<'{delimiter}'\n"
                f"{request}"
                f"{delimiter}\n"
            )

            result = subprocess.run(
                ["/bin/sh", "-c", command],
                capture_output=True,
                check=False,
                text=True,
            )

            output = json.loads(result.stdout)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("published", output["status"])

    def test_request_rejects_duplicate_headers_and_parts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            request = self.request_bytes(project)
            duplicate_header = request.replace(
                b"index_base: ",
                b"index_base: absent\nindex_base: ",
                1,
            )
            duplicate_part = request.replace(
                f"--{BOUNDARY} end--".encode(),
                (
                    f"--{BOUNDARY} conversation eval--\n"
                    "duplicate\n"
                    f"--{BOUNDARY} end--"
                ).encode(),
                1,
            )

            for invalid in (duplicate_header, duplicate_part):
                with self.subTest(invalid=invalid[:80]):
                    with self.assertRaises(publisher.PublishError) as raised:
                        publisher.parse_request(invalid)
                    self.assertEqual("invalid-request", raised.exception.status)

    def test_request_file_must_not_be_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.make_project(root)
            request = root / "request.txt"
            request.write_bytes(self.request_bytes(project))
            link = root / "request-link.txt"
            link.symlink_to(request)

            with self.assertRaises(publisher.PublishError) as raised:
                publisher.read_request(str(link))

            self.assertEqual("invalid-request", raised.exception.status)

    def test_os_failure_after_checkpoint_publication_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            before = self.projection_bytes(project)
            request = publisher.parse_request(self.request_bytes(project))
            real_replace = publisher.atomic_replace_bytes
            failed = False

            def fail_current(path: Path, content: bytes, mode: int = 0o600):
                nonlocal failed
                if path.parts[-2:] == ("eval", "CONVERSATION.md") and not failed:
                    failed = True
                    raise OSError("injected current replacement failure")
                return real_replace(path, content, mode)

            with mock.patch.object(
                publisher,
                "atomic_replace_bytes",
                side_effect=fail_current,
            ):
                result = publisher.publish(project, request, wait_seconds=0)

            self.assertEqual("rolled-back", result["status"])
            self.assertEqual(before, self.projection_bytes(project))
            self.assert_transaction_clean(project)

    def test_marker_fsync_failure_keeps_recovery_material_until_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            before = self.canonical_bytes(project)
            request = publisher.parse_request(self.request_bytes(project))
            marker = project.resolve() / ".scratch/_conversations/RECOVERY_REQUIRED.json"
            real_fsync = publisher.fsync_directory
            failed = False

            def fail_after_marker_link(path: Path):
                nonlocal failed
                if marker.exists() and not failed:
                    failed = True
                    raise OSError("injected marker directory fsync failure")
                return real_fsync(path)

            with mock.patch.object(
                publisher,
                "fsync_directory",
                side_effect=fail_after_marker_link,
            ):
                result = publisher.publish(project, request, wait_seconds=0)

            self.assertTrue(failed)
            self.assertEqual("rolled-back", result["status"])
            self.assertEqual(before, self.canonical_bytes(project))
            self.assert_transaction_clean(project)

    def test_commit_directory_fsync_failure_preserves_staging_and_warns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            request = publisher.parse_request(self.request_bytes(project))
            marker = project.resolve() / ".scratch/_conversations/RECOVERY_REQUIRED.json"
            checkpoint = project.resolve() / ".scratch/_conversations/sessions" / NEW_SESSION_NAME
            staging = project.resolve() / ".scratch/_conversations/.staging"
            real_fsync = publisher.fsync_directory
            failed = False

            def fail_after_commit_unlink(path: Path):
                nonlocal failed
                if checkpoint.exists() and not marker.exists() and not failed:
                    failed = True
                    raise OSError("injected commit directory fsync failure")
                return real_fsync(path)

            with mock.patch.object(
                publisher,
                "fsync_directory",
                side_effect=fail_after_commit_unlink,
            ):
                result = publisher.publish(project, request, wait_seconds=0)

            self.assertTrue(failed)
            self.assertEqual("published-with-durability-warning", result["status"])
            self.assertTrue(checkpoint.is_file())
            self.assertFalse(marker.exists())
            self.assertEqual(1, len(list(staging.iterdir())))
            self.assertTrue(
                any("durable removal" in value for value in result["warnings"])
            )

    def test_interrupt_after_commit_marker_unlink_preserves_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            request = publisher.parse_request(self.request_bytes(project))
            marker = project.resolve() / ".scratch/_conversations/RECOVERY_REQUIRED.json"
            checkpoint = project.resolve() / ".scratch/_conversations/sessions" / NEW_SESSION_NAME
            staging = project.resolve() / ".scratch/_conversations/.staging"
            lock = project.resolve() / publisher.conversation_lock.LOCK_RELATIVE_PATH
            real_unlink = Path.unlink
            interrupted = False

            def interrupt_after_marker_unlink(path: Path, *args, **kwargs):
                nonlocal interrupted
                real_unlink(path, *args, **kwargs)
                if path == marker and not interrupted:
                    interrupted = True
                    raise KeyboardInterrupt("injected post-unlink interruption")

            with mock.patch.object(
                Path,
                "unlink",
                new=interrupt_after_marker_unlink,
            ), self.assertRaises(KeyboardInterrupt):
                publisher.publish(project, request, wait_seconds=0)

            self.assertTrue(interrupted)
            self.assertTrue(checkpoint.is_file())
            self.assertFalse(marker.exists())
            self.assertEqual(1, len(list(staging.iterdir())))
            self.assertTrue(lock.is_dir())

    def test_rollback_marker_fsync_failure_reports_restored_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            before = self.projection_bytes(project)
            request = publisher.parse_request(self.request_bytes(project))
            marker = project.resolve() / ".scratch/_conversations/RECOVERY_REQUIRED.json"
            checkpoint = project.resolve() / ".scratch/_conversations/sessions" / NEW_SESSION_NAME
            staging = project.resolve() / ".scratch/_conversations/.staging"
            real_validate = publisher.validate_conversations.validate_project
            real_fsync = publisher.fsync_directory
            validation_calls = 0
            saw_marker = False
            failed = False

            def fail_second_validation(*args, **kwargs):
                nonlocal validation_calls
                validation_calls += 1
                if validation_calls == 1:
                    return real_validate(*args, **kwargs)
                return ["ERROR injected canonical validation failure"], []

            def fail_rollback_commit(path: Path):
                nonlocal saw_marker, failed
                if marker.exists():
                    saw_marker = True
                if saw_marker and not marker.exists() and not checkpoint.exists() and not failed:
                    failed = True
                    raise OSError("injected rollback directory fsync failure")
                return real_fsync(path)

            with mock.patch.object(
                publisher.validate_conversations,
                "validate_project",
                side_effect=fail_second_validation,
            ), mock.patch.object(
                publisher,
                "fsync_directory",
                side_effect=fail_rollback_commit,
            ):
                result = publisher.publish(project, request, wait_seconds=0)

            self.assertTrue(failed)
            self.assertEqual("rolled-back-with-cleanup-warning", result["status"])
            self.assertEqual(before, self.projection_bytes(project))
            self.assertFalse(marker.exists())
            self.assertEqual(1, len(list(staging.iterdir())))
            self.assertTrue(
                any("durable removal" in value for value in result["warnings"])
            )

    def test_interrupt_after_rollback_marker_unlink_preserves_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            before = self.projection_bytes(project)
            request = publisher.parse_request(self.request_bytes(project))
            marker = project.resolve() / ".scratch/_conversations/RECOVERY_REQUIRED.json"
            staging = project.resolve() / ".scratch/_conversations/.staging"
            lock = project.resolve() / publisher.conversation_lock.LOCK_RELATIVE_PATH
            real_validate = publisher.validate_conversations.validate_project
            real_unlink = Path.unlink
            validation_calls = 0
            interrupted = False

            def fail_second_validation(*args, **kwargs):
                nonlocal validation_calls
                validation_calls += 1
                if validation_calls == 1:
                    return real_validate(*args, **kwargs)
                return ["ERROR injected canonical validation failure"], []

            def interrupt_after_marker_unlink(path: Path, *args, **kwargs):
                nonlocal interrupted
                real_unlink(path, *args, **kwargs)
                if path == marker and not interrupted:
                    interrupted = True
                    raise KeyboardInterrupt("injected post-rollback interruption")

            with mock.patch.object(
                publisher.validate_conversations,
                "validate_project",
                side_effect=fail_second_validation,
            ), mock.patch.object(
                Path,
                "unlink",
                new=interrupt_after_marker_unlink,
            ), self.assertRaises(KeyboardInterrupt):
                publisher.publish(project, request, wait_seconds=0)

            self.assertTrue(interrupted)
            self.assertEqual(before, self.projection_bytes(project))
            self.assertFalse(marker.exists())
            self.assertEqual(1, len(list(staging.iterdir())))
            self.assertTrue(lock.is_dir())

    def leave_recovery_transaction(self, project: Path) -> None:
        request = publisher.parse_request(self.request_bytes(project))
        real_validate = publisher.validate_conversations.validate_project
        calls = 0

        def fail_second_validation(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_validate(*args, **kwargs)
            return ["ERROR injected canonical validation failure"], []

        with mock.patch.object(
            publisher.validate_conversations,
            "validate_project",
            side_effect=fail_second_validation,
        ), mock.patch.object(
            publisher,
            "rollback_transaction",
            side_effect=publisher.PublishError(
                "recovery-required",
                "injected recovery interruption",
            ),
        ):
            with self.assertRaises(publisher.PublishError) as raised:
                publisher.publish(project, request, wait_seconds=0)
        self.assertEqual("recovery-required", raised.exception.status)

    def test_explicit_recovery_restores_a_valid_interrupted_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            before = self.canonical_bytes(project)
            self.leave_recovery_transaction(project)

            result = publisher.recover(project, wait_seconds=0)

            self.assertEqual("recovered-retry-required", result["status"])
            self.assertEqual(before, self.canonical_bytes(project))
            self.assert_transaction_clean(project)

    def test_recovery_marker_fsync_failure_reports_restored_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            before = self.projection_bytes(project)
            self.leave_recovery_transaction(project)
            marker = project.resolve() / ".scratch/_conversations/RECOVERY_REQUIRED.json"
            staging = project.resolve() / ".scratch/_conversations/.staging"
            real_fsync = publisher.fsync_directory
            saw_marker = False
            failed = False

            def fail_recovery_commit(path: Path):
                nonlocal saw_marker, failed
                if marker.exists():
                    saw_marker = True
                if saw_marker and not marker.exists() and not failed:
                    failed = True
                    raise OSError("injected recovery directory fsync failure")
                return real_fsync(path)

            with mock.patch.object(
                publisher,
                "fsync_directory",
                side_effect=fail_recovery_commit,
            ):
                result = publisher.recover(project, wait_seconds=0)

            self.assertTrue(failed)
            self.assertEqual("recovered-with-cleanup-warning", result["status"])
            self.assertEqual(before, self.projection_bytes(project))
            self.assertFalse(marker.exists())
            self.assertEqual(1, len(list(staging.iterdir())))
            self.assertTrue(
                any("durable removal" in value for value in result["warnings"])
            )

    def test_recovery_reports_a_lock_release_failure_as_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            before = self.projection_bytes(project)
            self.leave_recovery_transaction(project)

            with mock.patch.object(
                publisher.conversation_lock,
                "release",
                side_effect=ValueError("injected release failure"),
            ):
                result = publisher.recover(project, wait_seconds=0)

            self.assertEqual("recovered-with-cleanup-warning", result["status"])
            self.assertEqual(before, self.projection_bytes(project))
            self.assertFalse(
                (project / ".scratch/_conversations/RECOVERY_REQUIRED.json").exists()
            )
            self.assertTrue(any("release publication lock" in value for value in result["warnings"]))

    def test_recovery_preserves_material_when_a_live_target_is_unexpected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))
            self.leave_recovery_transaction(project)
            current = project / ".scratch/eval/CONVERSATION.md"
            current.write_text("unexpected concurrent bytes\n", encoding="utf-8")
            marker = project / ".scratch/_conversations/RECOVERY_REQUIRED.json"

            with self.assertRaises(publisher.PublishError) as raised:
                publisher.recover(project, wait_seconds=0)

            self.assertEqual("recovery-required", raised.exception.status)
            self.assertEqual(b"unexpected concurrent bytes\n", current.read_bytes())
            self.assertTrue(marker.is_file())

    def test_recovery_rejects_a_symlinked_staging_component(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.make_project(root)
            self.leave_recovery_transaction(project)
            marker_path = project / ".scratch/_conversations/RECOVERY_REQUIRED.json"
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            stage = project / ".scratch" / Path(marker["manifest"]).parent
            candidate_directory = stage / "candidate/eval"
            candidate_bytes = (candidate_directory / "CONVERSATION.md").read_bytes()
            candidate_directory.rename(stage / "candidate/eval-original")
            external = root / "external"
            external.mkdir()
            (external / "CONVERSATION.md").write_bytes(candidate_bytes)
            candidate_directory.symlink_to(external, target_is_directory=True)

            with self.assertRaises(publisher.PublishError) as raised:
                publisher.recover(project, wait_seconds=0)

            self.assertEqual("recovery-required", raised.exception.status)
            self.assertEqual(candidate_bytes, (external / "CONVERSATION.md").read_bytes())
            self.assertTrue(marker_path.is_file())


if __name__ == "__main__":
    unittest.main()
