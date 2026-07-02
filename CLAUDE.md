# CLAUDE.md

This repository publishes the `cnki-search` Claude Code skill.

## Active Surface

The distributable skill is [.claude/skills/cnki-search](.claude/skills/cnki-search). The CLI
entry point is:

```bash
python .claude/skills/cnki-search/run.py --help
```

The top-level `install.py` must install into an external Claude Code project,
not back into this clone. The release source is a Claude Code bundle tree:
`.claude/agents/`, `.claude/hooks/`, `.claude/skills/`, and
`.claude/settings.cnki-snippet.json`.

Commands return structured JSON. Treat `status`, `workspace_id`, `run_id`,
`summary`, `rows`, `warnings`, and row-level errors as the contract.

## Guarded States

Do not bypass CNKI access controls. If the skill returns `captcha`,
`login_required`, `permission_denied`, `source_app_invalid`, `empty_body`, or
`format_mismatch`, preserve the workspace/run IDs and report the state.

## Verification

```bash
python -m unittest discover -s tests -v
python -m py_compile install.py .claude/hooks/cnki_search_hook.py .claude/skills/cnki-search/run.py .claude/skills/cnki-search/scripts/cli.py .claude/skills/cnki-search/src/actions/_workflow_impl.py tools/build_claude_bundle.py tools/install_claude_bundle.py
python tools/build_claude_bundle.py
git diff --check
```
