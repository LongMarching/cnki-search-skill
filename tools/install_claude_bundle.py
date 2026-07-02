"""Install a CNKI Claude bundle into a target project."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path


BUNDLE_FILES = (
    ".claude/agents/cnki-paper-retriever.md",
    ".claude/hooks/cnki_search_hook.py",
)
BUNDLE_DIRS = (
    ".claude/skills/cnki-search",
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
    if src.resolve() == dst.resolve():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_tree(src_root: Path, dst_root: Path) -> None:
    if src_root.resolve() == dst_root.resolve():
        return
    if dst_root.exists():
        shutil.rmtree(dst_root)
    shutil.copytree(src_root, dst_root)


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
    if not isinstance(target_hooks, dict):
        target_hooks = {}
    if not isinstance(snippet_hooks, dict):
        raise ValueError("settings snippet 'hooks' must be an object")

    merged["hooks"] = merge_hooks(target_hooks, snippet_hooks)

    dump_json(target_settings_path, merged)
    return backup


def remove_legacy_file(path: Path) -> bool:
    if not path.exists():
        return False
    path.unlink()
    parent = path.parent
    while parent.name and parent.exists():
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
    return True


def remove_legacy_tree(path: Path) -> bool:
    if not path.exists():
        return False
    shutil.rmtree(path)
    parent = path.parent
    while parent.name and parent.exists():
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
    return True


def install_bundle(bundle_root: Path, target_root: Path) -> dict:
    summary: dict[str, object] = {
        "bundle_root": str(bundle_root),
        "target_root": str(target_root),
        "copied_files": [],
        "copied_dirs": [],
        "removed_legacy_files": [],
        "removed_legacy_dirs": [],
        "settings_file": "",
        "settings_backup": "",
    }

    for rel_path in BUNDLE_FILES:
        src = bundle_root / rel_path
        dst = target_root / rel_path
        copy_file(src, dst)
        summary["copied_files"].append(rel_path)

    for rel_path in BUNDLE_DIRS:
        src = bundle_root / rel_path
        dst = target_root / rel_path
        copy_tree(src, dst)
        summary["copied_dirs"].append(rel_path)

    for rel_path in LEGACY_REMOVE_FILES:
        if remove_legacy_file(target_root / rel_path):
            summary["removed_legacy_files"].append(rel_path)
    for rel_path in LEGACY_REMOVE_DIRS:
        if remove_legacy_tree(target_root / rel_path):
            summary["removed_legacy_dirs"].append(rel_path)

    target_settings = target_root / ".claude" / "settings.local.json"
    backup = merge_settings(bundle_root / SETTINGS_SNIPPET, target_settings)
    summary["settings_file"] = str(target_settings)
    summary["settings_backup"] = str(backup) if backup else ""
    return summary


def infer_target_root(bundle_root: Path, explicit_target: str | None) -> Path:
    if explicit_target and explicit_target.strip() not in {"", "."}:
        return Path(explicit_target).resolve()

    if bundle_root.parent.name == ".claude":
        return bundle_root.parent.parent.resolve()

    if bundle_root.name.startswith("cnki-claude-bundle"):
        return bundle_root.parent.resolve()

    return Path.cwd().resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the CNKI Claude bundle into a target project.")
    parser.add_argument(
        "--target",
        default=".",
        help="Target Claude project root. Defaults to auto-detection based on the bundle location.",
    )
    parser.add_argument(
        "--bundle-root",
        default=str(Path(__file__).resolve().parent),
        help="Bundle root directory. Defaults to the directory containing this installer.",
    )
    args = parser.parse_args()

    bundle_root = Path(args.bundle_root).resolve()
    target_root = infer_target_root(bundle_root, args.target)
    summary = install_bundle(bundle_root, target_root)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
