"""Short-lived workspace storage helpers for cnki-search."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

WORKFLOW_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCHEMA_VERSION = 2
ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class WorkspaceStoreError(Exception):
    def __init__(self, code: str, detail: str = "", **extra: Any) -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code
        self.extra = extra


class WorkspaceLockTimeout(WorkspaceStoreError):
    def __init__(self, detail: str = "workspace lock timeout", **extra: Any) -> None:
        super().__init__("workspace_lock_timeout", detail, **extra)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso_at(hours: float) -> str:
    return (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def default_project_root():
    env_root = os.environ.get("CNKI_PROJECT_ROOT", "").strip()
    if env_root:
        return os.path.abspath(env_root)

    skills_root = os.path.dirname(WORKFLOW_DIR)
    if os.path.basename(skills_root).lower() == "skills":
        container_root = os.path.dirname(skills_root)
        if os.path.basename(container_root).lower() == ".claude":
            return os.path.dirname(container_root)
        return container_root
    return os.getcwd()


def default_workspace_root():
    return os.environ.get("CNKI_WORKSPACE_DIR") or os.path.join(WORKFLOW_DIR, "cnki-workspaces")


def default_workflow_root():
    """Compatibility alias for callers not yet renamed."""
    return default_workspace_root()


def workspace_ttl_hours() -> float:
    try:
        return max(0.1, float(os.environ.get("CNKI_WORKSPACE_TTL_HOURS", "12")))
    except ValueError:
        return 12.0


def _atomic_write_json(path: str | os.PathLike[str], data: Any) -> None:
    path = str(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def _read_json(path: str | os.PathLike[str], default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default


def _safe_id(value: str, kind: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise WorkspaceStoreError(f"{kind}_required", f"{kind} is required")
    if not ID_RE.fullmatch(text) or text in {".", ".."}:
        raise WorkspaceStoreError(f"invalid_{kind}", f"{kind} must contain only letters, digits, dot, underscore, or dash")
    return text


def new_workspace_id() -> str:
    return f"cws-{uuid.uuid4().hex[:12]}"


class WorkspaceStore:
    def __init__(self, root: str | os.PathLike[str]):
        self.root = os.path.abspath(str(root))

    def workspace_dir(self, workspace_id: str) -> str:
        return os.path.join(self.root, _safe_id(workspace_id, "workspace"))

    def workspace_path(self, workspace_id: str) -> str:
        return os.path.join(self.workspace_dir(workspace_id), "workspace.json")

    def runs_dir(self, workspace_id: str) -> str:
        return os.path.join(self.workspace_dir(workspace_id), "runs")

    def run_dir(self, workspace_id: str, run_id: str) -> str:
        return os.path.join(self.runs_dir(workspace_id), _safe_id(run_id, "run"))

    def run_path(self, workspace_id: str, run_id: str) -> str:
        return os.path.join(self.run_dir(workspace_id, run_id), "run.json")

    def results_path(self, workspace_id: str, run_id: str) -> str:
        return os.path.join(self.run_dir(workspace_id, run_id), "results.json")

    def details_path(self, workspace_id: str, run_id: str) -> str:
        return os.path.join(self.run_dir(workspace_id, run_id), "details.json")

    def artifacts_path(self, workspace_id: str, run_id: str) -> str:
        return os.path.join(self.run_dir(workspace_id, run_id), "artifacts.json")

    def events_path(self, workspace_id: str, run_id: str | None = None) -> str:
        if run_id:
            return os.path.join(self.run_dir(workspace_id, run_id), "events.jsonl")
        return os.path.join(self.workspace_dir(workspace_id), "events.jsonl")

    def cleanup_expired(self) -> list[str]:
        root = Path(self.root)
        if not root.exists():
            return []
        removed: list[str] = []
        now = datetime.now(timezone.utc)
        for child in root.iterdir():
            if not child.is_dir():
                continue
            meta_path = child / "workspace.json"
            if not meta_path.exists():
                continue
            meta = _read_json(meta_path, {})
            expires_at = parse_utc(str(meta.get("expires_at", "")))
            if expires_at and expires_at <= now:
                shutil.rmtree(child, ignore_errors=True)
                removed.append(child.name)
        return removed

    def create_workspace(self, workspace_id: str | None = None) -> dict[str, Any]:
        workspace_id = _safe_id(workspace_id or new_workspace_id(), "workspace")
        path = self.workspace_path(workspace_id)
        if os.path.exists(path):
            return self.load_workspace(workspace_id)
        os.makedirs(self.runs_dir(workspace_id), exist_ok=True)
        workspace = {
            "schema_version": SCHEMA_VERSION,
            "workspace_id": workspace_id,
            "created_at": utc_now(),
            "expires_at": _iso_at(workspace_ttl_hours()),
            "updated_at": utc_now(),
            "last_action": "create_workspace",
            "active_run_id": "",
            "runs": {},
        }
        _atomic_write_json(path, workspace)
        self.append_event(workspace_id, None, "workspace_created", expires_at=workspace["expires_at"])
        return workspace

    def exists(self, workspace_id: str) -> bool:
        return os.path.exists(self.workspace_path(workspace_id))

    def load_workspace(self, workspace_id: str) -> dict[str, Any]:
        path = self.workspace_path(workspace_id)
        if not os.path.exists(path):
            raise WorkspaceStoreError("workspace_not_found", f"workspace {workspace_id!r} does not exist", workspace_id=workspace_id)
        return _read_json(path, {})

    def save_workspace(self, workspace: dict[str, Any]) -> None:
        workspace["updated_at"] = utc_now()
        _atomic_write_json(self.workspace_path(workspace["workspace_id"]), workspace)

    def resolve_workspace(self, workspace_id: str | None = None, create: bool = False) -> dict[str, Any]:
        resolved = str(workspace_id or os.environ.get("CNKI_WORKSPACE_ID", "")).strip()
        if not resolved:
            if create:
                return self.create_workspace()
            raise WorkspaceStoreError("workspace_required", "provide --workspace or set CNKI_WORKSPACE_ID")
        if self.exists(resolved):
            return self.load_workspace(resolved)
        if create:
            return self.create_workspace(resolved)
        raise WorkspaceStoreError("workspace_not_found", f"workspace {resolved!r} does not exist", workspace_id=resolved)

    def load_run(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        path = self.run_path(workspace_id, run_id)
        if not os.path.exists(path):
            raise WorkspaceStoreError("run_not_found", f"run {run_id!r} does not exist", workspace_id=workspace_id, run_id=run_id)
        return _read_json(path, {})

    def save_run(self, run: dict[str, Any]) -> None:
        run["updated_at"] = utc_now()
        _atomic_write_json(self.run_path(run["workspace_id"], run["run_id"]), run)

    def create_run(self, workspace_id: str, run: dict[str, Any], results: list[dict[str, Any]] | None = None,
                   details: dict[str, Any] | None = None, artifacts: dict[str, Any] | None = None) -> dict[str, Any]:
        os.makedirs(self.run_dir(workspace_id, run["run_id"]), exist_ok=True)
        self.save_run(run)
        self.save_results(workspace_id, run["run_id"], results or [])
        self.save_details(workspace_id, run["run_id"], details or {})
        self.save_artifacts(workspace_id, run["run_id"], artifacts or {})
        workspace = self.load_workspace(workspace_id)
        workspace.setdefault("runs", {})[run["run_id"]] = self.run_summary(run)
        workspace["active_run_id"] = run["run_id"]
        workspace["last_action"] = "search"
        self.save_workspace(workspace)
        return run

    def refresh_run(self, workspace_id: str, run: dict[str, Any], results: list[dict[str, Any]],
                    details: dict[str, Any] | None = None, artifacts: dict[str, Any] | None = None) -> None:
        os.makedirs(self.run_dir(workspace_id, run["run_id"]), exist_ok=True)
        self.save_run(run)
        self.save_results(workspace_id, run["run_id"], results)
        self.save_details(workspace_id, run["run_id"], details or {})
        self.save_artifacts(workspace_id, run["run_id"], artifacts or {})
        workspace = self.load_workspace(workspace_id)
        workspace.setdefault("runs", {})[run["run_id"]] = self.run_summary(run)
        workspace["active_run_id"] = run["run_id"]
        workspace["last_action"] = run.get("last_action", "search")
        self.save_workspace(workspace)

    def set_active_run(self, workspace_id: str, run_id: str) -> None:
        workspace = self.load_workspace(workspace_id)
        if not os.path.exists(self.run_path(workspace_id, run_id)):
            raise WorkspaceStoreError("run_not_found", f"run {run_id!r} does not exist", workspace_id=workspace_id, run_id=run_id)
        workspace["active_run_id"] = run_id
        self.save_workspace(workspace)

    def resolve_run_id(self, workspace_id: str, run_id: str | None = None) -> str:
        if run_id:
            _safe_id(run_id, "run")
            if not os.path.exists(self.run_path(workspace_id, run_id)):
                raise WorkspaceStoreError("run_not_found", f"run {run_id!r} does not exist", workspace_id=workspace_id, run_id=run_id)
            return run_id
        workspace = self.load_workspace(workspace_id)
        active = str(workspace.get("active_run_id") or "").strip()
        if not active:
            raise WorkspaceStoreError("run_required", "provide --run or run search first", workspace_id=workspace_id)
        return active

    def load_results(self, workspace_id: str, run_id: str) -> list[dict[str, Any]]:
        return _read_json(self.results_path(workspace_id, run_id), [])

    def save_results(self, workspace_id: str, run_id: str, results: list[dict[str, Any]]) -> None:
        _atomic_write_json(self.results_path(workspace_id, run_id), results)

    def merge_results(self, workspace_id: str, run_id: str, updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        existing = self.load_results(workspace_id, run_id)
        by_rank = {int(row.get("global_rank", 0) or 0): dict(row) for row in existing}
        for update in updates:
            rank = int(update.get("global_rank", 0) or 0)
            if not rank:
                continue
            merged = by_rank.get(rank, {})
            merged.update(update)
            by_rank[rank] = merged
        results = [by_rank[key] for key in sorted(by_rank)]
        self.save_results(workspace_id, run_id, results)
        return results

    def load_details(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        return _read_json(self.details_path(workspace_id, run_id), {})

    def save_details(self, workspace_id: str, run_id: str, details: dict[str, Any]) -> None:
        _atomic_write_json(self.details_path(workspace_id, run_id), details)

    def merge_details(self, workspace_id: str, run_id: str, details: dict[str, Any]) -> dict[str, Any]:
        existing = self.load_details(workspace_id, run_id)
        existing.update(details)
        self.save_details(workspace_id, run_id, existing)
        return existing

    def load_artifacts(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        return _read_json(self.artifacts_path(workspace_id, run_id), {})

    def save_artifacts(self, workspace_id: str, run_id: str, artifacts: dict[str, Any]) -> None:
        _atomic_write_json(self.artifacts_path(workspace_id, run_id), artifacts)

    def merge_artifacts(self, workspace_id: str, run_id: str, key: str, updates: dict[str, Any]) -> dict[str, Any]:
        artifacts = self.load_artifacts(workspace_id, run_id)
        bucket = dict(artifacts.get(key) or {})
        bucket.update(updates)
        artifacts[key] = bucket
        self.save_artifacts(workspace_id, run_id, artifacts)
        return artifacts

    def append_event(self, workspace_id: str, run_id: str | None, event: str, **data: Any) -> None:
        event_path = self.events_path(workspace_id, run_id)
        os.makedirs(os.path.dirname(event_path), exist_ok=True)
        payload = {"ts": utc_now(), "event": event, **data}
        with open(event_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def run_summary(self, run: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": run.get("run_id", ""),
            "signature": run.get("signature", ""),
            "query": run.get("query", ""),
            "label": run.get("label", ""),
            "topic": run.get("topic", ""),
            "status": run.get("status", ""),
            "result_count": run.get("result_count", 0),
            "pages_loaded": run.get("pages_loaded", []),
            "created_at": run.get("created_at", ""),
            "updated_at": run.get("updated_at", ""),
            "last_action": run.get("last_action", ""),
        }

    @contextlib.contextmanager
    def with_run_lock(self, workspace_id: str, run_id: str | None = None) -> Iterator[None]:
        target_dir = self.run_dir(workspace_id, run_id) if run_id else self.workspace_dir(workspace_id)
        os.makedirs(target_dir, exist_ok=True)
        lock_path = os.path.join(target_dir, ".lock")
        timeout = float(os.environ.get("CNKI_WORKSPACE_LOCK_TIMEOUT", "10"))
        started = time.monotonic()
        fd: int | None = None
        while fd is None:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
            except FileExistsError as exc:
                if time.monotonic() - started >= timeout:
                    raise WorkspaceLockTimeout(lock_path=lock_path, workspace_id=workspace_id, run_id=run_id or "") from exc
                time.sleep(0.1)
        try:
            yield
        finally:
            if fd is not None:
                os.close(fd)
            try:
                os.unlink(lock_path)
            except OSError:
                pass
