"""Internal orchestration for the cnki-search skill."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
from typing import Any

from adapters.details import HttpDetailUnavailable, fetch_detail_rows
from adapters.downloads import download_direct_rows
from adapters.exports import HttpExportUnavailable, export_rows
from adapters.facets import HttpFacetUnavailable, run_http_facets
from adapters.search import HttpSearchUnavailable, SearchSpec, run_http_advsearch_workflow
from core.fieldsets import merge_row_with_detail, project_row, resolve_return_fields, strip_url_fields
from core.http import (
    ADVSEARCH_FIELD_CODES,
    ADVSEARCH_MODES,
    DOC_TYPE_MAP,
    ELITE_UNI_MAP,
    FORM_FILTER_MAP,
    JOURNAL_QUALITY_KEYS,
    SORT_KEY_MAP,
    THESIS_DOC_TYPES,
)
from core.state_store import (
    WorkspaceLockTimeout,
    WorkspaceStore,
    WorkspaceStoreError,
    default_project_root,
    default_workspace_root,
    utc_now,
)

VALID_LANGUAGES = {"zh", "en", "both"}
VALID_SEARCH_MODES = ADVSEARCH_MODES
VALID_DOC_TYPES = set(DOC_TYPE_MAP.keys())
VALID_FACET_GROUPS = {"subdiscipline"}
FACET_RETURN_FIELDS = ["index", "code", "label", "count", "checked", "visible"]
VALID_DOWNLOAD_FORMATS = {"pdf", "caj"}


def _parse_int(value, default=0):
    text = str(value or "").replace(",", "").strip()
    if not text:
        return int(default)
    try:
        return int(text)
    except ValueError:
        return int(default)


def _store(store=None) -> WorkspaceStore:
    store = store or WorkspaceStore(default_workspace_root())
    try:
        store.cleanup_expired()
    except OSError:
        pass
    return store


def _success_payload(action, workspace_id, run_id, count, summary, rows, returned_fields, warnings=None, **extra):
    payload = {
        "status": "ok",
        "workspace_id": workspace_id or "",
        "run_id": run_id or "",
        "action": action,
        "count": count,
        "summary": summary,
        "rows": rows,
        "returned_fields": returned_fields,
        "warnings": warnings or [],
    }
    payload.update(extra)
    return payload


def _error_payload(action, code, detail="", workspace_id="", run_id="", warnings=None, **extra):
    payload = {
        "status": "error",
        "workspace_id": workspace_id or "",
        "run_id": run_id or "",
        "action": action,
        "count": 0,
        "summary": {},
        "rows": [],
        "returned_fields": [],
        "warnings": warnings or [],
        "error": code,
    }
    if detail:
        payload["detail"] = detail
    payload.update(extra)
    return payload


def _store_error_payload(action: str, exc: WorkspaceStoreError):
    return _error_payload(action, exc.code, exc.detail, **exc.extra)


DEFAULT_DOWNLOAD_ROOT = "cnki-search-download"
DOWNLOAD_FORMAT_DIRS = {"pdf": "PDF", "caj": "CAJ"}


def _resolve_download_dir(download_dir=None, fmt=None):
    configured = str(download_dir or os.environ.get("CNKI_DOWNLOAD_DIR", "") or "").strip()
    if configured:
        if os.path.isabs(configured):
            return os.path.abspath(configured)
        return os.path.abspath(os.path.join(default_project_root(), configured))
    fmt_dir = DOWNLOAD_FORMAT_DIRS.get(str(fmt or "").lower(), str(fmt or "PDF").upper())
    return os.path.join(default_project_root(), DEFAULT_DOWNLOAD_ROOT, fmt_dir)


def _normalize_authors(value):
    if isinstance(value, list):
        authors = []
        for item in value:
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()
                if name:
                    authors.append(name)
            else:
                text = str(item).strip()
                if text:
                    authors.append(text)
        return "; ".join(authors)
    return str(value or "").strip()


def _result_row_from_search_page(item, page_no, page_row_no, global_rank):
    row = {
        "row_id": f"row-{global_rank:04d}",
        "page_no": int(page_no),
        "page_row_no": int(page_row_no),
        "global_rank": int(global_rank),
        "title": item.get("title", ""),
        "authors": _normalize_authors(item.get("authors")),
        "journal": item.get("journal", ""),
        "date": item.get("date", ""),
        "database": item.get("database", ""),
        "citations": item.get("citations", ""),
        "downloads": item.get("downloads", ""),
        "is_online_first": bool(item.get("is_online_first") or item.get("isOnlineFirst")),
        "detail_url": item.get("detail_url") or item.get("href") or item.get("url") or "",
        "export_id": item.get("export_id") or item.get("exportId") or "",
        "detail_status": "pending",
        "detail_ref": "",
        "detail_error": "",
        "download_status": "pending",
        "download_error": "",
        "download_path": "",
        "download_format": "",
    }
    for field in ("pdf_url", "caj_url", "download_url"):
        if item.get(field):
            row[field] = item.get(field)
    return row


def _rows_from_pages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for page_payload in payload.get("pages", []):
        page_no = _parse_int(page_payload.get("page_no"), len(results) + 1)
        for page_row_no, item in enumerate(page_payload.get("results", []), start=1):
            global_rank = len(results) + 1
            results.append(_result_row_from_search_page(item, page_no, page_row_no, global_rank))
    return results


def _merge_search_rows(old_rows: list[dict[str, Any]], new_rows: list[dict[str, Any]], refresh=False) -> list[dict[str, Any]]:
    if refresh:
        return new_rows
    old_index = {int(row.get("global_rank", 0) or 0): row for row in old_rows}
    merged: list[dict[str, Any]] = []
    state_fields = {
        "detail_status", "detail_ref", "detail_error",
        "download_status", "download_error", "download_path", "download_format",
        "download_transport", "download_strategy",
    }
    link_fields = {"pdf_url", "caj_url", "download_url"}
    for row in new_rows:
        rank = int(row.get("global_rank", 0) or 0)
        previous = old_index.get(rank)
        if previous:
            combined = dict(row)
            for field in state_fields | link_fields:
                if previous.get(field) and not (field in link_fields and row.get(field)):
                    combined[field] = previous.get(field)
            merged.append(combined)
        else:
            merged.append(row)
    known = {int(row.get("global_rank", 0) or 0) for row in merged}
    for row in old_rows:
        rank = int(row.get("global_rank", 0) or 0)
        if rank and rank not in known:
            merged.append(row)
    return sorted(merged, key=lambda row: int(row.get("global_rank", 0) or 0))


def _detail_record_from_payload(row, payload):
    detail_ref = row.get("detail_ref") or f"detail-{row['row_id']}"
    return detail_ref, {
        "detail_ref": detail_ref,
        "row_id": row["row_id"],
        "global_rank": row["global_rank"],
        "title": payload.get("title", row.get("title", "")),
        "abstract": payload.get("abstract", ""),
        "keywords": payload.get("keywords", []),
        "fund": payload.get("fund", ""),
        "classification": payload.get("classification", ""),
        "authors_structured": payload.get("authors_structured") or payload.get("authors") or [],
        "affiliations": payload.get("affiliations", []),
        "journal": payload.get("journal") or row.get("journal", ""),
        "pub_info": payload.get("pub_info") or payload.get("pubInfo") or "",
        "citation_info": payload.get("citation_info") or payload.get("citationInfo") or {},
        "toc": payload.get("toc", ""),
        "raw_url": payload.get("raw_url") or payload.get("url") or row.get("detail_url") or "",
        "fetched_at": utc_now(),
    }


def _summary_from_run(workspace: dict[str, Any], run: dict[str, Any], extra=None):
    discipline_codes = run.get("discipline", [])
    active_filters = {}
    if discipline_codes:
        from core.discipline_map import code_to_name as _ctn_map
        cn_map = _ctn_map()
        active_filters["discipline"] = [cn_map.get(code, code) for code in discipline_codes]
    summary = {
        "query": run.get("query", ""),
        "label": run.get("label", ""),
        "topic": run.get("topic", ""),
        "language": run.get("language", ""),
        "search_mode": run.get("search_mode", "advsearch"),
        "doc_type": run.get("doc_type", "all"),
        "discipline": discipline_codes,
        "active_filters": active_filters,
        "quality": run.get("quality", []),
        "elite_uni": run.get("elite_uni") or None,
        "sort": run.get("sort", "date"),
        "date_from": run.get("date_from") or None,
        "date_to": run.get("date_to") or None,
        "fields": run.get("fields") or None,
        "form_filters": run.get("form_filters", []),
        "page_size": run.get("page_size", 20),
        "result_count": run.get("result_count", 0),
        "pages_loaded": run.get("pages_loaded", []),
        "status": run.get("status", ""),
        "created_at": run.get("created_at", ""),
        "updated_at": run.get("updated_at", ""),
        "last_action": run.get("last_action", ""),
        "workspace_expires_at": workspace.get("expires_at", ""),
        "active_run": workspace.get("active_run_id") == run.get("run_id"),
        "search_transport": run.get("search_transport", "http"),
    }
    if run.get("no_results_message"):
        summary["no_results_message"] = run.get("no_results_message")
    if extra:
        summary.update(extra)
    return summary


def _select_rows(results, requested_rows):
    index = {int(row["global_rank"]): row for row in results}
    selected = []
    missing = []
    for number in requested_rows:
        row = index.get(int(number))
        if row is None:
            missing.append(int(number))
        else:
            selected.append(row)
    return selected, missing


def _rows_for_pages(results: list[dict[str, Any]], page_numbers: list[int]) -> list[dict[str, Any]]:
    page_set = {int(page) for page in page_numbers}
    return [row for row in results if int(row.get("page_no", 0) or 0) in page_set]


def _parse_page_range(page=1, pages=None) -> list[int]:
    if pages:
        numbers = sorted({int(value) for value in pages if int(value) > 0})
    else:
        numbers = [max(1, int(page or 1))]
    max_pages = max(1, int(os.environ.get("CNKI_HTTP_MAX_PAGES_PER_COMMAND", "10")))
    if len(numbers) > max_pages:
        raise ValueError(f"requested pages exceed CNKI_HTTP_MAX_PAGES_PER_COMMAND={max_pages}")
    return numbers


def _normalize_search_inputs(
    query, language, search_mode, extra, doc_type, discipline, quality, subdiscipline,
    elite_uni, sort, date_from, date_to, fields, form_filters,
):
    if not (query or "").strip() and search_mode not in ("author", "sentence"):
        if not fields:
            raise ValueError("query must not be empty (or provide --fields for multi-field mode)")
    if language not in VALID_LANGUAGES:
        raise ValueError("language must be one of: zh, en, both")
    if search_mode not in VALID_SEARCH_MODES:
        raise ValueError(f"search_mode must be one of: {', '.join(sorted(VALID_SEARCH_MODES))}")
    doc_type = doc_type or "all"
    if doc_type not in VALID_DOC_TYPES:
        raise ValueError(f"doc_type must be one of: {', '.join(sorted(VALID_DOC_TYPES))}")

    from core.discipline_map import resolve_code
    resolved_discipline = []
    bad_disc = []
    for value in [str(c).strip() for c in (discipline or []) if str(c).strip()]:
        resolved = resolve_code(value)
        if resolved:
            resolved_discipline.append(resolved)
        else:
            bad_disc.append(value)
    for value in [str(c).strip() for c in (subdiscipline or []) if str(c).strip()]:
        resolved = resolve_code(value)
        if resolved:
            resolved_discipline.append(resolved)
        else:
            bad_disc.append(value)
    if bad_disc:
        raise ValueError(f"unknown discipline values: {bad_disc}")

    raw_quality = [str(k).lower() for k in (quality or [])]
    bad_quality = [k for k in raw_quality if k not in JOURNAL_QUALITY_KEYS]
    if bad_quality:
        raise ValueError(f"quality keys unknown: {bad_quality}; valid: {sorted(JOURNAL_QUALITY_KEYS)}")
    quality = [k for k in raw_quality if k in JOURNAL_QUALITY_KEYS]

    elite_uni = (elite_uni or "").strip().lower() or None
    if elite_uni and elite_uni not in ELITE_UNI_MAP:
        raise ValueError(f"elite_uni must be one of: {', '.join(sorted(ELITE_UNI_MAP))}")
    if elite_uni and doc_type not in THESIS_DOC_TYPES:
        raise ValueError("elite_uni requires --doc-type thesis, phd, or masters")

    sort = sort or "date"
    if sort not in SORT_KEY_MAP:
        raise ValueError(f"sort must be one of: {', '.join(SORT_KEY_MAP)}")

    raw_filters = [str(k).lower() for k in (form_filters or [])]
    bad_filters = [k for k in raw_filters if k not in FORM_FILTER_MAP]
    if bad_filters:
        raise ValueError(f"form_filters keys unknown: {bad_filters}; valid: {sorted(FORM_FILTER_MAP)}")
    form_filters = [k for k in raw_filters if k in FORM_FILTER_MAP]

    if isinstance(fields, str):
        try:
            fields = json.loads(fields)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("fields must be a JSON array of {field, value, op?, precision?}") from exc
    if fields is not None and not isinstance(fields, list):
        fields = None
    if fields:
        bad_fields = [field.get("field", "") for field in fields if str(field.get("field", "SU")).upper() not in ADVSEARCH_FIELD_CODES]
        if bad_fields:
            raise ValueError(f"unknown field codes: {bad_fields}; valid: {sorted(ADVSEARCH_FIELD_CODES)}")
        if not (query or "").strip() and not any(str(field.get("value", "")).strip() for field in fields):
            raise ValueError("query must not be empty, and at least one --fields entry must have a non-empty value")

    return {
        "query": query,
        "language": language,
        "search_mode": search_mode,
        "extra": extra or {},
        "doc_type": doc_type,
        "discipline": resolved_discipline,
        "quality": quality,
        "elite_uni": elite_uni,
        "sort": sort,
        "date_from": date_from or None,
        "date_to": date_to or None,
        "fields": fields or None,
        "form_filters": form_filters,
    }


def _signature_for(search_inputs: dict[str, Any], label="", topic="", page_size=20) -> tuple[str, str]:
    payload = {**search_inputs, "label": label or "", "topic": topic or "", "page_size": int(page_size)}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return digest, text


def _search_spec(search_inputs: dict[str, Any], page_limit: int, page_size: int) -> SearchSpec:
    return SearchSpec(
        query=search_inputs["query"],
        language=search_inputs["language"],
        page_limit=max(1, int(page_limit)),
        search_mode=search_inputs["search_mode"],
        extra=search_inputs["extra"],
        doc_type=search_inputs["doc_type"],
        discipline=search_inputs["discipline"],
        quality=search_inputs["quality"],
        elite_uni=search_inputs["elite_uni"],
        sort=search_inputs["sort"],
        date_from=search_inputs["date_from"],
        date_to=search_inputs["date_to"],
        fields=search_inputs["fields"],
        form_filters=search_inputs["form_filters"],
        page_size=page_size,
    )


def search_action(
    query, language="zh", page_limit=1, return_fields=None, store=None,
    search_mode="advsearch", extra=None, doc_type="all", discipline=None, quality=None,
    subdiscipline=None, elite_uni=None, sort=None, date_from=None, date_to=None, fields=None, form_filters=None,
    workspace_id=None, run_id=None, label="", topic="", page=1, pages=None, refresh=False, activate=True,
    output_limit=None, debug=False,
):
    store = _store(store)
    try:
        requested_pages = _parse_page_range(page=page, pages=pages)
        search_inputs = _normalize_search_inputs(
            query, language, search_mode, extra, doc_type, discipline, quality, subdiscipline,
            elite_uni, sort, date_from, date_to, fields, form_filters,
        )
        page_size = max(1, int(os.environ.get("CNKI_HTTP_PAGE_SIZE", "20")))
        signature, signature_source = _signature_for(search_inputs, label=label, topic=topic, page_size=page_size)
        workspace = store.resolve_workspace(workspace_id, create=True)
        workspace_id = workspace["workspace_id"]
        resolved_run_id = run_id or f"run-{signature[:12]}"
    except (ValueError, WorkspaceStoreError) as exc:
        if isinstance(exc, WorkspaceStoreError):
            return _store_error_payload("search", exc)
        return _error_payload("search", "invalid_arguments", str(exc), workspace_id=workspace_id or "", run_id=run_id or "")

    try:
        with store.with_run_lock(workspace_id, resolved_run_id):
            existing = None
            if os.path.exists(store.run_path(workspace_id, resolved_run_id)):
                existing = store.load_run(workspace_id, resolved_run_id)
                if existing.get("signature") != signature:
                    return _error_payload("search", "run_signature_mismatch", "explicit --run belongs to a different search signature", workspace_id, resolved_run_id)

            loaded_pages = set(int(page_no) for page_no in (existing or {}).get("pages_loaded", []) if int(page_no) > 0)
            needs_fetch = refresh or existing is None or not set(requested_pages).issubset(loaded_pages)
            cache_status = "hit"
            warnings: list[dict[str, Any]] = []
            results: list[dict[str, Any]]
            run: dict[str, Any]

            if needs_fetch:
                fetch_limit = max(requested_pages + list(loaded_pages or [1]))
                if refresh:
                    cache_status = "refresh"
                elif existing is None:
                    cache_status = "miss"
                else:
                    cache_status = "partial"
                try:
                    payload = run_http_advsearch_workflow(_search_spec(search_inputs, fetch_limit, page_size))
                except HttpSearchUnavailable as exc:
                    return _error_payload("search", exc.code, exc.detail, workspace_id, resolved_run_id, **exc.extra)
                new_results = _rows_from_pages(payload)
                results = _merge_search_rows(store.load_results(workspace_id, resolved_run_id) if existing else [], new_results, refresh=bool(refresh))
                loaded = sorted({int(row.get("page_no", 0) or 0) for row in results if int(row.get("page_no", 0) or 0) > 0})
                now = utc_now()
                run = {
                    "schema_version": 2,
                    "workspace_id": workspace_id,
                    "run_id": resolved_run_id,
                    "signature": signature,
                    "signature_source": signature_source,
                    "query": search_inputs["query"],
                    "label": label or "",
                    "topic": topic or "",
                    **search_inputs,
                    "page_size": page_size,
                    "status": "ok",
                    "result_count": _parse_int(payload.get("total"), len(results)),
                    "pages_loaded": loaded,
                    "page_markers": {str(page.get("page_no")): page.get("page", "") for page in payload.get("pages", [])},
                    "no_results_message": payload.get("noResultsMessage", ""),
                    "search_transport": payload.get("transport", "http"),
                    "created_at": (existing or {}).get("created_at", now),
                    "updated_at": now,
                    "last_action": "search",
                }
                store.refresh_run(
                    workspace_id,
                    run,
                    results,
                    details={} if refresh else store.load_details(workspace_id, resolved_run_id),
                    artifacts={} if refresh else store.load_artifacts(workspace_id, resolved_run_id),
                )
                for page_payload in payload.get("pages", []):
                    store.append_event(workspace_id, resolved_run_id, "search_page_fetched", page_no=page_payload.get("page_no"), rows=len(page_payload.get("results", [])))
                store.append_event(workspace_id, resolved_run_id, "search_completed", cache_status=cache_status, result_count=run["result_count"])
                warnings = payload.get("warnings", [])
            else:
                run = existing or store.load_run(workspace_id, resolved_run_id)
                results = store.load_results(workspace_id, resolved_run_id)

            if activate:
                store.set_active_run(workspace_id, resolved_run_id)
            workspace = store.load_workspace(workspace_id)
            fields_out = resolve_return_fields(return_fields, "search_basic")
            selected_rows = _rows_for_pages(results, requested_pages)
            if output_limit is not None and int(output_limit) > 0:
                selected_rows = selected_rows[: int(output_limit)]
            projected = [project_row(row, fields_out) for row in selected_rows]
            clean_fields, clean_rows = strip_url_fields(fields_out, projected, debug=debug)
            has_more = bool(run.get("result_count", 0) and max(requested_pages) * int(run.get("page_size", page_size)) < int(run.get("result_count", 0) or 0))
            summary = _summary_from_run(workspace, run, {
                "page": requested_pages[0] if len(requested_pages) == 1 else None,
                "pages_returned": requested_pages,
                "returned_count": len(clean_rows),
                "has_more": has_more,
                "cache_status": cache_status,
            })
            return _success_payload(
                "search",
                workspace_id,
                resolved_run_id,
                len(clean_rows),
                summary,
                clean_rows,
                clean_fields,
                warnings=warnings,
                cache_status=cache_status,
                page=requested_pages[0] if len(requested_pages) == 1 else None,
                pages_returned=requested_pages,
                pages_loaded=run.get("pages_loaded", []),
                result_count=run.get("result_count", 0),
                returned_count=len(clean_rows),
                has_more=has_more,
            )
    except WorkspaceLockTimeout as exc:
        return _store_error_payload("search", exc)


def _resolve_run_context(action: str, store: WorkspaceStore, workspace_id: str | None, run_id: str | None):
    workspace = store.resolve_workspace(workspace_id, create=False)
    resolved_run_id = store.resolve_run_id(workspace["workspace_id"], run_id)
    run = store.load_run(workspace["workspace_id"], resolved_run_id)
    return workspace, run


def _target_numbers(results, rows=None, top=None, pending_only=False, status_field="", complete_value="ok", sample=None):
    if rows is not None:
        numbers = sorted(set(int(n) for n in rows))
    elif pending_only and status_field:
        numbers = [int(row["global_rank"]) for row in results if row.get(status_field) != complete_value]
    elif top is not None and int(top) > 0:
        numbers = [int(row["global_rank"]) for row in results[: int(top)]]
    else:
        raise ValueError("specify at least one of: --rows, --top, or --pending-only")
    if top is not None and int(top) > 0:
        numbers = numbers[: int(top)]
    if sample is not None and int(sample) > 0:
        rng = random.Random(12345)
        count = min(int(sample), len(numbers))
        numbers = rng.sample(numbers, count) if count < len(numbers) else numbers
    return numbers


def _fetch_details_for_selected(store: WorkspaceStore, workspace_id: str, run_id: str, results: list[dict[str, Any]],
                                selected_rows: list[dict[str, Any]], refresh_existing=False):
    details = store.load_details(workspace_id, run_id)
    rows_to_fetch = []
    for row in selected_rows:
        if row.get("detail_status") == "ok" and row.get("detail_ref") and not refresh_existing:
            continue
        rows_to_fetch.append({"row_id": row["row_id"], "global_rank": row["global_rank"], "detail_url": row.get("detail_url", "")})

    warnings = []
    returned_items: dict[int, dict[str, Any]] = {}
    if rows_to_fetch:
        try:
            http_items = fetch_detail_rows(rows_to_fetch)
            store.append_event(workspace_id, run_id, "http_fetch_details_completed", rows=len(http_items), ok=sum(1 for item in http_items if item.get("status") == "ok"))
        except HttpDetailUnavailable as exc:
            store.append_event(workspace_id, run_id, "http_fetch_details_failed", error=exc.code, detail=exc.detail, **exc.extra)
            http_items = [{**item, "status": "error", "error": exc.code, "detail": exc.detail, "attempts": 0} for item in rows_to_fetch]
        returned_items = {int(item.get("global_rank", 0) or 0): item for item in http_items}

    result_index = {int(row.get("global_rank", 0) or 0): row for row in results}
    new_details = {}
    for row in selected_rows:
        if row.get("detail_status") == "ok" and row.get("detail_ref") and not refresh_existing:
            continue
        rank = int(row["global_rank"])
        item = returned_items.get(rank)
        target = result_index.get(rank, row)
        if not item:
            target["detail_status"] = "error"
            target["detail_error"] = "detail_result_missing"
            warnings.append({"row": rank, "error": "detail_result_missing"})
            continue
        if item.get("status") == "error":
            target["detail_status"] = "error"
            target["detail_error"] = item.get("error", "unknown")
            warnings.append({"row": rank, "error": item.get("error", "unknown"), "detail": item.get("detail", "")})
            continue
        detail_ref, detail_record = _detail_record_from_payload(target, item)
        new_details[detail_ref] = detail_record
        target["detail_status"] = "ok"
        target["detail_ref"] = detail_ref
        target["detail_error"] = ""
        for link_field in ("pdf_url", "caj_url", "download_url"):
            if item.get(link_field):
                target[link_field] = item.get(link_field)
        store.append_event(workspace_id, run_id, "detail_fetch_succeeded", row=rank, detail_ref=detail_ref)

    details.update(new_details)
    store.save_results(workspace_id, run_id, results)
    store.save_details(workspace_id, run_id, details)
    return results, details, warnings


def fetch_details_action(workspace_id=None, run_id=None, rows=None, top=None, pending_only=False, sample=None,
                         return_fields=None, refresh_existing=False, store=None, debug=False):
    store = _store(store)
    try:
        workspace, run = _resolve_run_context("fetch_details", store, workspace_id, run_id)
        workspace_id, run_id = workspace["workspace_id"], run["run_id"]
        with store.with_run_lock(workspace_id, run_id):
            results = store.load_results(workspace_id, run_id)
            target_numbers = _target_numbers(results, rows=rows, top=top, pending_only=pending_only, status_field="detail_status", complete_value="ok", sample=sample)
            selected_rows, missing = _select_rows(results, target_numbers)
            if missing:
                return _error_payload("fetch_details", "rows_not_found", "some requested rows do not exist in the run result table", workspace_id, run_id, missing_rows=missing)
            store.append_event(workspace_id, run_id, "fetch_details_started", rows=target_numbers, pending_only=bool(pending_only), refresh_existing=bool(refresh_existing))
            results, details, warnings = _fetch_details_for_selected(store, workspace_id, run_id, results, selected_rows, refresh_existing=refresh_existing)
            run["status"] = "ok"
            run["last_action"] = "fetch_details"
            store.save_run(run)
            store.append_event(workspace_id, run_id, "fetch_details_completed", warnings=len(warnings))
            fields_out = resolve_return_fields(return_fields, "detail_basic")
            projected = []
            for row in selected_rows:
                current = next((item for item in results if int(item.get("global_rank", 0)) == int(row.get("global_rank", 0))), row)
                detail = details.get(current.get("detail_ref", "")) if current.get("detail_ref") else None
                projected.append(project_row(merge_row_with_detail(current, detail), fields_out))
            clean_fields, clean_rows = strip_url_fields(fields_out, projected, debug=debug)
            return _success_payload("fetch_details", workspace_id, run_id, len(clean_rows), _summary_from_run(workspace, run), clean_rows, clean_fields, warnings=warnings, rows_processed=target_numbers)
    except WorkspaceStoreError as exc:
        return _store_error_payload("fetch_details", exc)
    except ValueError as exc:
        return _error_payload("fetch_details", "invalid_arguments", str(exc), workspace_id or "", run_id or "")


def download_action(workspace_id=None, run_id=None, rows=None, top=None, pending_only=False, sample=None,
                    fmt="pdf", download_dir=None, return_fields=None, redownload=False,
                    direct_concurrency=None, store=None, debug=False):
    store = _store(store)
    fmt = (fmt or "pdf").lower()
    if fmt not in VALID_DOWNLOAD_FORMATS:
        return _error_payload("download", "invalid_format", f"format must be one of: {', '.join(sorted(VALID_DOWNLOAD_FORMATS))}", workspace_id or "", run_id or "")
    download_dir = _resolve_download_dir(download_dir, fmt=fmt)
    try:
        os.makedirs(str(download_dir), exist_ok=True)
    except OSError as exc:
        return _error_payload("download", "invalid_download_dir", f"cannot create or access download directory: {exc}", workspace_id or "", run_id or "", dir=download_dir)

    try:
        workspace, run = _resolve_run_context("download", store, workspace_id, run_id)
        workspace_id, run_id = workspace["workspace_id"], run["run_id"]
        with store.with_run_lock(workspace_id, run_id):
            results = store.load_results(workspace_id, run_id)
            target_numbers = _target_numbers(results, rows=rows, top=top, pending_only=pending_only, status_field="download_status", complete_value="downloaded", sample=sample)
            if pending_only and not redownload:
                target_numbers = [number for number in target_numbers if next((row for row in results if int(row.get("global_rank", 0)) == number and row.get("download_status") != "downloaded"), None)]
            if not target_numbers:
                return _error_payload("download", "no_rows_to_download", "after applying filters, no rows remain to download", workspace_id, run_id)
            selected_rows, missing = _select_rows(results, target_numbers)
            if missing:
                return _error_payload("download", "rows_not_found", "some requested rows do not exist in the run result table", workspace_id, run_id, missing_rows=missing)

            for row in selected_rows:
                if row.get("download_status") == "downloaded" and row.get("download_path") and not os.path.exists(str(row.get("download_path"))) and not redownload:
                    row["download_status"] = "pending"
                    row["download_error"] = "download_file_missing"

            need_detail = [
                row for row in selected_rows
                if not (((row.get("caj_url") or row.get("download_url")) if fmt == "caj" else row.get("pdf_url"))) and row.get("detail_url")
            ]
            if need_detail:
                results, _details, detail_warnings = _fetch_details_for_selected(store, workspace_id, run_id, results, need_detail, refresh_existing=False)
            else:
                detail_warnings = []

            result_index = {int(row.get("global_rank", 0) or 0): row for row in results}
            direct_rows = []
            output_by_rank = {}
            warnings = list(detail_warnings)
            for row in selected_rows:
                current = result_index.get(int(row.get("global_rank", 0) or 0), row)
                if current.get("download_status") == "downloaded" and current.get("download_path") and os.path.exists(str(current.get("download_path"))) and not redownload:
                    output_by_rank[int(current["global_rank"])] = {
                        "row_id": current.get("row_id", ""),
                        "global_rank": current.get("global_rank", 0),
                        "title": current.get("title", ""),
                        "download_status": "downloaded",
                        "download_format": current.get("download_format", fmt.upper()),
                        "saved_to": current.get("download_path", ""),
                        "filename": os.path.basename(str(current.get("download_path", ""))),
                        "download_error": "",
                    }
                    continue
                direct_url = (current.get("caj_url") or current.get("download_url")) if fmt == "caj" else current.get("pdf_url")
                if not direct_url:
                    item = {
                        "row_id": current.get("row_id", ""),
                        "global_rank": current.get("global_rank", 0),
                        "title": current.get("title", ""),
                        "download_status": "error",
                        "download_format": fmt.upper(),
                        "download_error": "direct_url_missing",
                        "saved_to": "",
                        "filename": "",
                    }
                    output_by_rank[int(current.get("global_rank", 0) or 0)] = item
                    warnings.append({"row": item["global_rank"], "error": "direct_url_missing"})
                    continue
                direct_rows.append(current)

            direct_items = download_direct_rows(direct_rows, fmt=fmt, download_dir=download_dir, concurrency=direct_concurrency) if direct_rows else []
            for row, item in zip(direct_rows, direct_items):
                rank = int(row.get("global_rank", 0) or 0)
                if item.get("status") == "downloaded":
                    row["download_status"] = "downloaded"
                    row["download_path"] = item.get("saved_to", "")
                    row["download_format"] = item.get("format", fmt.upper())
                    row["download_error"] = ""
                    row["download_transport"] = item.get("download_transport", "http_direct")
                    row["download_strategy"] = item.get("download_strategy", "")
                    output_by_rank[rank] = {
                        "row_id": row.get("row_id", ""),
                        "global_rank": rank,
                        "title": row.get("title", ""),
                        "download_status": "downloaded",
                        "download_format": item.get("format", fmt.upper()),
                        "saved_to": item.get("saved_to", ""),
                        "filename": item.get("filename", ""),
                        "download_error": "",
                        "download_transport": item.get("download_transport", "http_direct"),
                        "download_strategy": item.get("download_strategy", ""),
                    }
                else:
                    row["download_status"] = "error"
                    row["download_format"] = item.get("format", fmt.upper())
                    row["download_error"] = item.get("error", "unknown")
                    warnings.append({"row": rank, "error": row["download_error"], "detail": item.get("detail", "")})
                    output_by_rank[rank] = {
                        "row_id": row.get("row_id", ""),
                        "global_rank": rank,
                        "title": row.get("title", ""),
                        "download_status": "error",
                        "download_format": item.get("format", fmt.upper()),
                        "saved_to": item.get("saved_to", ""),
                        "filename": item.get("filename", ""),
                        "download_error": row["download_error"],
                    }
            store.save_results(workspace_id, run_id, results)
            artifacts = {str(rank): output_by_rank[rank] for rank in output_by_rank}
            store.merge_artifacts(workspace_id, run_id, "download", artifacts)
            run["last_action"] = "download"
            store.save_run(run)
            store.append_event(workspace_id, run_id, "download_completed", rows=target_numbers, warnings=len(warnings))
            output_rows = [output_by_rank.get(int(row.get("global_rank", 0) or 0), {}) for row in selected_rows]
            fields_out = resolve_return_fields(return_fields, "download_basic")
            projected = [project_row(row, fields_out) for row in output_rows]
            clean_fields, clean_rows = strip_url_fields(fields_out, projected, debug=debug)
            return _success_payload("download", workspace_id, run_id, len(clean_rows), _summary_from_run(workspace, run), clean_rows, clean_fields, warnings=warnings, rows_processed=target_numbers)
    except WorkspaceStoreError as exc:
        return _store_error_payload("download", exc)
    except ValueError as exc:
        return _error_payload("download", "invalid_arguments", str(exc), workspace_id or "", run_id or "")


def export_action(workspace_id=None, run_id=None, rows=None, top=None, sample=None, modes=None, file_type="txt",
                  return_fields=None, refresh_existing=False, store=None, debug=False):
    store = _store(store)
    try:
        workspace, run = _resolve_run_context("export", store, workspace_id, run_id)
        workspace_id, run_id = workspace["workspace_id"], run["run_id"]
        with store.with_run_lock(workspace_id, run_id):
            results = store.load_results(workspace_id, run_id)
            target_numbers = _target_numbers(results, rows=rows, top=top, sample=sample)
            selected_rows, missing = _select_rows(results, target_numbers)
            if missing:
                return _error_payload("export", "rows_not_found", "some requested rows do not exist in the run result table", workspace_id, run_id, missing_rows=missing)
            modes = modes or ["GBTREFER", "MLA", "APA"]
            artifact_key = f"{file_type}:{','.join(modes)}"
            artifacts = store.load_artifacts(workspace_id, run_id).get("export", {})
            output_rows = []
            rows_to_export = []
            for row in selected_rows:
                cached = artifacts.get(str(row.get("global_rank"))) if isinstance(artifacts, dict) else None
                if cached and cached.get("artifact_key") == artifact_key and not refresh_existing:
                    output_rows.append(cached.get("payload", {}))
                else:
                    rows_to_export.append(row)
            warnings = []
            if rows_to_export:
                try:
                    exported = export_rows(rows_to_export, modes=modes, file_type=file_type)
                except HttpExportUnavailable as exc:
                    return _error_payload("export", exc.code, exc.detail, workspace_id, run_id, **exc.extra)
                for row in exported:
                    output_rows.append(row)
                    artifacts[str(row.get("global_rank"))] = {"artifact_key": artifact_key, "payload": row, "updated_at": utc_now()}
                    if row.get("export_status") != "ok":
                        warnings.append({"row": row.get("global_rank"), "error": row.get("export_error", "unknown"), "mode_errors": row.get("mode_errors", {})})
                store.merge_artifacts(workspace_id, run_id, "export", artifacts)
            run["last_action"] = "export"
            store.save_run(run)
            store.append_event(workspace_id, run_id, "export_completed", rows=target_numbers, warnings=len(warnings))
            output_rows = sorted(output_rows, key=lambda row: int(row.get("global_rank", 0) or 0))
            fields_out = resolve_return_fields(return_fields, "export_basic")
            projected = [project_row(row, fields_out) for row in output_rows]
            clean_fields, clean_rows = strip_url_fields(fields_out, projected, debug=debug)
            return _success_payload("export", workspace_id, run_id, len(clean_rows), _summary_from_run(workspace, run), clean_rows, clean_fields, warnings=warnings, rows_processed=target_numbers)
    except WorkspaceStoreError as exc:
        return _store_error_payload("export", exc)
    except ValueError as exc:
        return _error_payload("export", "invalid_arguments", str(exc), workspace_id or "", run_id or "")


def discover_facets_action(workspace_id=None, run_id=None, group="subdiscipline", store=None, debug=False):
    store = _store(store)
    if group not in VALID_FACET_GROUPS:
        return _error_payload("discover_facets", "invalid_facet_group", f"facet group must be one of: {', '.join(sorted(VALID_FACET_GROUPS))}", workspace_id or "", run_id or "")
    try:
        workspace, run = _resolve_run_context("discover_facets", store, workspace_id, run_id)
        workspace_id, run_id = workspace["workspace_id"], run["run_id"]
        spec = _search_spec({
            "query": run.get("query", ""),
            "language": run.get("language", "zh"),
            "search_mode": run.get("search_mode", "advsearch"),
            "extra": run.get("extra") or {},
            "doc_type": run.get("doc_type", "all"),
            "discipline": run.get("discipline") or [],
            "quality": run.get("quality") or [],
            "elite_uni": run.get("elite_uni"),
            "sort": run.get("sort", "date"),
            "date_from": run.get("date_from"),
            "date_to": run.get("date_to"),
            "fields": run.get("fields"),
            "form_filters": run.get("form_filters") or [],
        }, page_limit=1, page_size=int(run.get("page_size", 20) or 20))
        payload = run_http_facets(spec, group=group)
        summary = _summary_from_run(workspace, run, {"facet_group": payload.get("facet_group", group), "facet_item_count": len(payload.get("items", []))})
        return _success_payload("discover_facets", workspace_id, run_id, len(payload.get("items", [])), summary, payload.get("items", []), FACET_RETURN_FIELDS, warnings=payload.get("warnings", []))
    except WorkspaceStoreError as exc:
        return _store_error_payload("discover_facets", exc)
    except HttpFacetUnavailable as exc:
        return _error_payload("discover_facets", exc.code, exc.detail, workspace_id or "", run_id or "", **exc.extra)


def inspect_action(workspace_id=None, run_id=None, rows=None, return_fields=None, view="rows", page=None, store=None, debug=False):
    store = _store(store)
    try:
        workspace = store.resolve_workspace(workspace_id, create=False)
        workspace_id = workspace["workspace_id"]
        if view == "summary":
            return _success_payload("inspect", workspace_id, "", len(workspace.get("runs", {})), {
                "workspace_id": workspace_id,
                "created_at": workspace.get("created_at", ""),
                "expires_at": workspace.get("expires_at", ""),
                "active_run_id": workspace.get("active_run_id", ""),
                "run_count": len(workspace.get("runs", {})),
            }, [], [], warnings=[])
        if view == "runs":
            run_rows = list((workspace.get("runs") or {}).values())
            return _success_payload("inspect", workspace_id, "", len(run_rows), {
                "workspace_id": workspace_id,
                "active_run_id": workspace.get("active_run_id", ""),
                "run_count": len(run_rows),
            }, run_rows, ["run_id", "query", "label", "topic", "status", "result_count", "pages_loaded", "updated_at"], warnings=[])
        resolved_run_id = store.resolve_run_id(workspace_id, run_id)
        run = store.load_run(workspace_id, resolved_run_id)
        results = store.load_results(workspace_id, resolved_run_id)
        details = store.load_details(workspace_id, resolved_run_id)
        if rows:
            selected_rows, missing = _select_rows(results, rows)
            if missing:
                return _error_payload("inspect", "rows_not_found", "some requested rows do not exist in the run result table", workspace_id, resolved_run_id, missing_rows=missing)
        elif page:
            selected_rows = _rows_for_pages(results, [int(page)])
        else:
            selected_rows = list(results)
        fields_out = resolve_return_fields(return_fields, "search_basic")
        projected = []
        for row in selected_rows:
            detail = details.get(row.get("detail_ref", "")) if row.get("detail_ref") else None
            projected.append(project_row(merge_row_with_detail(row, detail), fields_out))
        clean_fields, clean_rows = strip_url_fields(fields_out, projected, debug=debug)
        return _success_payload("inspect", workspace_id, resolved_run_id, len(clean_rows), _summary_from_run(workspace, run), clean_rows, clean_fields, warnings=[])
    except WorkspaceStoreError as exc:
        return _store_error_payload("inspect", exc)
