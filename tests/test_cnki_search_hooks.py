import json
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_PY = REPO_ROOT / ".claude" / "hooks" / "cnki_search_hook.py"


class CnkiSearchHookTests(unittest.TestCase):
    def run_hook(self, action: str, payload: dict) -> dict:
        proc = subprocess.run(
            [sys.executable, str(HOOK_PY), action],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout) if proc.stdout.strip() else {}

    def test_pre_tool_use_adds_cnki_defaults_to_cli_command(self):
        payload = {
            "tool_name": "Bash",
            "tool_input": {
                "command": 'python .claude/skills/cnki-search/run.py search "机器学习" --page 1'
            },
        }

        result = self.run_hook("pre-tool-use", payload)

        output = result["hookSpecificOutput"]
        command = output["updatedInput"]["command"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertIn("PYTHONIOENCODING", command)
        self.assertIn("CNKI_WORKSPACE_DIR", command)
        self.assertIn("CNKI_DOWNLOAD_DIR", command)
        self.assertIn(".claude/skills/cnki-search/run.py", command)

    def test_pre_tool_use_ignores_unrelated_command(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "python -m unittest"}}

        result = self.run_hook("pre-tool-use", payload)

        self.assertEqual(result, {})

    def test_session_start_returns_cnki_context(self):
        result = self.run_hook("session-start", {})

        output = result["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "SessionStart")
        self.assertIn(".claude/skills/cnki-search", output["additionalContext"])


if __name__ == "__main__":
    unittest.main()
