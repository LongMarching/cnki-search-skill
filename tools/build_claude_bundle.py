"""Build a shareable Claude bundle containing CNKI skills and agent assets."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "dist" / "cnki-claude-bundle"

SKILL_DIRS = ("cnki-search",)
ROOT_FILES = (
)
TEMPLATE_FILES = (
    ("agent/cnki-paper-retriever.md", ".claude/agents/cnki-paper-retriever.md"),
)


def should_skip(path: Path) -> bool:
    parts = set(path.parts)
    if "__pycache__" in parts:
        return True
    if "cnki-workflows" in parts or "cnki-workspaces" in parts:
        return True
    if path.name.endswith((".pyc", ".pyo")):
        return True
    return False


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_tree(src_root: Path, dst_root: Path) -> None:
    for src in src_root.rglob("*"):
        if should_skip(src):
            continue
        rel = src.relative_to(src_root)
        dst = dst_root / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        copy_file(src, dst)


def rewrite_text(path: Path, replacements: list[tuple[str, str]]) -> None:
    text = path.read_text(encoding="utf-8")
    for old, new in replacements:
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


def write_install_doc(bundle_root: Path) -> None:
    text = """# CNKI Claude Bundle

This bundle contains the shareable CNKI skills and agent assets:

- `install.py`
- `.claude/settings.cnki-snippet.json`
- `.claude/agents/cnki-paper-retriever.md` (CNKI paper retrieval agent)
- `.claude/skills/cnki-search/`

## Install Into Another Claude Project

### Simple install

If the bundle sits inside the target project (for example `<project>/cnki-claude-bundle/` or `<project>/.claude/cnki-claude-bundle/`), just run:

```bash
python install.py
```

The installer will:

1. copy the CNKI skill and agent files into the target project
2. merge the CNKI settings snippet into `.claude/settings.local.json`
3. create a timestamped backup if `.claude/settings.local.json` already exists
4. auto-detect the target project root from the bundle location

### Install from another location

```bash
python install.py --target E:/path/to/target-project
```

After install, reopen the Claude project so settings reload.

## Validation

1. Open a Claude chat in the target project root.
2. Run `cd .claude/skills/cnki-search && python run.py search "机器学习" --page 1`
"""
    (bundle_root / "INSTALL.md").write_text(text, encoding="utf-8")


def write_settings_snippet(bundle_root: Path) -> None:
    src = REPO_ROOT / ".claude" / "settings.json"
    dst = bundle_root / ".claude" / "settings.cnki-snippet.json"
    copy_file(src, dst)


def build_bundle(output_root: Path) -> None:
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    for src_rel, dst_rel in TEMPLATE_FILES:
        copy_file(REPO_ROOT / src_rel, output_root / dst_rel)

    write_settings_snippet(output_root)
    copy_file(REPO_ROOT / "tools" / "install_claude_bundle.py", output_root / "install.py")

    for skill_dir in SKILL_DIRS:
        src = REPO_ROOT / "skill" / skill_dir
        dst = output_root / ".claude" / "skills" / skill_dir
        copy_tree(src, dst)
        rewrite_text(
            dst / "SKILL.md",
            [
                ("cd skill/cnki-search", "cd .claude/skills/cnki-search"),
            ],
        )

    write_install_doc(output_root)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a shareable Claude skills/agent bundle.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Destination directory for the generated bundle (default: dist/cnki-claude-bundle)",
    )
    args = parser.parse_args()
    output_root = Path(args.output).resolve()
    build_bundle(output_root)
    print(output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
