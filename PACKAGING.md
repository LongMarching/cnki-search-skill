# Packaging

Most users do not need this file. The normal install path is:

```bash
cd /path/to/target-project
git clone https://github.com/LongMarching/cnki-search-skill.git
cd cnki-search-skill
python install.py
```

Running `python install.py` inside the clone installs into the clone's parent
project. If the clone is inside `<project>/.claude/`, it installs into
`<project>`. Use `--target /path/to/target-project` only when installing from
another location.

To build a standalone Claude Code bundle from the repository root:

```bash
python tools/build_claude_bundle.py
```

Default output:

```text
dist/cnki-claude-bundle/
```

Custom output:

```bash
python tools/build_claude_bundle.py --output /tmp/cnki-claude-bundle
```

The bundle contains:

```text
install.py
INSTALL.md
.claude/settings.cnki-snippet.json
.claude/agents/cnki-paper-retriever.md
.claude/hooks/cnki_search_hook.py
.claude/skills/cnki-search/
```

Install into a target Claude project:

```bash
cd dist/cnki-claude-bundle
python install.py --target /path/to/target-project
```
