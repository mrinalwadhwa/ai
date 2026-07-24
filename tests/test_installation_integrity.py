from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONFIGURATION = REPOSITORY_ROOT / "configuration"
MANAGER_PATH = CONFIGURATION / "manage_installation.py"
MANAGED_SKILLS = ("save-conversation", "resume-conversation")


def load_manager():
    spec = importlib.util.spec_from_file_location(
        "installation_integrity_manager", MANAGER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {MANAGER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


manager = load_manager()


class InstallationIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "installation root with spaces"
        self.root.mkdir()
        self.home = self.root / "home"
        self.codex_home = self.home / ".codex"
        self.environment = os.environ.copy()
        self.environment.pop("XDG_STATE_HOME", None)
        self.environment.update(
            {
                "HOME": str(self.home),
                "CODEX_HOME": str(self.codex_home),
            }
        )

    def run_command(
        self,
        *command: str | Path,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(part) for part in command],
            env=environment or self.environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def write_lock(self, skills: dict[str, object]) -> None:
        lock = self.home / ".agents" / ".skill-lock.json"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(
            json.dumps({"version": 3, "skills": skills}, indent=2) + "\n",
            encoding="utf-8",
        )

    def read_lock(self) -> dict[str, object]:
        lock = self.home / ".agents" / ".skill-lock.json"
        return json.loads(lock.read_text(encoding="utf-8"))["skills"]

    def seed_managed_skills(self) -> None:
        installed_root = self.home / ".agents" / "skills"
        claude_root = self.home / ".claude" / "skills"
        installed_root.mkdir(parents=True, exist_ok=True)
        claude_root.mkdir(parents=True, exist_ok=True)
        lock: dict[str, object] = {}
        for name in MANAGED_SKILLS:
            installed = installed_root / name
            shutil.copytree(REPOSITORY_ROOT / "skills" / name, installed, symlinks=True)
            (claude_root / name).symlink_to(
                os.path.relpath(installed, claude_root),
            )
            lock[name] = {
                "source": "mrinalwadhwa/ai",
                "sourceType": "github",
                "ref": "main",
                "skillPath": f"skills/{name}/SKILL.md",
            }
        self.write_lock(lock)

    def install_configuration(self) -> None:
        result = self.run_command(
            CONFIGURATION / "install", "--conversation-continuity"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def seed_healthy_installation(self) -> None:
        self.seed_managed_skills()
        self.install_configuration()

    def seed_retired_handoff(self, source: str) -> None:
        installed = self.home / ".agents" / "skills" / "handoff"
        installed.mkdir(parents=True, exist_ok=True)
        (installed / "SKILL.md").write_text(
            "---\nname: handoff\ndescription: Retired test skill.\n---\n",
            encoding="utf-8",
        )
        claude_root = self.home / ".claude" / "skills"
        claude_root.mkdir(parents=True, exist_ok=True)
        (claude_root / "handoff").symlink_to(os.path.relpath(installed, claude_root))
        skills = (
            self.read_lock()
            if (self.home / ".agents/.skill-lock.json").exists()
            else {}
        )
        skills["handoff"] = {
            "source": source,
            "sourceType": "github",
            "skillPath": "skills/handoff/SKILL.md",
        }
        self.write_lock(skills)

    def home_snapshot(self) -> dict[str, tuple[str, int, bytes | str]]:
        snapshot: dict[str, tuple[str, int, bytes | str]] = {}
        if not self.home.exists():
            return snapshot
        pending = [self.home]
        while pending:
            directory = pending.pop()
            for entry in os.scandir(directory):
                path = Path(entry.path)
                relative = path.relative_to(self.home).as_posix()
                metadata = path.lstat()
                mode = metadata.st_mode & 0o777
                if entry.is_symlink():
                    snapshot[relative] = ("symlink", mode, os.readlink(path))
                elif entry.is_dir(follow_symlinks=False):
                    snapshot[relative] = ("directory", mode, b"")
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    snapshot[relative] = ("file", mode, path.read_bytes())
                else:
                    snapshot[relative] = ("other", mode, b"")
        return snapshot

    def make_manifest(
        self, value: dict[str, object], skill_name: str = "example"
    ) -> Path:
        repository = self.root / "manifest-repository"
        configuration = repository / "configuration"
        skill = repository / "skills" / skill_name
        configuration.mkdir(parents=True, exist_ok=True)
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: Test skill.\n---\n",
            encoding="utf-8",
        )
        manifest = configuration / "skills.json"
        manifest.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return manifest

    def make_fake_skills_executable(self) -> tuple[Path, Path]:
        executable = self.root / "fake-skills"
        log = self.root / "skills.log"
        executable.write_text(
            f"""#!{sys.executable}
import json
import os
import shutil
import sys
from pathlib import Path

home = Path(os.environ["HOME"])
codex_home = Path(os.environ["CODEX_HOME"])
log = Path(os.environ["AI_SKILLS_LOG"])
source_root = Path({str(REPOSITORY_ROOT)!r})
arguments = sys.argv[1:]
with log.open("a", encoding="utf-8") as stream:
    stream.write(" ".join(arguments) + "\\n")

lock_path = home / ".agents" / ".skill-lock.json"
if lock_path.exists():
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
else:
    lock = {{"version": 3, "skills": {{}}}}

def remove_path(path):
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)

if arguments[:2] == ["skills", "add"]:
    source, revision = arguments[2].split("#", 1)
    omitted = os.environ.get("AI_FAKE_OMIT_SKILL")
    names = [
        arguments[index + 1]
        for index, argument in enumerate(arguments)
        if argument == "--skill"
    ]
    for name in names:
        if name == omitted:
            continue
        source_path = source_root / "skills" / name
        installed = home / ".agents" / "skills" / name
        installed.parent.mkdir(parents=True, exist_ok=True)
        remove_path(installed)
        shutil.copytree(source_path, installed, symlinks=True)
        claude = home / ".claude" / "skills" / name
        claude.parent.mkdir(parents=True, exist_ok=True)
        remove_path(claude)
        claude.symlink_to(os.path.relpath(installed, claude.parent))
        lock["skills"][name] = {{
            "source": source,
            "sourceType": "github",
            "ref": revision,
            "skillPath": f"skills/{{name}}/SKILL.md",
        }}
elif arguments[:2] == ["skills", "remove"]:
    name = arguments[2]
    if not all(
        (home / ".agents" / "skills" / managed).is_dir()
        for managed in ("save-conversation", "resume-conversation")
    ):
        raise SystemExit("retirement ran before managed skill installation")
    if (home / ".agents" / "AGENTS.md").exists():
        raise SystemExit("configuration installation ran before skill retirement")
    for path in (
        home / ".agents" / "skills" / name,
        home / ".claude" / "skills" / name,
        codex_home / "skills" / name,
    ):
        remove_path(path)
    lock["skills"].pop(name, None)
else:
    raise SystemExit(f"unexpected invocation: {{arguments!r}}")

lock_path.parent.mkdir(parents=True, exist_ok=True)
lock_path.write_text(json.dumps(lock, indent=2) + "\\n", encoding="utf-8")
""",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        return executable, log

    def test_manifest_validation_accepts_valid_data_and_rejects_unsafe_paths(
        self,
    ) -> None:
        value = {
            "schema_version": 1,
            "source": "example/skills",
            "ref": "main",
            "skills": [{"name": "example", "path": "skills/example"}],
            "retired": [
                {
                    "name": "old-example",
                    "path": "skills/old-example",
                    "replacement": "example",
                    "owned_sources": ["example/old-skills"],
                }
            ],
        }
        manifest_path = self.make_manifest(value)

        manifest = manager.load_skill_manifest(manifest_path)

        self.assertEqual(manifest.source, "example/skills")
        self.assertEqual([skill.name for skill in manifest.skills], ["example"])
        value["skills"][0]["path"] = "../outside"
        manifest_path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(manager.InstallationError, "safe relative path"):
            manager.load_skill_manifest(manifest_path)

    def test_healthy_doctor_matches_real_installer_output(self) -> None:
        self.seed_healthy_installation()

        result = self.run_command(CONFIGURATION / "doctor", "--conversation-continuity")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "OK: The installed skills and configuration match their sources.",
            result.stdout,
        )
        self.assertIn("A running Claude session can retain a skill", result.stdout)

    def test_doctor_reports_drift_and_retired_skill_without_writing(self) -> None:
        self.seed_healthy_installation()
        skill_file = self.home / ".agents" / "skills" / "save-conversation" / "SKILL.md"
        skill_file.write_text(
            skill_file.read_text(encoding="utf-8") + "\nDrift.\n", encoding="utf-8"
        )
        self.seed_retired_handoff("mrinalwadhwa/skills")
        before = self.home_snapshot()

        result = self.run_command(CONFIGURATION / "doctor", "--conversation-continuity")

        self.assertEqual(result.returncode, 1)
        self.assertIn("installed skill differs from its source", result.stdout)
        self.assertIn(
            "retired skill remains in the skills lock: handoff", result.stdout
        )
        self.assertIn("retired skill remains discoverable", result.stdout)
        self.assertEqual(self.home_snapshot(), before)

    def test_update_installs_skills_then_retires_then_installs_configuration(
        self,
    ) -> None:
        self.seed_retired_handoff("mrinalwadhwa/skills")
        executable, log = self.make_fake_skills_executable()
        environment = {
            **self.environment,
            "AI_SKILLS_EXECUTABLE": str(executable),
            "AI_SKILLS_LOG": str(log),
        }

        result = self.run_command(
            CONFIGURATION / "update",
            "--conversation-continuity",
            environment=environment,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        invocations = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            invocations,
            [
                "skills add mrinalwadhwa/ai#main --skill save-conversation "
                "--skill resume-conversation --agent claude-code codex --global --yes",
                "skills remove handoff --agent claude-code codex --global --yes",
            ],
        )
        self.assertNotIn("handoff", self.read_lock())
        self.assertFalse((self.home / ".agents/skills/handoff").exists())
        self.assertTrue((self.home / ".agents/AGENTS.md").is_file())
        self.assertIn(
            "OK: The installed skills and configuration match their sources.",
            result.stdout,
        )

    def test_update_refuses_unowned_retired_skill_before_any_write(self) -> None:
        self.seed_retired_handoff("someone-else/personal-skills")
        executable, log = self.make_fake_skills_executable()
        before = self.home_snapshot()
        environment = {
            **self.environment,
            "AI_SKILLS_EXECUTABLE": str(executable),
            "AI_SKILLS_LOG": str(log),
        }

        result = self.run_command(CONFIGURATION / "update", environment=environment)

        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to remove retired skill handoff", result.stderr)
        self.assertIn("lock provenance is not owned", result.stderr)
        self.assertFalse(log.exists())
        self.assertEqual(self.home_snapshot(), before)

    def test_update_refuses_unowned_active_skill_before_any_write(self) -> None:
        installed = self.home / ".agents" / "skills" / "save-conversation"
        installed.mkdir(parents=True)
        (installed / "SKILL.md").write_text(
            "---\nname: save-conversation\ndescription: Unmanaged collision.\n---\n",
            encoding="utf-8",
        )
        executable, log = self.make_fake_skills_executable()
        before = self.home_snapshot()
        environment = {
            **self.environment,
            "AI_SKILLS_EXECUTABLE": str(executable),
            "AI_SKILLS_LOG": str(log),
        }

        result = self.run_command(CONFIGURATION / "update", environment=environment)

        self.assertEqual(result.returncode, 1)
        self.assertIn("without lock provenance", result.stderr)
        self.assertFalse(log.exists())
        self.assertEqual(self.home_snapshot(), before)

    def test_partial_skill_install_stops_before_retirement_and_configuration(
        self,
    ) -> None:
        self.seed_retired_handoff("mrinalwadhwa/skills")
        executable, log = self.make_fake_skills_executable()
        environment = {
            **self.environment,
            "AI_SKILLS_EXECUTABLE": str(executable),
            "AI_SKILLS_LOG": str(log),
            "AI_FAKE_OMIT_SKILL": "resume-conversation",
        }

        result = self.run_command(
            CONFIGURATION / "update",
            "--conversation-continuity",
            environment=environment,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "skill installation is incomplete for resume-conversation", result.stderr
        )
        self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 1)
        self.assertTrue((self.home / ".agents/skills/handoff").is_dir())
        self.assertIn("handoff", self.read_lock())
        self.assertFalse((self.home / ".agents/AGENTS.md").exists())

    def test_bad_second_manifest_stops_before_public_skill_installation(self) -> None:
        manifest_value = {
            "schema_version": 1,
            "source": "example/private-skills",
            "ref": "main",
            "skills": [{"name": "example", "path": "skills/example"}],
            "retired": [],
        }
        manifest = self.make_manifest(manifest_value)
        repository = manifest.parent.parent
        subprocess.run(
            ["git", "init", "-b", "main", repository], check=True, capture_output=True
        )
        subprocess.run(["git", "-C", repository, "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                repository,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-m",
                "Add fixture",
            ],
            check=True,
            capture_output=True,
        )
        skill_file = repository / "skills/example/SKILL.md"
        skill_file.write_text(
            skill_file.read_text(encoding="utf-8") + "\nDirty.\n", encoding="utf-8"
        )
        executable, log = self.make_fake_skills_executable()
        environment = {
            **self.environment,
            "AI_SKILLS_EXECUTABLE": str(executable),
            "AI_SKILLS_LOG": str(log),
        }

        result = self.run_command(
            CONFIGURATION / "update",
            "--skill-manifest",
            manifest,
            environment=environment,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("uncommitted changes in example/private-skills", result.stderr)
        self.assertFalse(log.exists())
        self.assertFalse((self.home / ".agents/skills/save-conversation").exists())

    def test_retirement_requires_exact_lock_provenance(self) -> None:
        self.seed_retired_handoff("mrinalwadhwa/skills")
        executable, log = self.make_fake_skills_executable()
        environment = {
            **self.environment,
            "AI_SKILLS_EXECUTABLE": str(executable),
            "AI_SKILLS_LOG": str(log),
        }
        skills = self.read_lock()
        skills["handoff"]["sourceType"] = "local"
        self.write_lock(skills)

        wrong_type = self.run_command(CONFIGURATION / "update", environment=environment)

        self.assertEqual(wrong_type.returncode, 1)
        self.assertIn("lock provenance is not owned", wrong_type.stderr)
        self.assertFalse(log.exists())

        skills = self.read_lock()
        skills["handoff"]["sourceType"] = "github"
        skills["handoff"]["skillPath"] = "another/handoff/SKILL.md"
        self.write_lock(skills)

        wrong_path = self.run_command(CONFIGURATION / "update", environment=environment)

        self.assertEqual(wrong_path.returncode, 1)
        self.assertIn("lock provenance is not owned", wrong_path.stderr)
        self.assertFalse(log.exists())
        self.assertTrue((self.home / ".agents/skills/handoff").is_dir())

    def test_doctor_honors_xdg_state_home_for_the_skills_lock(self) -> None:
        self.seed_healthy_installation()
        xdg_state = self.root / "state"
        xdg_lock = xdg_state / "skills" / ".skill-lock.json"
        xdg_lock.parent.mkdir(parents=True)
        shutil.move(self.home / ".agents/.skill-lock.json", xdg_lock)
        environment = {
            **self.environment,
            "XDG_STATE_HOME": str(xdg_state),
        }

        result = self.run_command(
            CONFIGURATION / "doctor",
            "--conversation-continuity",
            environment=environment,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "OK: The installed skills and configuration match their sources.",
            result.stdout,
        )


if __name__ == "__main__":
    unittest.main()
