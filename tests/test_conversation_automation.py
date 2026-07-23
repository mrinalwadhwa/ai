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

    def state_path(self, client: str = "codex") -> Path:
        scope = continuity.find_project_scope(str(self.project))
        self.assertIsNotNone(scope)
        return continuity.state_path(self.home / "state", client, "session-1", scope.root)

    def read_state(self, client: str = "codex"):
        return json.loads(self.state_path(client).read_text(encoding="utf-8"))

    def events(self):
        event_directory = self.home / "state" / "events"
        if not event_directory.exists():
            return []
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(event_directory.glob("*.json"))
        ]

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

    def test_default_schedule_is_eight_turns_forty_five_minutes_and_70_85_percent(self) -> None:
        self.assertEqual(
            continuity.load_config(self.home),
            {
                "save_every_turns": 8,
                "save_every_minutes": 45,
                "context_thresholds": [70, 85],
            },
        )

    def test_first_stop_and_eighth_later_stop_request_save_check(self) -> None:
        continuity.process_hook(self.event("SessionStart", source="startup"), "codex", self.home, 100)

        first = continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 101)
        continued = continuity.process_hook(self.event("Stop", stop_hook_active=True), "codex", self.home, 102)
        later = [
            continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, now)
            for now in range(103, 111)
        ]

        self.assertEqual(first["decision"], "block")
        self.assertIn("automatic path", first["reason"])
        self.assertEqual(continued, {})
        self.assertEqual(later[:7], [{}] * 7)
        self.assertEqual(later[7]["decision"], "block")
        self.assertIn("turn-count", later[7]["reason"])

    def test_another_session_router_change_does_not_suppress_a_due_check(self) -> None:
        router = self.write_router()
        continuity.process_hook(self.event("SessionStart", source="startup"), "claude", self.home, 100)
        router.write_text(router.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")

        result = continuity.process_hook(self.event("Stop", stop_hook_active=False), "claude", self.home, 101)

        self.assertEqual(result["decision"], "block")

    def test_issuing_evaluation_leaves_schedule_pending_until_completion(self) -> None:
        continuity.process_hook(self.event("SessionStart", source="startup"), "codex", self.home, 100)

        result = continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 101)
        state = self.read_state()

        self.assertEqual(result["decision"], "block")
        self.assertEqual(state["last_completed_at"], 100)
        self.assertEqual(state["turns_since_completion"], 1)
        self.assertEqual(state["pending_evaluation"]["triggered_at"], 101)
        self.assertEqual(state["pending_evaluation"]["causes"], ["first-session"])
        self.assertEqual(self.events(), [])

    def test_completion_records_index_duration_and_resets_schedule(self) -> None:
        self.write_router()
        continuity.process_hook(self.event("SessionStart", source="startup"), "codex", self.home, 100)
        continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 101)

        continuity.process_hook(self.event("Stop", stop_hook_active=True), "codex", self.home, 140)
        state = self.read_state()
        events = self.events()

        self.assertIsNone(state["pending_evaluation"])
        self.assertEqual(state["last_completed_at"], 140)
        self.assertEqual(state["turns_since_completion"], 0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["outcome"], "index-unchanged")
        self.assertEqual(events[0]["duration_seconds"], 39)
        self.assertFalse(events[0]["index_changed"])
        self.assertEqual(events[0]["completion_source"], "stop-hook")
        event_file = next((self.home / "state" / "events").glob("*.json"))
        self.assertEqual(event_file.stat().st_mode & 0o777, 0o600)

    def test_completion_records_a_changed_index(self) -> None:
        router = self.write_router()
        continuity.process_hook(self.event("SessionStart", source="startup"), "claude", self.home, 100)
        continuity.process_hook(self.event("Stop", stop_hook_active=False), "claude", self.home, 101)
        router.write_text(router.read_text(encoding="utf-8") + "saved\n", encoding="utf-8")

        continuity.process_hook(self.event("Stop", stop_hook_active=True), "claude", self.home, 120)

        event = self.events()[0]
        self.assertEqual(event["outcome"], "index-changed")
        self.assertTrue(event["index_changed"])

    def test_duplicate_ordinary_stop_does_not_reissue_fresh_pending_evaluation(self) -> None:
        continuity.process_hook(self.event("SessionStart", source="startup"), "codex", self.home, 100)
        first = continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 101)
        before = self.read_state()["pending_evaluation"]

        duplicate = continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 102)

        self.assertEqual(first["decision"], "block")
        self.assertEqual(duplicate, {})
        self.assertEqual(self.read_state()["pending_evaluation"], before)

    def test_later_ordinary_stop_redelivers_an_unfinished_evaluation(self) -> None:
        continuity.process_hook(self.event("SessionStart", source="startup"), "codex", self.home, 100)
        continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 101)
        evaluation_id = self.read_state()["pending_evaluation"]["evaluation_id"]

        redelivered = continuity.process_hook(
            self.event("Stop", stop_hook_active=False),
            "codex",
            self.home,
            101 + continuity.DUPLICATE_STOP_SECONDS,
        )

        self.assertEqual(redelivered["decision"], "block")
        self.assertEqual(self.read_state()["pending_evaluation"]["evaluation_id"], evaluation_id)
        self.assertEqual(self.events(), [])

    def test_stale_pending_evaluation_is_logged_and_retried(self) -> None:
        continuity.process_hook(self.event("SessionStart", source="startup"), "codex", self.home, 100)
        continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 101)
        original = self.read_state()["pending_evaluation"]

        retry = continuity.process_hook(
            self.event("Stop", stop_hook_active=False),
            "codex",
            self.home,
            101 + continuity.PENDING_STALE_SECONDS,
        )
        current = self.read_state()["pending_evaluation"]

        self.assertEqual(retry["decision"], "block")
        self.assertNotEqual(current["evaluation_id"], original["evaluation_id"])
        self.assertEqual(current["attempt"], 2)
        self.assertEqual(current["causes"], ["first-session", "retry"])
        self.assertEqual(self.events()[0]["outcome"], "expired")
        self.assertEqual(self.events()[0]["completion_source"], "stale-recovery")

    def test_persisted_stale_finalization_recovers_its_retry(self) -> None:
        continuity.process_hook(self.event("SessionStart", source="startup"), "codex", self.home, 100)
        continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 101)
        path = self.state_path()
        state = self.read_state()
        original = state["pending_evaluation"]
        continuity.prepare_finalization(
            state,
            self.project / ".scratch" / "CONVERSATIONS.md",
            101 + continuity.PENDING_STALE_SECONDS,
            "stale-recovery",
            forced_outcome="expired",
            retry={"causes": ["first-session", "retry"], "attempt": 2},
        )
        state["force_causes"] = ["context-85"]
        continuity.write_state(path, state)

        recovered = continuity.process_hook(
            self.event("Stop", stop_hook_active=False),
            "codex",
            self.home,
            101 + continuity.PENDING_STALE_SECONDS + 1,
        )
        pending = self.read_state()["pending_evaluation"]

        self.assertEqual(recovered["decision"], "block")
        self.assertNotEqual(pending["evaluation_id"], original["evaluation_id"])
        self.assertEqual(pending["causes"], ["first-session", "retry", "context-85"])
        self.assertEqual(pending["attempt"], 2)
        self.assertEqual(len(self.events()), 1)

    def test_persisted_finalization_recovers_once_after_a_crash(self) -> None:
        continuity.process_hook(self.event("SessionStart", source="startup"), "codex", self.home, 100)
        continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 101)
        path = self.state_path()
        state = self.read_state()
        continuity.prepare_finalization(
            state,
            self.project / ".scratch" / "CONVERSATIONS.md",
            120,
            "stop-hook",
        )
        continuity.write_state(path, state)

        result = continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 200)

        self.assertEqual(result, {})
        state = self.read_state()
        self.assertEqual(state["last_completed_at"], 120)
        self.assertEqual(state["turns_since_completion"], 1)
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.events()[0]["completion_source"], "stop-hook")

    def test_active_stop_without_owned_pending_evaluation_does_not_advance_schedule(self) -> None:
        continuity.process_hook(self.event("SessionStart", source="startup"), "codex", self.home, 100)
        before = self.state_path().read_bytes()

        result = continuity.process_hook(self.event("Stop", stop_hook_active=True), "codex", self.home, 200)

        self.assertEqual(result, {})
        self.assertEqual(self.state_path().read_bytes(), before)
        self.assertEqual(self.events(), [])

    def test_elapsed_schedule_starts_when_evaluation_finishes(self) -> None:
        continuity.process_hook(self.event("SessionStart", source="startup"), "codex", self.home, 100)
        continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 101)
        continuity.process_hook(self.event("Stop", stop_hook_active=True), "codex", self.home, 1400)

        before = continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 2800)
        due = continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 4100)

        self.assertEqual(before, {})
        self.assertEqual(due["decision"], "block")
        self.assertIn("elapsed-time", due["reason"])

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
            "context_window": {"used_percentage": 70},
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
        continuity.process_hook(self.event("Stop", stop_hook_active=True), "claude", self.home, 102)
        status = {
            "session_id": "session-1",
            "cwd": str(self.project),
            "context_window": {"used_percentage": 70.4},
        }

        rendered = continuity.process_statusline(status, self.home, 200)
        result = continuity.process_hook(self.event("Stop", stop_hook_active=False), "claude", self.home, 201)

        self.assertEqual(rendered, "ctx 70%")
        self.assertEqual(result["decision"], "block")
        self.assertIn("context-70", result["reason"])

    def test_router_change_does_not_suppress_a_context_threshold_check(self) -> None:
        router = self.write_router()
        continuity.process_hook(self.event("SessionStart", source="startup"), "claude", self.home, 100)
        continuity.process_hook(self.event("Stop", stop_hook_active=False), "claude", self.home, 101)
        continuity.process_hook(self.event("Stop", stop_hook_active=True), "claude", self.home, 102)
        router.write_text(router.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        status = {
            "session_id": "session-1",
            "cwd": str(self.project),
            "context_window": {"used_percentage": 70},
        }

        continuity.process_statusline(status, self.home, 103)
        result = continuity.process_hook(self.event("Stop", stop_hook_active=False), "claude", self.home, 104)

        self.assertEqual(result["decision"], "block")

    def test_statusline_writes_state_only_when_a_threshold_changes(self) -> None:
        status = {
            "session_id": "session-1",
            "cwd": str(self.project),
            "context_window": {"used_percentage": 10},
        }

        with mock.patch.object(continuity, "write_state", wraps=continuity.write_state) as write_state:
            continuity.process_statusline(status, self.home, 100)
            continuity.process_statusline({**status, "context_window": {"used_percentage": 70}}, self.home, 101)
            continuity.process_statusline({**status, "context_window": {"used_percentage": 71}}, self.home, 102)

        self.assertEqual(write_state.call_count, 1)

    def test_context_thresholds_cross_once_and_queue_a_distinct_evaluation(self) -> None:
        continuity.process_hook(self.event("SessionStart", source="startup"), "claude", self.home, 100)
        continuity.process_hook(self.event("Stop", stop_hook_active=False), "claude", self.home, 101)
        status = {
            "session_id": "session-1",
            "cwd": str(self.project),
            "context_window": {"used_percentage": 69.9},
        }

        continuity.process_statusline(status, self.home, 102)
        continuity.process_statusline({**status, "context_window": {"used_percentage": 70}}, self.home, 103)
        continuity.process_statusline({**status, "context_window": {"used_percentage": 84.9}}, self.home, 104)
        continuity.process_statusline({**status, "context_window": {"used_percentage": 90}}, self.home, 105)
        continuity.process_statusline({**status, "context_window": {"used_percentage": 91}}, self.home, 106)

        state = self.read_state("claude")
        self.assertEqual(state["context_thresholds_seen"], [70, 85])
        self.assertEqual(state["pending_evaluation"]["causes"], ["first-session"])
        self.assertEqual(state["force_causes"], ["context-70", "context-85"])

        queued = continuity.process_hook(
            self.event("Stop", stop_hook_active=True),
            "claude",
            self.home,
            107,
        )

        self.assertEqual(queued["decision"], "block")
        self.assertIn("context-70, context-85", queued["reason"])

    def test_native_resume_preserves_pending_state_without_injecting_context(self) -> None:
        continuity.process_hook(self.event("SessionStart", source="startup"), "claude", self.home, 100)
        continuity.process_hook(self.event("Stop", stop_hook_active=False), "claude", self.home, 101)
        before = self.state_path("claude").read_bytes()

        result = continuity.process_hook(
            self.event("SessionStart", source="resume"),
            "claude",
            self.home,
            200,
        )

        self.assertEqual(result, {})
        self.assertEqual(self.state_path("claude").read_bytes(), before)

    def test_compaction_injects_resume_and_requests_save_at_stop(self) -> None:
        self.write_router()

        context = continuity.process_hook(self.event("SessionStart", source="compact"), "claude", self.home, 100)
        stopped = continuity.process_hook(self.event("Stop", stop_hook_active=False), "claude", self.home, 101)

        self.assertIn("post-compaction", context["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(stopped["decision"], "block")
        self.assertIn("post-compaction", stopped["reason"])

    def test_compaction_while_pending_queues_a_distinct_check(self) -> None:
        self.write_router()
        continuity.process_hook(self.event("SessionStart", source="startup"), "claude", self.home, 100)
        continuity.process_hook(self.event("Stop", stop_hook_active=False), "claude", self.home, 101)

        continuity.process_hook(self.event("SessionStart", source="compact"), "claude", self.home, 110)
        pending = self.read_state("claude")["pending_evaluation"]
        completed = continuity.process_hook(
            self.event("Stop", stop_hook_active=True),
            "claude",
            self.home,
            120,
        )
        later = continuity.process_hook(
            self.event("Stop", stop_hook_active=True),
            "claude",
            self.home,
            121,
        )

        self.assertEqual(pending["causes"], ["first-session"])
        self.assertEqual(completed["decision"], "block")
        self.assertIn("post-compaction", completed["reason"])
        self.assertEqual(later, {})
        event_causes = [event["causes"] for event in self.events()]
        self.assertIn(["first-session"], event_causes)
        self.assertIn(["post-compaction"], event_causes)

    def test_valid_v1_state_migrates_without_an_immediate_cadence_check(self) -> None:
        path = self.state_path()
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "turns_since_evaluation": 2,
                    "last_evaluation_at": 50,
                    "force_evaluation": False,
                    "context_thresholds_seen": [55],
                    "last_context_percentage": 60,
                }
            ),
            encoding="utf-8",
        )

        result = continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 100)
        state = self.read_state()

        self.assertEqual(result, {})
        self.assertEqual(state["version"], 2)
        self.assertEqual(state["last_completed_at"], 100)
        self.assertEqual(state["turns_since_completion"], 1)
        self.assertEqual(state["context_thresholds_seen"], [55])

    def test_corrupt_state_fails_open_to_a_fresh_schedule(self) -> None:
        scope = continuity.find_project_scope(str(self.project))
        self.assertIsNotNone(scope)
        path = continuity.state_path(self.home / "state", "codex", "session-1", scope.root)
        path.parent.mkdir(parents=True)
        path.write_text("{bad json", encoding="utf-8")

        result = continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 100)

        self.assertEqual(result["decision"], "block")
        state = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(state["version"], 2)
        self.assertIsNotNone(state["pending_evaluation"])

    def test_invalid_state_fields_reset_to_a_fresh_schedule(self) -> None:
        scope = continuity.find_project_scope(str(self.project))
        self.assertIsNotNone(scope)
        path = continuity.state_path(self.home / "state", "codex", "session-1", scope.root)
        path.parent.mkdir(parents=True)
        state = continuity.new_state(50)
        state["turns_since_completion"] = "bad"
        path.write_text(json.dumps(state), encoding="utf-8")

        result = continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 100)

        self.assertEqual(result["decision"], "block")
        state = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(state["turns_since_completion"], 1)
        self.assertIsNotNone(state["pending_evaluation"])

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

    def test_automatic_instruction_is_silent_and_requires_no_agent_callback(self) -> None:
        continuity.process_hook(self.event("SessionStart", source="startup"), "codex", self.home, 100)

        result = continuity.process_hook(self.event("Stop", stop_hook_active=False), "codex", self.home, 101)

        self.assertIn("Do not announce or narrate the check", result["reason"])
        self.assertIn("Never save recoverable live state", result["reason"])
        self.assertIn("Save unfinished discussion only when", result["reason"])
        self.assertIn("Only report a failure or conflict", result["reason"])
        self.assertNotIn("conversation-continuity complete", result["reason"])


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
