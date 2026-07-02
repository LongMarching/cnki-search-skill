from __future__ import annotations

from typing import Any

from core.state_store import WorkspaceStore, utc_now


def create_workspace_run(
    store: WorkspaceStore,
    workspace_id: str = "cws-test",
    run_id: str = "run-test",
    results: list[dict[str, Any]] | None = None,
    query: str = "机器学习",
    label: str = "",
    topic: str = "",
) -> tuple[str, str]:
    store.create_workspace(workspace_id)
    rows = results or []
    now = utc_now()
    pages_loaded = sorted({int(row.get("page_no", 1) or 1) for row in rows}) or [1]
    run = {
        "schema_version": 2,
        "workspace_id": workspace_id,
        "run_id": run_id,
        "signature": f"sig-{run_id}",
        "signature_source": "{}",
        "query": query,
        "label": label,
        "topic": topic,
        "language": "zh",
        "search_mode": "advsearch",
        "extra": {},
        "doc_type": "all",
        "discipline": [],
        "quality": [],
        "elite_uni": None,
        "sort": "date",
        "date_from": None,
        "date_to": None,
        "fields": None,
        "form_filters": [],
        "page_size": 20,
        "created_at": now,
        "updated_at": now,
        "pages_loaded": pages_loaded,
        "result_count": len(rows),
        "status": "ok",
        "last_action": "search",
        "search_transport": "http",
    }
    store.create_run(workspace_id, run, results=rows, details={}, artifacts={})
    return workspace_id, run_id
