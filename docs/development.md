# Development

## Structure

```text
skill/cnki-search/run.py            # CLI entry point
skill/cnki-search/scripts/cli.py    # argparse command wiring
skill/cnki-search/src/actions/      # workflow orchestration
skill/cnki-search/src/adapters/     # CNKI HTTP/interface adapters
skill/cnki-search/src/core/         # state store, HTTP helpers, fieldsets
skill/cnki-search/resources/        # packaged discipline map
tests/                              # offline tests plus live harness
install.py                          # clone-repo installer for Claude Code projects
```

## Verification

Run before publishing changes:

```bash
python -m unittest discover -s tests -v
python -m py_compile install.py skill/cnki-search/run.py skill/cnki-search/scripts/cli.py skill/cnki-search/src/actions/_workflow_impl.py tools/build_claude_bundle.py tools/install_claude_bundle.py
python tools/build_claude_bundle.py
git diff --check
```

Live CNKI checks are separate and require `CNKI_LIVE_TEST=1`.

## Publishing Boundary

Keep the repository focused on the distributable skill:

- keep `skill/cnki-search/`, `agent/`, `tools/`, `tests/`, and `docs/`
- exclude local cookies, workspaces, downloaded papers, generated bundles, and
  personal-only agent integrations
- report CNKI guarded states instead of bypassing access controls
