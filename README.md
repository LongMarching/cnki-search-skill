# CNKI Search Skill

`cnki-search` is a Claude Code skill for CNKI literature retrieval. It provides
a structured JSON CLI for paper search, details, facets, citation export,
downloads, and workspace inspection.

The repository is designed to be cloned directly, then installed into any
Claude Code project with the top-level `install.py` script.

## Install Into Claude Code

Clone this repository:

```bash
git clone https://github.com/LongMarching/cnki-search-skill.git
```

Run the installer from the Claude Code project that should receive the skill:

```bash
cd /path/to/your/claude-project
python /path/to/cnki-search-skill/install.py
```

Or pass the target project explicitly:

```bash
python /path/to/cnki-search-skill/install.py --target /path/to/your/claude-project
```

Do not run `python install.py` from inside the cloned `cnki-search-skill`
directory. The installer refuses to install into the clone itself; the target
must be the Claude Code project where you want `.claude/skills/cnki-search/`
to be created.

The installer copies:

```text
.claude/skills/cnki-search/
.claude/agents/cnki-paper-retriever.md
```

It also merges this repository's Claude Code settings snippet into
`.claude/settings.local.json` and creates a timestamped backup when that file
already exists. Reopen the Claude Code project after installation so settings
reload.

After installation, Claude Code can invoke `Skill("cnki-search")`, and the CLI
is available inside the target project:

```bash
python .claude/skills/cnki-search/run.py search "机器学习" --page 1
```

## Repository Layout

```text
install.py                  # clone-repo installer for Claude Code projects
skill/cnki-search/          # complete skill source and CLI
agent/                      # Claude Code agent template
tools/                      # optional bundle builder and bundle installer
tests/                      # offline tests and guarded live harness
docs/                       # installation and development notes
templates/                  # Claude settings snippet used by install.py
```

## Direct Development Use

From the cloned repository root:

```bash
python skill/cnki-search/run.py search "机器学习" --page 1 --output-limit 5 --return-fields search_basic
```

PowerShell users should set UTF-8 output first:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
python skill/cnki-search/run.py search "机器学习" --page 1 --output-limit 5 --return-fields search_basic
```

Every command prints JSON. Use `status`, `workspace_id`, `run_id`, `summary`,
`rows`, `warnings`, and row-level status fields from that JSON.

## Main Commands

```bash
python skill/cnki-search/run.py search "机器学习" --pages 1-2 --sort citations
python skill/cnki-search/run.py fetch_details --workspace WORKSPACE --run RUN --top 10
python skill/cnki-search/run.py discover_facets --workspace WORKSPACE --run RUN --group subdiscipline
python skill/cnki-search/run.py export --workspace WORKSPACE --run RUN --rows 1-5 --mode GBTREFER BibTex
python skill/cnki-search/run.py download --workspace WORKSPACE --run RUN --rows 1-3 --format pdf
python skill/cnki-search/run.py inspect --workspace WORKSPACE --run RUN --view rows
```

The full command reference lives in [skill/cnki-search/SKILL.md](skill/cnki-search/SKILL.md).

## Optional Bundle Build

Most users should use `install.py` directly. A standalone Claude-ready bundle
can also be built:

```bash
python tools/build_claude_bundle.py
```

## Access Boundary

This project does not bypass CNKI access controls. It can use legitimate CNKI
access through IP login, `CNKI_COOKIE`, or `CNKI_COOKIE_FILE`, and it reports
guarded states such as `captcha`, `login_required`, `permission_denied`,
`source_app_invalid`, `empty_body`, and `format_mismatch` as structured JSON.

## Test

```bash
python -m unittest discover -s tests -v
python -m py_compile install.py skill/cnki-search/run.py skill/cnki-search/scripts/cli.py skill/cnki-search/src/actions/_workflow_impl.py
```

Live CNKI validation is opt-in:

```bash
CNKI_LIVE_TEST=1 python tests/live/cnki_search_live.py --help
```

## License

No open-source license has been added yet. Until the repository owner adds one,
all rights are reserved.
