import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_PY = REPO_ROOT / "install.py"
spec = importlib.util.spec_from_file_location("cnki_search_installer", INSTALL_PY)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_install_copies_bundle_assets_and_merges_hooks_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            settings = target / ".claude" / "settings.local.json"
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "Bash",
                                    "hooks": [{"type": "command", "command": "echo existing"}],
                                }
                            ]
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            legacy_hook = target / ".claude" / "hooks" / "cnki_session_hook.py"
            legacy_hook.parent.mkdir(parents=True, exist_ok=True)
            legacy_hook.write_text("# stale\n", encoding="utf-8")

            first = installer.install(target)
            installer.install(target)

            self.assertTrue((target / ".claude" / "skills" / "cnki-search" / "run.py").exists())
            self.assertTrue((target / ".claude" / "agents" / "cnki-paper-retriever.md").exists())
            self.assertTrue((target / ".claude" / "hooks" / "cnki_search_hook.py").exists())
            self.assertFalse(legacy_hook.exists())
            self.assertTrue(first["settings_backup"])

            merged = json.loads(settings.read_text(encoding="utf-8"))
            self.assertIn("SessionStart", merged["hooks"])
            self.assertIn("SubagentStart", merged["hooks"])
            self.assertIn("PreToolUse", merged["hooks"])
            commands = [
                hook["command"]
                for entry in merged["hooks"]["PreToolUse"]
                for hook in entry.get("hooks", [])
            ]
            self.assertEqual(commands.count("python .claude/hooks/cnki_search_hook.py pre-tool-use"), 1)
            self.assertIn("echo existing", commands)

    def test_install_refuses_to_install_into_clone_root(self):
        with self.assertRaises(ValueError):
            installer.install(REPO_ROOT)

    def test_default_target_inside_clone_is_clone_parent(self):
        inferred = installer.infer_target_root(source_root=REPO_ROOT, cwd=REPO_ROOT, explicit_target=None)

        self.assertEqual(inferred, REPO_ROOT.parent.resolve())

    def test_default_target_for_clone_inside_project_claude_dir_is_project_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            source = project / ".claude" / "cnki-search-skill"
            cwd = source / "docs"

            inferred = installer.infer_target_root(source_root=source, cwd=cwd, explicit_target=None)

            self.assertEqual(inferred, project.resolve())

    def test_default_target_for_clone_inside_project_claude_dir_ignores_external_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            source = project / ".claude" / "cnki-search-skill"
            external_cwd = Path(tmp) / "elsewhere"

            inferred = installer.infer_target_root(source_root=source, cwd=external_cwd, explicit_target=None)

            self.assertEqual(inferred, project.resolve())

    def test_default_target_for_repo_root_as_project_claude_dir_is_project_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            source = project / ".claude"

            inferred = installer.infer_target_root(source_root=source, cwd=source, explicit_target=None)

            self.assertEqual(inferred, project.resolve())

    def test_default_target_outside_clone_is_current_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)

            inferred = installer.infer_target_root(source_root=REPO_ROOT, cwd=cwd, explicit_target=None)

            self.assertEqual(inferred, cwd.resolve())

    def test_explicit_target_is_used_as_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)

            inferred = installer.infer_target_root(source_root=REPO_ROOT, cwd=REPO_ROOT, explicit_target=str(target))

            self.assertEqual(inferred, target.resolve())


if __name__ == "__main__":
    unittest.main()
