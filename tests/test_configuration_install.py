from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
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


installer = load_module(
    "install_conversation_continuity",
    REPO_ROOT / "configuration/install_conversation_continuity.py",
)


class ConversationContinuityInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "home"
        self.codex_home = self.home / ".codex"
        self.source = REPO_ROOT / "configuration/conversation_continuity.py"
        (self.home / ".claude").mkdir(parents=True)
        self.codex_home.mkdir(parents=True)
        self.skill_root = self.home / ".agents" / "skills" / "save-conversation"
        (self.skill_root / "scripts").mkdir(parents=True)
        (self.skill_root / "SKILL.md").write_text(
            "Publication protocol: `publisher-v1`.\n",
            encoding="utf-8",
        )
        (self.skill_root / "scripts" / "publish_conversation.py").write_text(
            'PUBLISHER_PROTOCOL = "publisher-v1"\n',
            encoding="utf-8",
        )

    def write_json(self, path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def test_install_preserves_existing_settings_and_hooks(self) -> None:
        claude_path = self.home / ".claude" / "settings.json"
        codex_path = self.codex_home / "hooks.json"
        worktree_group = {
            "hooks": [
                {
                    "type": "command",
                    "command": "/existing/worktree-create",
                }
            ]
        }
        self.write_json(
            claude_path,
            {
                "enabledPlugins": {"example": True},
                "hooks": {"WorktreeCreate": [worktree_group]},
            },
        )
        self.write_json(
            codex_path,
            {
                "description": "Existing hooks",
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "/existing/pre-tool"}],
                        }
                    ]
                },
            },
        )

        installer.install(self.source, self.home, self.codex_home)

        claude = json.loads(claude_path.read_text(encoding="utf-8"))
        codex = json.loads(codex_path.read_text(encoding="utf-8"))
        executable = self.home / ".agents" / "bin" / "conversation-continuity"
        self.assertEqual(claude["enabledPlugins"], {"example": True})
        self.assertEqual(claude["hooks"]["WorktreeCreate"], [worktree_group])
        self.assertEqual(codex["description"], "Existing hooks")
        self.assertEqual(codex["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "/existing/pre-tool")
        self.assertIn("SessionStart", claude["hooks"])
        self.assertIn("Stop", codex["hooks"])
        for groups in codex["hooks"].values():
            for group in groups:
                for handler in group["hooks"]:
                    if handler["command"].endswith("hook --client codex"):
                        self.assertNotIn("statusMessage", handler)
        self.assertEqual(claude["statusLine"]["command"], f"{executable} statusline")
        self.assertTrue(executable.stat().st_mode & 0o100)

    def test_repository_protocol_declarations_match(self) -> None:
        publisher = REPO_ROOT / "skills/save-conversation/scripts/publish_conversation.py"

        self.assertEqual(
            installer.SAVE_PROTOCOL,
            installer.read_protocol_assignment(self.source, "SAVE_PROTOCOL", "continuity controller"),
        )
        self.assertEqual(
            installer.SAVE_PROTOCOL,
            installer.read_protocol_assignment(publisher, "PUBLISHER_PROTOCOL", "conversation publisher"),
        )

    def test_reinstall_is_idempotent(self) -> None:
        installer.install(self.source, self.home, self.codex_home)
        first_claude = json.loads((self.home / ".claude/settings.json").read_text(encoding="utf-8"))
        first_codex = json.loads((self.codex_home / "hooks.json").read_text(encoding="utf-8"))

        installer.install(self.source, self.home, self.codex_home)

        second_claude = json.loads((self.home / ".claude/settings.json").read_text(encoding="utf-8"))
        second_codex = json.loads((self.codex_home / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(second_claude, first_claude)
        self.assertEqual(second_codex, first_codex)
        self.assertEqual(len(second_claude["hooks"]["Stop"]), 1)
        self.assertEqual(len(second_codex["hooks"]["SessionStart"]), 1)

    def test_existing_statusline_is_preserved(self) -> None:
        claude_path = self.home / ".claude" / "settings.json"
        existing = {"type": "command", "command": "/user/statusline"}
        self.write_json(claude_path, {"statusLine": existing})

        messages = installer.install(self.source, self.home, self.codex_home)

        claude = json.loads(claude_path.read_text(encoding="utf-8"))
        self.assertEqual(claude["statusLine"], existing)
        self.assertTrue(any("Preserved existing Claude status line" in message for message in messages))

    def test_malformed_configuration_fails_before_writing(self) -> None:
        codex_path = self.codex_home / "hooks.json"
        codex_path.write_text("{bad json", encoding="utf-8")

        with self.assertRaises(json.JSONDecodeError):
            installer.install(self.source, self.home, self.codex_home)

        self.assertFalse((self.home / ".agents/bin/conversation-continuity").exists())
        self.assertFalse((self.home / ".claude/settings.json").exists())

    def test_dangling_configuration_symlink_is_preserved(self) -> None:
        claude_path = self.home / ".claude" / "settings.json"
        target = self.home / "missing-settings.json"
        claude_path.symlink_to(target)

        with self.assertRaisesRegex(ValueError, "regular file"):
            installer.install(self.source, self.home, self.codex_home)

        self.assertTrue(claude_path.is_symlink())
        self.assertEqual(claude_path.readlink(), target)
        self.assertFalse(target.exists())
        self.assertFalse((self.home / ".agents/bin/conversation-continuity").exists())

    def test_write_failure_restores_every_installation_target(self) -> None:
        claude_path = self.home / ".claude" / "settings.json"
        codex_path = self.codex_home / "hooks.json"
        claude_bytes = b'{\n  "enabledPlugins": {"example": true}\n}\n'
        codex_bytes = b'{\n  "description": "existing"\n}\n'
        claude_path.write_bytes(claude_bytes)
        codex_path.write_bytes(codex_bytes)
        real_atomic_write = installer.atomic_write
        calls = 0

        def fail_fourth_write(path, content, mode):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise OSError("injected Codex write failure")
            return real_atomic_write(path, content, mode)

        with mock.patch.object(installer, "atomic_write", side_effect=fail_fourth_write):
            with self.assertRaisesRegex(OSError, "injected Codex write failure"):
                installer.install(self.source, self.home, self.codex_home)

        self.assertEqual(claude_path.read_bytes(), claude_bytes)
        self.assertEqual(codex_path.read_bytes(), codex_bytes)
        self.assertFalse((self.home / ".agents/bin/conversation-continuity").exists())
        self.assertFalse((self.home / ".agents/bin/conversation-continuity.sha256").exists())

    def test_modified_installed_executable_is_not_overwritten(self) -> None:
        installer.install(self.source, self.home, self.codex_home)
        executable = self.home / ".agents" / "bin" / "conversation-continuity"
        executable.write_text("# modified\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "refusing to replace modified"):
            installer.install(self.source, self.home, self.codex_home)

    def test_missing_save_skill_is_rejected_before_writing(self) -> None:
        (self.skill_root / "SKILL.md").unlink()

        with self.assertRaisesRegex(ValueError, "save-conversation skill is missing"):
            installer.install(self.source, self.home, self.codex_home)

        self.assertFalse((self.home / ".agents/bin/conversation-continuity").exists())
        self.assertFalse((self.home / ".claude/settings.json").exists())
        self.assertFalse((self.codex_home / "hooks.json").exists())

    def test_mismatched_publisher_is_rejected_before_writing(self) -> None:
        installer.install(self.source, self.home, self.codex_home)
        targets = (
            self.home / ".agents/bin/conversation-continuity",
            self.home / ".agents/bin/conversation-continuity.sha256",
            self.home / ".claude/settings.json",
            self.codex_home / "hooks.json",
        )
        installed = {path: path.read_bytes() for path in targets}
        publisher = self.skill_root / "scripts" / "publish_conversation.py"
        publisher.write_text('PUBLISHER_PROTOCOL = "publisher-v2"\n', encoding="utf-8")

        with self.assertRaisesRegex(
            ValueError,
            "conversation publisher protocol is incompatible: expected publisher-v1, found publisher-v2",
        ):
            installer.install(self.source, self.home, self.codex_home)

        self.assertEqual(installed, {path: path.read_bytes() for path in targets})

    def test_unadvertised_publisher_is_rejected_before_writing(self) -> None:
        publisher = self.skill_root / "scripts" / "publish_conversation.py"
        publisher.write_text("REQUEST_VERSION = 1\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "does not declare one literal PUBLISHER_PROTOCOL"):
            installer.install(self.source, self.home, self.codex_home)

        self.assertFalse((self.home / ".agents/bin/conversation-continuity").exists())
        self.assertFalse((self.home / ".claude/settings.json").exists())
        self.assertFalse((self.codex_home / "hooks.json").exists())

    def test_missing_publisher_is_rejected_before_writing(self) -> None:
        (self.skill_root / "scripts" / "publish_conversation.py").unlink()

        with self.assertRaisesRegex(ValueError, "conversation publisher is missing"):
            installer.install(self.source, self.home, self.codex_home)

        self.assertFalse((self.home / ".agents/bin/conversation-continuity").exists())
        self.assertFalse((self.home / ".claude/settings.json").exists())
        self.assertFalse((self.codex_home / "hooks.json").exists())

    def test_reinstall_upgrades_a_managed_controller(self) -> None:
        executable = self.home / ".agents" / "bin" / "conversation-continuity"
        digest = executable.with_suffix(".sha256")
        executable.parent.mkdir(parents=True)
        old_content = b"#!/usr/bin/env python3\n# old managed controller\n"
        executable.write_bytes(old_content)
        digest.write_text(f"{installer.file_digest(executable)}\n", encoding="utf-8")

        installer.install(self.source, self.home, self.codex_home)

        self.assertEqual(self.source.read_bytes(), executable.read_bytes())
        claude = json.loads((self.home / ".claude/settings.json").read_text(encoding="utf-8"))
        codex = json.loads((self.codex_home / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(1, len(claude["hooks"]["Stop"]))
        self.assertEqual(1, len(codex["hooks"]["Stop"]))

    def test_base_configuration_install_does_not_add_hooks_without_flag(self) -> None:
        (self.skill_root / "SKILL.md").unlink()
        environment = {
            **os.environ,
            "HOME": str(self.home),
            "CODEX_HOME": str(self.codex_home),
        }

        subprocess.run(
            [str(REPO_ROOT / "configuration/install")],
            check=True,
            text=True,
            capture_output=True,
            env=environment,
        )

        self.assertFalse((self.codex_home / "hooks.json").exists())
        self.assertFalse((self.home / ".agents/bin/conversation-continuity").exists())

    def test_configuration_flag_installs_hooks(self) -> None:
        environment = {
            **os.environ,
            "HOME": str(self.home),
            "CODEX_HOME": str(self.codex_home),
        }

        subprocess.run(
            [str(REPO_ROOT / "configuration/install"), "--conversation-continuity"],
            check=True,
            text=True,
            capture_output=True,
            env=environment,
        )

        self.assertTrue((self.codex_home / "hooks.json").is_file())
        self.assertTrue((self.home / ".agents/bin/conversation-continuity").is_file())


if __name__ == "__main__":
    unittest.main()
