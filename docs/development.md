# Development

## Structure

```text
.claude/skills/cnki-search/run.py            # CLI entry point
.claude/skills/cnki-search/scripts/cli.py    # argparse command wiring
.claude/skills/cnki-search/src/actions/      # workflow orchestration
.claude/skills/cnki-search/src/adapters/     # CNKI HTTP/interface adapters
.claude/skills/cnki-search/src/core/         # state store, HTTP helpers, fieldsets
.claude/skills/cnki-search/resources/        # packaged discipline map
.claude/agents/cnki-paper-retriever.md       # Claude Code agent template
.claude/hooks/cnki_search_hook.py            # Claude Code hook helper
.claude/settings.cnki-snippet.json           # settings snippet merged by installers
tests/                                       # offline tests plus live harness
install.py                                   # clone-repo installer for Claude Code projects
```

## Verification

Run before publishing changes:

```bash
python -m unittest discover -s tests -v
python -m py_compile install.py .claude/skills/cnki-search/run.py .claude/skills/cnki-search/scripts/cli.py .claude/skills/cnki-search/src/actions/_workflow_impl.py tools/build_claude_bundle.py tools/install_claude_bundle.py
python tools/build_claude_bundle.py
git diff --check
```

Live CNKI checks are separate and require `CNKI_LIVE_TEST=1`.

## Publishing Boundary

Keep the repository focused on the distributable Claude Code bundle:

- keep `.claude/skills/cnki-search/`, `.claude/agents/`, `.claude/hooks/`,
  `.claude/settings.cnki-snippet.json`, `tools/`, `tests/`, and `docs/`
- exclude local cookies, workspaces, downloaded papers, generated bundles, and
  personal-only agent integrations
- report CNKI guarded states instead of bypassing access controls
