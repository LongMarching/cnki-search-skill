"""Claude Code hooks for the cnki-search skill.

The old CNKI bundle used browser/session lifecycle hooks. This public
cnki-search package is HTTP/workspace based, so this hook only adds safe local
defaults for cnki-search CLI calls and lightweight Claude context.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any


CNKI_RUN_RE = re.compile(
    r"(?i)(?:^|[\s;&|])(?:python(?:\.exe)?|py)(?:\s+-\d(?:\.\d+)?)?\s+"
    r"(?:\.claude[\\/](?:skills[\\/])?cnki-search|"
    r"\.claude[\\/]skills[\\/]cnki-search|"
    r"skills[\\/]cnki-search|"
    r"skill[\\/]cnki-search)"
    r"[\\/]run\.py\b"
)

DEFAULTS = {
    "PYTHONIOENCODING": "utf-8",
    "CNKI_WORKSPACE_DIR": ".claude/cnki-workspaces",
}


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_payload(payload: dict[str, Any]) -> int:
    if payload:
        print(json.dumps(payload, ensure_ascii=False))
    return 0


def get_command(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    command = tool_input.get("command")
    return command if isinstance(command, str) else ""


def looks_like_powershell(command: str) -> bool:
    lowered = command.lower()
    return "$env:" in lowered or "powershell" in lowered or "pwsh" in lowered


def prefix_bash(command: str) -> str:
    assignments = " ".join(f'{key}="${{{key}:-{value}}}"' for key, value in DEFAULTS.items())
    return f"{assignments} {command}"


def prefix_powershell(command: str) -> str:
    parts = [
        "$env:PYTHONIOENCODING = if ($env:PYTHONIOENCODING) { $env:PYTHONIOENCODING } else { 'utf-8' }",
        "$env:CNKI_WORKSPACE_DIR = if ($env:CNKI_WORKSPACE_DIR) { $env:CNKI_WORKSPACE_DIR } else { '.claude/cnki-workspaces' }",
        command,
    ]
    return "; ".join(parts)


def pre_tool_use(payload: dict[str, Any]) -> dict[str, Any]:
    command = get_command(payload)
    if not command or not CNKI_RUN_RE.search(command):
        return {}

    rewritten = prefix_powershell(command) if looks_like_powershell(command) else prefix_bash(command)
    if rewritten == command:
        return {}

    tool_input = dict(payload.get("tool_input") or {})
    tool_input["command"] = rewritten
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": tool_input,
        }
    }


def context_message(event_name: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": (
                "cnki-search is installed at .claude/skills/cnki-search. "
                "Use python .claude/skills/cnki-search/run.py for CLI calls. "
                "Workspace state defaults to .claude/cnki-workspaces and downloads default to cnki-search-download/PDF or CAJ."
            ),
        }
    }


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    action = args[0] if args else "pre-tool-use"
    payload = read_payload()

    if action == "pre-tool-use":
        return write_payload(pre_tool_use(payload))
    if action == "session-start":
        return write_payload(context_message("SessionStart"))
    if action == "subagent-start":
        return write_payload(context_message("SubagentStart"))
    return write_payload({})


if __name__ == "__main__":
    raise SystemExit(main())
