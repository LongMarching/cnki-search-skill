# Installation

## Install After Clone

Clone this repository:

```bash
git clone https://github.com/LongMarching/cnki-search-skill.git
```

From the target Claude Code project root, run:

```bash
python /path/to/cnki-search-skill/install.py
```

Or pass the target explicitly:

```bash
python /path/to/cnki-search-skill/install.py --target /path/to/claude-project
```

The installer writes:

```text
<claude-project>/
  .claude/
    settings.local.json
    agents/
      cnki-paper-retriever.md
    skills/
      cnki-search/
```

If `.claude/settings.local.json` already exists, the installer creates a
timestamped backup before merging the settings snippet.

Reopen the Claude Code project after installation so local settings reload.

## Verify The Installed Skill

From the target Claude Code project root:

```bash
python .claude/skills/cnki-search/run.py search "机器学习" --page 1 --output-limit 5 --return-fields search_basic
```

The command should return structured JSON with `status`, `workspace_id`,
`run_id`, `summary`, and `rows`.

## Direct CLI Use From The Clone

The cloned repository can also run the skill directly:

```bash
python skill/cnki-search/run.py search "机器学习" --page 1
```

No package installation is required for the offline code path; the skill uses
Python standard library modules.

## Optional Claude Code Bundle

Build the bundle:

```bash
python tools/build_claude_bundle.py
```

The output is:

```text
dist/cnki-claude-bundle/
  install.py
  INSTALL.md
  .claude/settings.cnki-snippet.json
  .claude/agents/cnki-paper-retriever.md
  .claude/skills/cnki-search/
```

For normal use, prefer the top-level `install.py` flow.

## Access Configuration

The default cookie order is:

1. CNKI IP-login cookie seed
2. `CNKI_COOKIE`
3. `CNKI_COOKIE_FILE`

Useful environment variables:

```bash
export PYTHONIOENCODING=utf-8
export CNKI_WORKSPACE_DIR=/path/to/cnki-workspaces
export CNKI_DOWNLOAD_DIR=/path/to/cnki-downloads
export CNKI_COOKIE_FILE=/path/to/cnki-cookie.txt
```

Set `CNKI_AUTO_IP_LOGIN=0` only when you want to skip automatic IP-login.
