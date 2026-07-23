from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


continuity = load_module(
    "conversation_continuity",
    REPO_ROOT / "configuration/conversation_continuity.py",
)
conversation_lock = load_module(
    "conversation_lock",
    REPO_ROOT / "skills/save-conversation/scripts/conversation_lock.py",
)


class ConversationContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "home"
        self.project = Path(self.temporary.name) / "project"
        self.home.mkdir()
        self.project.mkdir()
        (self.project / ".git").mkdir()
        self.environment = mock.patch.dict(
            os.environ,
            {
                "HOME": str(self.home),
                continuity.AUTOMATION_ENV: "on",
                continuity.CONFIG_ENV: str(self.home / "no-config.json"),
                continuity.STATE_DIR_ENV: str(self.home / "state"),
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def write_router(self, suffix: str = "") -> Path:
        router = self.project / ".scratch" / "CONVERSATIONS.md"
        router.parent.mkdir(parents=True, exist_ok=True)
        router.write_text(
            "---\n"
            "managed_by: conversation-continuity\n"
            "conversation_version: 1\n"
            "updated_at: 2026-07-22T12:00:00-07:00\n"
            "---\n"
            "# Conversations\n"
            f"{suffix}\n",
            encoding="utf-8",
        )
        return router

    def event(self, name: str, **values):
        return {
            "hook_event_name": name,
            "session_id": "session-1",
            "cwd": str(self.project),
            "permission_mode": "default",
            **values,
        }

    def test_startup_injects_resume_only_for_managed_router(self) -> None:
        self.write_router()

        startup = continuity.process_hook(self.event("SessionStart", source="startup"), "codex", self.home, 100)
        native_resume = continuity.process_hook(self.event("SessionStart", source="resume"), "codex", self.home, 101)

        self.assertEqual(startup["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("automatic startup path", startup["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(native_resume, {})

    def test_startup_skips_unmanaged_router(self) -> None:
        router = self.project / ".scratch" / "CONVERSATIONS.md"
        router.parent.mkdir(parents=True)
        router.write_text(
            "# User notes\n\nExample: managed_by: conversation-continuity and conversation_version: 1\n",
            encoding="utf-8",
        )

        result = continuity.process_hook(self.event("SessionStart", source="startup"), "claude", self.home, 100)

        self.assertEqual(result, {})
        self.assertFalse((self.home / "state").exists())

    def test_workspace_container_uses_its_main_worktree(self) -> None:
        workspace = Path(self.temporary.name) / "workspace"
        main = workspace / "main"
        main.mkdir(parents=True)
        (main / ".git").mkdir()
        router = main / ".scratch" / "CONVERSATIONS.md"
        router.parent.mkdir()
        router.write_text(
            "---\nmanaged_by: conversation-continuity\nconversation_version: 1\n---\n",
            encoding="utf-8",
        )

        scope = continuity.find_project_scope(str(workspace))

        self.assertIsNotNone(scope)
        self.assertEqual(scope.root, main.resolve())
        self.assertEqual(scope.router, router.resolve())
        self.assertTrue(scope.managed)

    def test_linked_worktree_uses_the_primary_worktree(self) -> None:
        primary = Path(self.temporary.name) / "workspace" / "main"
        peer = Path(self.temporary.name) / "workspace" / "feature"
        git_directory = primary / ".git" / "worktrees" / "feature"
        git_directory.mkdir(parents=True)
        peer.mkdir(parents=True)
        (git_directory / "commondir").write_text("../..\n", encoding="utf-8")
        (peer / ".git").write_text(f"gitdir: {git_directory}\n", encoding="utf-8")
        router = primary / ".scratch" / "CONVERSATIONS.md"
        router.parent.mkdir()
        router.write_text(
            "---\nmanaged_by: conversation-continuity\nconversation_version: 1\n---\n",
            encoding="utf-8",
        )

        scope = continuity.find_project_scope(str(peer))

        self.assertIsNotNone(scope)
        self.assertEqual(scope.root, primary.resolve())
        self.assertEqual(scope.router, router.resolve())
        self.assertTrue(scope.managed)

    def test_bypass_mode_skips_startup_resume_for_workers(self) -> None:
        self.write_router()

        result = continuity.process_hook(
            self.event("SessionStart", source="startup", permission_mode="bypassPermissions"),
            "claude",
            self.home,
            100,
        )

        self.assertEqual(result, {})
        self.assertFalse((self.home / "state").exists())

    def test_first_stop_and_every_third_later_stop_request_save_check(self) -> None:
        continuity.process_hook(self.event("SessionStart", source="startup"), "codex", self.home, 100)

        first = continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 101)
        continued = continuity.process_hook(self.event("Stop", stop_hook_active=True), "codex", self.home, 102)
        later = [
            continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, now)
            for now in (103, 104, 105)
        ]

        self.assertEqual(first["decision"], "block")
        self.assertIn("automatic path", first["reason"])
        self.assertEqual(continued, {})
        self.assertEqual(later[0], {})
        self.assertEqual(later[1], {})
        self.assertEqual(later[2]["decision"], "block")

    def test_another_session_router_change_does_not_suppress_a_due_check(self) -> None:
        router = self.write_router()
        continuity.process_hook(self.event("SessionStart", source="startup"), "claude", self.home, 100)
        router.write_text(router.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")

        result = continuity.process_hook(self.event("Stop", stop_hook_active=False), "claude", self.home, 101)

        self.assertEqual(result["decision"], "block")

    def test_elapsed_time_requests_save_before_turn_interval(self) -> None:
        continuity.process_hook(self.event("SessionStart", source="startup"), "codex", self.home, 100)
        continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 101)

        result = continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 1002)

        self.assertEqual(result["decision"], "block")

    def test_plan_mode_defers_and_bypass_mode_skips(self) -> None:
        continuity.process_hook(self.event("SessionStart", source="startup"), "codex", self.home, 100)

        planned = continuity.process_hook(
            self.event("Stop", stop_hook_active=False, permission_mode="plan"),
            "codex",
            self.home,
            101,
        )
        bypassed = continuity.process_hook(
            self.event("Stop", stop_hook_active=False, permission_mode="bypassPermissions"),
            "codex",
            self.home,
            102,
        )
        resumed = continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 103)

        self.assertEqual(planned, {})
        self.assertEqual(bypassed, {})
        self.assertEqual(resumed["decision"], "block")

    def test_force_setting_allows_bypass_mode(self) -> None:
        continuity.process_hook(self.event("SessionStart", source="startup"), "codex", self.home, 100)
        with mock.patch.dict(os.environ, {continuity.AUTOMATION_ENV: "force"}):
            result = continuity.process_hook(
                self.event("Stop", stop_hook_active=False, permission_mode="bypassPermissions"),
                "codex",
                self.home,
                101,
            )

        self.assertEqual(result["decision"], "block")

    def test_off_setting_disables_hooks_and_statusline(self) -> None:
        status = {
            "session_id": "session-1",
            "cwd": str(self.project),
            "context_window": {"used_percentage": 55},
        }
        with mock.patch.dict(os.environ, {continuity.AUTOMATION_ENV: "off"}):
            hook = continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 100)
            rendered = continuity.process_statusline(status, self.home, 100)

        self.assertEqual(hook, {})
        self.assertEqual(rendered, "")
        self.assertFalse((self.home / "state").exists())

    def test_statusline_threshold_schedules_save_and_renders_percentage(self) -> None:
        continuity.process_hook(self.event("SessionStart", source="startup"), "claude", self.home, 100)
        continuity.process_hook(self.event("Stop", stop_hook_active=False), "claude", self.home, 101)
        status = {
            "session_id": "session-1",
            "cwd": str(self.project),
            "context_window": {"used_percentage": 55.4},
        }

        rendered = continuity.process_statusline(status, self.home, 200)
        result = continuity.process_hook(self.event("Stop", stop_hook_active=False), "claude", self.home, 201)

        self.assertEqual(rendered, "ctx 55%")
        self.assertEqual(result["decision"], "block")

    def test_router_change_does_not_suppress_a_context_threshold_check(self) -> None:
        router = self.write_router()
        continuity.process_hook(self.event("SessionStart", source="startup"), "claude", self.home, 100)
        continuity.process_hook(self.event("Stop", stop_hook_active=False), "claude", self.home, 101)
        router.write_text(router.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        status = {
            "session_id": "session-1",
            "cwd": str(self.project),
            "context_window": {"used_percentage": 55},
        }

        continuity.process_statusline(status, self.home, 102)
        result = continuity.process_hook(self.event("Stop", stop_hook_active=False), "claude", self.home, 103)

        self.assertEqual(result["decision"], "block")

    def test_statusline_writes_state_only_when_a_threshold_changes(self) -> None:
        status = {
            "session_id": "session-1",
            "cwd": str(self.project),
            "context_window": {"used_percentage": 10},
        }

        with mock.patch.object(continuity, "write_state", wraps=continuity.write_state) as write_state:
            continuity.process_statusline(status, self.home, 100)
            continuity.process_statusline({**status, "context_window": {"used_percentage": 55}}, self.home, 101)
            continuity.process_statusline({**status, "context_window": {"used_percentage": 56}}, self.home, 102)

        self.assertEqual(write_state.call_count, 1)

    def test_compaction_injects_resume_and_requests_save_at_stop(self) -> None:
        self.write_router()

        context = continuity.process_hook(self.event("SessionStart", source="compact"), "claude", self.home, 100)
        stopped = continuity.process_hook(self.event("Stop", stop_hook_active=False), "claude", self.home, 101)

        self.assertIn("post-compaction", context["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(stopped["decision"], "block")

    def test_corrupt_state_fails_open_to_a_fresh_schedule(self) -> None:
        scope = continuity.find_project_scope(str(self.project))
        self.assertIsNotNone(scope)
        path = continuity.state_path(self.home / "state", "codex", "session-1", scope.root)
        path.parent.mkdir(parents=True)
        path.write_text("{bad json", encoding="utf-8")

        result = continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 100)

        self.assertEqual(result["decision"], "block")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], 1)

    def test_invalid_state_fields_reset_to_a_fresh_schedule(self) -> None:
        scope = continuity.find_project_scope(str(self.project))
        self.assertIsNotNone(scope)
        path = continuity.state_path(self.home / "state", "codex", "session-1", scope.root)
        path.parent.mkdir(parents=True)
        state = continuity.new_state(50)
        state["turns_since_evaluation"] = "bad"
        path.write_text(json.dumps(state), encoding="utf-8")

        result = continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 100)

        self.assertEqual(result["decision"], "block")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["turns_since_evaluation"], 0)

    def test_cli_always_returns_json_for_hook_errors(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "configuration/conversation_continuity.py"),
                "hook",
                "--client",
                "codex",
            ],
            input="not json",
            text=True,
            capture_output=True,
            env={**os.environ, "HOME": str(self.home)},
            check=True,
        )

        self.assertEqual(json.loads(result.stdout), {})


class ConversationLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)

    def test_acquire_and_release(self) -> None:
        token = conversation_lock.acquire(self.project, wait_seconds=0, stale_seconds=60)
        lock = self.project / conversation_lock.LOCK_RELATIVE_PATH

        self.assertTrue(lock.is_dir())
        conversation_lock.release(self.project, token)
        self.assertFalse(lock.exists())

    def test_second_writer_cannot_release_the_lock(self) -> None:
        token = conversation_lock.acquire(self.project, wait_seconds=0, stale_seconds=60)

        with self.assertRaises(ValueError):
            conversation_lock.release(self.project, "wrong-token")

        conversation_lock.release(self.project, token)

    def test_refresh_extends_the_lock_lifetime(self) -> None:
        token = conversation_lock.acquire(self.project, wait_seconds=0, stale_seconds=60)
        lock = self.project / conversation_lock.LOCK_RELATIVE_PATH
        owner_path = lock / conversation_lock.OWNER_FILE
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        owner["created_unix"] = 0
        owner["refreshed_unix"] = 0
        owner_path.write_text(json.dumps(owner), encoding="utf-8")

        conversation_lock.refresh(self.project, token)

        self.assertLess(conversation_lock.lock_age(lock, time.time()), 2)
        conversation_lock.release(self.project, token)

    def test_stale_lock_is_archived_before_acquire(self) -> None:
        first = conversation_lock.acquire(self.project, wait_seconds=0, stale_seconds=60)
        lock = self.project / conversation_lock.LOCK_RELATIVE_PATH
        owner_path = lock / conversation_lock.OWNER_FILE
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        owner["created_unix"] = 0
        owner["refreshed_unix"] = 0
        owner_path.write_text(json.dumps(owner), encoding="utf-8")

        second = conversation_lock.acquire(self.project, wait_seconds=0, stale_seconds=1)

        self.assertNotEqual(first, second)
        self.assertEqual(len(list(lock.parent.glob(".write-lock.stale-*"))), 1)
        conversation_lock.release(self.project, second)


if __name__ == "__main__":
    unittest.main()
