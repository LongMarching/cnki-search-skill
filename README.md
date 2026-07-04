# CNKI Search Skill

[中文说明](README.zh-CN.md)

`cnki-search` is a Claude Code skill for CNKI literature retrieval. It provides
a structured JSON CLI for paper search, details, facets, citation export,
downloads, and workspace inspection.

The repository is designed to be cloned directly, then installed into any
Claude Code project with the top-level `install.py` script.

## Feature Overview

| Area | What it does | Main command or file |
| --- | --- | --- |
| Paper search | Search CNKI by keyword, advanced fields, professional expression, author, or sentence proximity. Supports pages, sorting, document type, discipline, quality, date, and form filters. | `search` |
| Workspace/run state | Stores each search as a reusable workspace/run so later actions can reuse the same result set without searching again. | `workspace_id`, `run_id` |
| Result inspection | Lists workspace summaries, runs, stored rows, selected pages, or selected row ranges. | `inspect` |
| Detail fetching | Fetches detail pages and stores abstracts, keywords, fund info, classifications, publication info, author/affiliation data, and discovered PDF/CAJ links. | `fetch_details` |
| Facet discovery | Replays a stored search and reads available result-page facet options, currently subdiscipline facets. | `discover_facets` |
| Citation export | Exports bibliography/citation text in formats such as `GBTREFER`, `MLA`, `APA`, `BibTex`, `EndNote`, `NoteExpress`, `Refworks`, `NodeFirst`, `REFER`, and `NEW`. | `export` |
| PDF/CAJ download | Downloads selected rows as PDF or CAJ. Default output is `<project-root>/cnki-search-download/PDF` or `<project-root>/cnki-search-download/CAJ`. | `download` |
| Claude Code install | Installs the skill, agent, hook helper, and settings snippet into a target Claude Code project. | `install.py` |
| Claude agent support | Provides a `cnki-paper-retriever` agent template for retrieval workflows. | `.claude/agents/cnki-paper-retriever.md` |
| Claude hooks | Adds session context and safe CLI defaults for Claude Code runs without forcing a download directory override. | `.claude/hooks/cnki_search_hook.py` |
| Access-state reporting | Reports guarded CNKI states as JSON, including captcha, login-required, permission-denied, source-app-invalid, empty-body, and format-mismatch. | JSON `status`, `error`, `warnings` |

## Install Into Claude Code

Clone this repository:

```bash
git clone https://github.com/LongMarching/cnki-search-skill.git
```

Clone it inside the Claude Code project that should receive the skill:

```bash
cd /path/to/your/claude-project
git clone https://github.com/LongMarching/cnki-search-skill.git
cd cnki-search-skill
python install.py
```

When `python install.py` is run from inside the cloned repository, the installer
automatically installs into the containing Claude Code project, not into the
clone's own `.claude` directory.

This also works when the clone is stored inside the project's `.claude`
directory:

```bash
cd /path/to/your/claude-project/.claude
git clone https://github.com/LongMarching/cnki-search-skill.git
cd cnki-search-skill
python install.py
```

That layout still installs to:

```text
/path/to/your/claude-project/.claude/skills/cnki-search/
```

If the clone lives somewhere else, pass the target project explicitly:

```bash
python /path/to/cnki-search-skill/install.py --target /path/to/your/claude-project
```

The target is the Claude Code project where `.claude/skills/cnki-search/`
should be created.

The installer copies:

```text
.claude/skills/cnki-search/
.claude/agents/cnki-paper-retriever.md
.claude/hooks/cnki_search_hook.py
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
install.py                         # clone-repo installer for Claude Code projects
.claude/settings.cnki-snippet.json # Claude Code hooks/settings snippet
.claude/agents/                   # Claude Code agent template
.claude/hooks/                    # Claude Code hook helper
.claude/skills/cnki-search/       # complete skill source and CLI
tools/                            # optional bundle builder and bundle installer
tests/                            # offline tests and guarded live harness
docs/                             # installation and development notes
```

## Direct Development Use

From the cloned repository root:

```bash
python .claude/skills/cnki-search/run.py search "机器学习" --page 1 --output-limit 5 --return-fields search_basic
```

PowerShell users should set UTF-8 output first:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
python .claude/skills/cnki-search/run.py search "机器学习" --page 1 --output-limit 5 --return-fields search_basic
```

Every command prints JSON. Use `status`, `workspace_id`, `run_id`, `summary`,
`rows`, `warnings`, and row-level status fields from that JSON.

## Main Commands

```bash
python .claude/skills/cnki-search/run.py search "机器学习" --pages 1-2 --sort citations
python .claude/skills/cnki-search/run.py fetch_details --workspace WORKSPACE --run RUN --top 10
python .claude/skills/cnki-search/run.py discover_facets --workspace WORKSPACE --run RUN --group subdiscipline
python .claude/skills/cnki-search/run.py export --workspace WORKSPACE --run RUN --rows 1-5 --mode GBTREFER BibTex
python .claude/skills/cnki-search/run.py download --workspace WORKSPACE --run RUN --rows 1-3 --format pdf
python .claude/skills/cnki-search/run.py inspect --workspace WORKSPACE --run RUN --view rows
```

The full command reference lives in [.claude/skills/cnki-search/SKILL.md](.claude/skills/cnki-search/SKILL.md).

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
python -m py_compile install.py .claude/skills/cnki-search/run.py .claude/skills/cnki-search/scripts/cli.py .claude/skills/cnki-search/src/actions/_workflow_impl.py
```

Live CNKI validation is opt-in:

```bash
CNKI_LIVE_TEST=1 python tests/live/cnki_search_live.py --help
```

## License

No open-source license has been added yet. Until the repository owner adds one,
all rights are reserved.
