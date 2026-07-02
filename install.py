"""Install this cloned repository into a Claude Code project."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent

SOURCE_FILES = (
    (".claude/agents/cnki-paper-retriever.md", ".claude/agents/cnki-paper-retriever.md"),
    (".claude/hooks/cnki_search_hook.py", ".claude/hooks/cnki_search_hook.py"),
)
SOURCE_DIRS = (
    (".claude/skills/cnki-search", ".claude/skills/cnki-search"),
)
LEGACY_REMOVE_FILES = (
    ".claude/agents/academic-researcher.md",
    ".claude/agents/literature-retriever.md",
    ".claude/hooks/cnki_session_hook.py",
    ".claude/skills/cnki_session_registry.py",
    "agents/literature-retriever.md",
)
LEGACY_REMOVE_DIRS = (
    ".claude/skills/paper-workflow",
    ".claude/skills/journal-workflow",
    "skills/paper-workflow",
    "skills/journal-workflow",
)
SETTINGS_SNIPPET = ".claude/settings.cnki-snippet.json"


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig") as handle:
        return json.load(handle)


def dump_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup)
    return backup


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_tree(src_root: Path, dst_root: Path) -> None:
    if src_root.resolve() == dst_root.resolve():
        return
    if dst_root.exists():
        shutil.rmtree(dst_root)
    shutil.copytree(
        src_root,
        dst_root,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "cnki-workspaces"),
    )


def hook_key(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def merge_hooks(target_hooks: dict, snippet_hooks: dict) -> dict:
    merged_hooks = dict(target_hooks)
    for event_name, snippet_entries in snippet_hooks.items():
        if not isinstance(snippet_entries, list):
            raise ValueError(f"settings snippet hooks.{event_name} must be a list")

        target_entries = merged_hooks.get(event_name)
        if not isinstance(target_entries, list):
            target_entries = []

        seen = {hook_key(entry) for entry in target_entries}
        for entry in snippet_entries:
            key = hook_key(entry)
            if key not in seen:
                target_entries.append(entry)
                seen.add(key)
        merged_hooks[event_name] = target_entries
    return merged_hooks


def merge_settings(snippet_path: Path, target_settings_path: Path) -> Path | None:
    snippet = load_json(snippet_path)
    if not isinstance(snippet, dict):
        raise ValueError(f"Invalid settings snippet: {snippet_path}")

    target_payload = load_json(target_settings_path)
    if not isinstance(target_payload, dict):
        raise ValueError(f"Invalid target settings file: {target_settings_path}")

    backup = backup_file(target_settings_path)
    merged = dict(target_payload)

    snippet_hooks = snippet.get("hooks") or {}
    target_hooks = merged.get("hooks") or {}
    if not isinstance(snippet_hooks, dict):
        raise ValueError("settings snippet 'hooks' must be an object")
    if not isinstance(target_hooks, dict):
        target_hooks = {}

    merged["hooks"] = merge_hooks(target_hooks, snippet_hooks)
    dump_json(target_settings_path, merged)
    return backup


def remove_file(path: Path) -> bool:
    if not path.exists():
        return False
    path.unlink()
    return True


def remove_tree(path: Path) -> bool:
    if not path.exists():
        return False
    shutil.rmtree(path)
    return True


def install(target_root: Path, source_root: Path = REPO_ROOT) -> dict:
    source_root = source_root.resolve()
    target_root = target_root.resolve()
    if target_root == source_root:
        raise ValueError(
            "Refusing to install into the cloned cnki-search-skill repository itself. "
            "Run this installer from your Claude Code project root, or pass "
            "`--target /path/to/your/claude-project`."
        )

    summary: dict[str, object] = {
        "source_root": str(source_root),
        "target_root": str(target_root),
        "copied_files": [],
        "copied_dirs": [],
        "removed_legacy_files": [],
        "removed_legacy_dirs": [],
        "settings_file": "",
        "settings_backup": "",
    }

    for src_rel, dst_rel in SOURCE_FILES:
        copy_file(source_root / src_rel, target_root / dst_rel)
        summary["copied_files"].append(dst_rel)

    for src_rel, dst_rel in SOURCE_DIRS:
        copy_tree(source_root / src_rel, target_root / dst_rel)
        summary["copied_dirs"].append(dst_rel)

    for rel_path in LEGACY_REMOVE_FILES:
        if remove_file(target_root / rel_path):
            summary["removed_legacy_files"].append(rel_path)
    for rel_path in LEGACY_REMOVE_DIRS:
        if remove_tree(target_root / rel_path):
            summary["removed_legacy_dirs"].append(rel_path)

    target_settings = target_root / ".claude" / "settings.local.json"
    backup = merge_settings(source_root / SETTINGS_SNIPPET, target_settings)
    summary["settings_file"] = str(target_settings)
    summary["settings_backup"] = str(backup) if backup else ""
    return summary


def infer_target_root(source_root: Path = REPO_ROOT, cwd: Path | None = None, explicit_target: str | None = None) -> Path:
    if explicit_target and explicit_target.strip():
        return Path(explicit_target).resolve()

    source_root = source_root.resolve()
    cwd = (cwd or Path.cwd()).resolve()
    if source_root.name == ".claude":
        return source_root.parent.resolve()
    if source_root.parent.name == ".claude":
        return source_root.parent.parent.resolve()
    if cwd == source_root or source_root in cwd.parents:
        return source_root.parent.resolve()
    return cwd


def main() -> int:
    parser = argparse.ArgumentParser(description="Install cnki-search into a Claude Code project.")
    parser.add_argument(
        "--target",
        default=None,
        help=(
            "Target Claude Code project root. Defaults to the current working directory, "
            "or to the containing Claude project when run inside the clone."
        ),
    )
    args = parser.parse_args()

    target_root = infer_target_root(explicit_target=args.target)
    try:
        summary = install(target_root)
    except ValueError as exc:
        parser.error(str(exc))
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
