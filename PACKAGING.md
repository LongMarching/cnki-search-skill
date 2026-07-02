# Packaging

Most users do not need this file. The normal install path is:

```bash
python /path/to/cnki-search-skill/install.py --target /path/to/target-project
```

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
.claude/skills/cnki-search/
```

Install into a target Claude project:

```bash
cd dist/cnki-claude-bundle
python install.py --target /path/to/target-project
```
