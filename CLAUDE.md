# CLAUDE.md

This repository publishes the `cnki-search` Claude Code skill.

## Active Surface

The distributable skill is [skill/cnki-search](skill/cnki-search). The CLI
entry point is:

```bash
python skill/cnki-search/run.py --help
```

The top-level `install.py` must install into an external Claude Code project,
not back into this clone.

Commands return structured JSON. Treat `status`, `workspace_id`, `run_id`,
`summary`, `rows`, `warnings`, and row-level errors as the contract.

## Guarded States

Do not bypass CNKI access controls. If the skill returns `captcha`,
`login_required`, `permission_denied`, `source_app_invalid`, `empty_body`, or
`format_mismatch`, preserve the workspace/run IDs and report the state.

## Verification

```bash
python -m unittest discover -s tests -v
python -m py_compile install.py skill/cnki-search/run.py skill/cnki-search/scripts/cli.py skill/cnki-search/src/actions/_workflow_impl.py tools/build_claude_bundle.py tools/install_claude_bundle.py
python tools/build_claude_bundle.py
git diff --check
```
