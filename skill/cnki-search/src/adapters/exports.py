"""Direct HTTP export/citation transport for cnki-search.

This module models CNKI's document-management export endpoints. It is
stdlib-only and intentionally separate from search so citation/export stays a
direct HTTP/interface action.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from html import unescape
from typing import Any

from core.http import _load_cookie_seed

GET_EXPORT_URL = "https://kns.cnki.net/dm8/API/GetExport"
FILE_TO_TEXT_URL = "https://kns.cnki.net/dm8/manage/FileToText"
ENDNOTE_FILE_TO_TEXT_URL = "https://kns.cnki.net/dm8/manage/FileToText.enw"
EXPORT_REFERER = "https://kns.cnki.net/dm8/manage/export.html"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Safari/537.36"
)

QUICK_CITATION_MODES = ("GBTREFER", "MLA", "APA")
FILE_EXPORT_MODES = ("REFER", "NEW", "EndNote", "NoteExpress", "NodeFirst", "BibTex", "Refworks")
DEFAULT_MODES = QUICK_CITATION_MODES
MODE_ALIASES = {
    "gbt": "GBTREFER",
    "gbt7714": "GBTREFER",
    "gbtrefer": "GBTREFER",
    "mla": "MLA",
    "apa": "APA",
    "refer": "REFER",
    "cajcd": "REFER",
    "new": "NEW",
    "endnote": "EndNote",
    "noteexpress": "NoteExpress",
    "notefirst": "NodeFirst",
    "nodefirst": "NodeFirst",
    "bibtex": "BibTex",
    "refworks": "Refworks",
}

LOGIN_RE = re.compile(r"未登录|请登录|登录后|用户登录|login\.cnki", re.I)
CAPTCHA_RE = re.compile(r"tcaptcha|captcha\.qq\.com|验证码|安全验证|人机验证|拖动滑块", re.I)
NO_RESULTS_RE = re.compile(r"暂无数据|未找到|没有找到|抱歉，暂无数据", re.I)


class HttpExportUnavailable(Exception):
    def __init__(self, code: str, detail: str = "", **extra: Any):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code
        self.extra = extra


@dataclass
class HttpResponse:
    url: str
    status: int
    headers: dict[str, str]
    body: str
    elapsed_ms: int
    error: str = ""


def strip_html(value: str) -> str:
    value = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", value or "")
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return re.sub(r"[ \t\r\f\v]+", " ", unescape(value)).strip()


def normalize_modes(modes: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if not modes:
        modes = list(DEFAULT_MODES)
    if isinstance(modes, str):
        raw_tokens = re.split(r"[,，\s]+", modes)
    else:
        raw_tokens = []
        for item in modes:
            raw_tokens.extend(re.split(r"[,，\s]+", str(item or "")))
    result = []
    for token in raw_tokens:
        key = token.strip()
        if not key:
            continue
        canonical = MODE_ALIASES.get(key.lower(), key)
        if canonical not in QUICK_CITATION_MODES and canonical not in FILE_EXPORT_MODES:
            raise HttpExportUnavailable("unsupported_export_mode", f"unsupported export mode: {token}", mode=token)
        if canonical not in result:
            result.append(canonical)
    return result or list(DEFAULT_MODES)


def detect_state(response: HttpResponse) -> str:
    text = (response.body or "")[:250000]
    if response.error and response.status == 0:
        return "network_error"
    if response.status == 429:
        return "rate_limited"
    if response.status in {401, 302} and LOGIN_RE.search(text + response.url):
        return "login_required"
    if response.status == 403:
        return "forbidden"
    if CAPTCHA_RE.search(text + response.url):
        return "captcha"
    if LOGIN_RE.search(text + response.url):
        return "login_required"
    if NO_RESULTS_RE.search(text):
        return "no_results"
    if response.status >= 400:
        return "http_error"
    return "ok"


def _load_http_cookie() -> tuple[str, str]:
    try:
        cookie, source = _load_cookie_seed()
    except Exception as exc:  # noqa: BLE001
        raise HttpExportUnavailable("cookie_seed_failed", str(exc)) from exc
    cookie = str(cookie or "").strip()
    if not cookie:
        raise HttpExportUnavailable("cookie_unavailable", f"no HTTP cookie available from {source or 'configured sources'}")
    return cookie, source or "unknown"


def _request(method: str, url: str, cookie: str, form: dict[str, str], timeout: float, referer: str = EXPORT_REFERER) -> HttpResponse:
    data = urllib.parse.urlencode(form).encode("utf-8")
    headers = {
        "User-Agent": os.environ.get("CNKI_USER_AGENT", DEFAULT_USER_AGENT),
        "Referer": referer,
        "Origin": "https://kns.cnki.net",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, identity",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Cookie": cookie,
    }
    started = time.monotonic()
    request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            raw = response.read()
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                raw = gzip.decompress(raw)
            return HttpResponse(
                url=response.geturl(),
                status=int(response.status),
                headers={k: v for k, v in response.headers.items()},
                body=raw.decode("utf-8", errors="replace"),
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        if exc.headers.get("Content-Encoding", "").lower() == "gzip":
            raw = gzip.decompress(raw)
        return HttpResponse(
            url=exc.geturl(),
            status=int(exc.code),
            headers={k: v for k, v in exc.headers.items()},
            body=raw.decode("utf-8", errors="replace"),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error=str(exc),
        )
    except Exception as exc:  # noqa: BLE001
        return HttpResponse(url=url, status=0, headers={}, body="", elapsed_ms=int((time.monotonic() - started) * 1000), error=f"{type(exc).__name__}: {exc}")


def _parse_get_export(body: str) -> dict[str, str]:
    exports = _parse_get_export_values(body)
    return {mode: "\n".join(values).strip() for mode, values in exports.items()}


def _parse_get_export_values(body: str) -> dict[str, list[str]]:
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise HttpExportUnavailable("export_json_parse_failed", str(exc)) from exc
    if payload.get("code") != 1:
        raise HttpExportUnavailable("export_no_data", str(payload.get("msg") or "export returned no data"), response_code=payload.get("code"))
    exports: dict[str, list[str]] = {}
    for item in payload.get("data") or []:
        mode = str(item.get("mode") or "").strip()
        values = item.get("value") or []
        if mode and isinstance(values, list):
            exports[mode] = [strip_html(str(value)) for value in values if str(value).strip()]
    return exports


def _quick_citation_exports(export_id: str, modes: list[str], cookie: str, timeout: float) -> dict[str, str]:
    requested = [mode for mode in modes if mode in QUICK_CITATION_MODES]
    if not requested:
        return {}
    # CNKI reliably returns MLA/APA when requested with the default three-mode bundle;
    # single MLA/APA requests can return "暂无数据".
    form = {
        "filename": export_id,
        "displaymode": ",".join(QUICK_CITATION_MODES),
        "uniplatform": "NZKPT",
        "subject": "",
        "language": "CHS",
    }
    response = _request("POST", GET_EXPORT_URL, cookie, form, timeout, EXPORT_REFERER)
    state = detect_state(response)
    if state != "ok":
        raise HttpExportUnavailable(f"export_{state}", f"GetExport returned state {state}", http_status=response.status)
    exports = _parse_get_export(response.body)
    return {mode: exports[mode] for mode in requested if mode in exports}


def _file_text_export(export_id: str, mode: str, cookie: str, timeout: float, file_type: str = "txt") -> str:
    endpoint = ENDNOTE_FILE_TO_TEXT_URL if mode == "EndNote" else FILE_TO_TEXT_URL
    form = {
        "FileName": export_id,
        "DisplayMode": mode,
        "OrderParam": "0",
        "OrderType": "desc",
        "SelectField": "",
        "PageIndex": "1",
        "PageSize": "20",
        "language": "CHS",
        "uniplatform": "NZKPT",
        "subject": "",
        "Type": file_type,
    }
    referer = f"{EXPORT_REFERER}?displaymode={urllib.parse.quote(mode)}&uniplatform=NZKPT&language=CHS"
    response = _request("POST", endpoint, cookie, form, timeout, referer)
    state = detect_state(response)
    if state != "ok":
        raise HttpExportUnavailable(f"export_file_{state}", f"FileToText returned state {state}", mode=mode, http_status=response.status)
    text = strip_html(response.body)
    if not text:
        raise HttpExportUnavailable("export_file_empty", f"FileToText returned empty body for {mode}", mode=mode)
    return text


def export_row(row: dict[str, Any], modes: list[str] | tuple[str, ...] | str | None = None, file_type: str = "txt", cookie: str | None = None, timeout: float | None = None) -> dict[str, Any]:
    export_id = str(row.get("export_id") or "").strip()
    normalized_modes = normalize_modes(modes)
    base = {
        "row_id": row.get("row_id", ""),
        "global_rank": row.get("global_rank", ""),
        "title": row.get("title", ""),
        "export_modes": normalized_modes,
        "export_transport": "http",
        "exports": {},
        "mode_errors": {},
    }
    if not export_id:
        return {**base, "export_status": "error", "export_error": "missing_export_id"}
    if cookie is None:
        cookie, _source = _load_http_cookie()
    timeout = float(timeout if timeout is not None else os.environ.get("CNKI_HTTP_TIMEOUT", "25"))

    exports: dict[str, str] = {}
    mode_errors: dict[str, str] = {}
    try:
        exports.update(_quick_citation_exports(export_id, normalized_modes, cookie, timeout))
    except HttpExportUnavailable as exc:
        for mode in normalized_modes:
            if mode in QUICK_CITATION_MODES:
                mode_errors[mode] = exc.code

    for mode in normalized_modes:
        if mode in QUICK_CITATION_MODES:
            continue
        try:
            exports[mode] = _file_text_export(export_id, mode, cookie, timeout, file_type=file_type)
        except HttpExportUnavailable as exc:
            mode_errors[mode] = exc.code

    if not exports:
        return {**base, "export_status": "error", "export_error": "export_failed", "mode_errors": mode_errors}
    return {**base, "export_status": "ok", "export_error": "", "exports": exports, "mode_errors": mode_errors}


def _match_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").lower(), flags=re.UNICODE)


def _batch_quick_citation_rows(rows: list[dict[str, Any]], modes: list[str], cookie: str, timeout: float) -> dict[int, dict[str, Any]]:
    requested = [mode for mode in modes if mode in QUICK_CITATION_MODES]
    if not requested or len(rows) <= 1:
        return {}
    indexed = [(index, row, str(row.get("export_id") or "").strip()) for index, row in enumerate(rows)]
    indexed = [(index, row, export_id) for index, row, export_id in indexed if export_id]
    if len(indexed) <= 1:
        return {}
    form = {
        "filename": ",".join(export_id for _index, _row, export_id in indexed),
        "displaymode": ",".join(QUICK_CITATION_MODES),
        "uniplatform": "NZKPT",
        "subject": "",
        "language": "CHS",
    }
    response = _request("POST", GET_EXPORT_URL, cookie, form, timeout, EXPORT_REFERER)
    state = detect_state(response)
    if state != "ok":
        return {}
    try:
        values_by_mode = _parse_get_export_values(response.body)
    except HttpExportUnavailable:
        return {}

    used: dict[str, set[int]] = {mode: set() for mode in requested}
    assigned: dict[int, dict[str, str]] = {index: {} for index, _row, _export_id in indexed}
    for index, row, _export_id in indexed:
        title_key = _match_text(row.get("title", ""))
        if not title_key:
            continue
        for mode in requested:
            for value_index, value in enumerate(values_by_mode.get(mode, [])):
                if value_index in used[mode]:
                    continue
                if title_key and title_key in _match_text(value):
                    assigned[index][mode] = value
                    used[mode].add(value_index)
                    break

    rows_by_index: dict[int, dict[str, Any]] = {}
    for index, row, _export_id in indexed:
        exports = assigned.get(index) or {}
        if not all(mode in exports for mode in requested):
            continue
        rows_by_index[index] = {
            "row_id": row.get("row_id", ""),
            "global_rank": row.get("global_rank", ""),
            "title": row.get("title", ""),
            "export_modes": modes,
            "export_transport": "http",
            "exports": exports,
            "mode_errors": {},
            "export_status": "ok",
            "export_error": "",
            "export_batch": True,
        }
    return rows_by_index


def export_rows(rows: list[dict[str, Any]], modes: list[str] | tuple[str, ...] | str | None = None, file_type: str = "txt") -> list[dict[str, Any]]:
    cookie, _source = _load_http_cookie()
    timeout = float(os.environ.get("CNKI_HTTP_TIMEOUT", "25"))
    normalized_modes = normalize_modes(modes)
    if (
        os.environ.get("CNKI_EXPORT_BATCH_QUICK", "1").strip().lower() not in {"0", "false", "no", "off"}
        and rows
        and all(mode in QUICK_CITATION_MODES for mode in normalized_modes)
    ):
        batched = _batch_quick_citation_rows(rows, normalized_modes, cookie, timeout)
        if batched:
            output = []
            missing_indexes = []
            for index, row in enumerate(rows):
                if index in batched:
                    output.append(batched[index])
                else:
                    missing_indexes.append(index)
                    output.append(None)
            for index in missing_indexes:
                output[index] = export_row(rows[index], modes=normalized_modes, file_type=file_type, cookie=cookie, timeout=timeout)
            return [row for row in output if row is not None]

    concurrency = max(1, int(os.environ.get("CNKI_EXPORT_MAX_CONCURRENCY", "3")))
    if concurrency <= 1 or len(rows) <= 1:
        return [export_row(row, modes=normalized_modes, file_type=file_type, cookie=cookie, timeout=timeout) for row in rows]
    results: list[dict[str, Any] | None] = [None] * len(rows)
    with ThreadPoolExecutor(max_workers=min(concurrency, len(rows))) as executor:
        futures = {
            executor.submit(export_row, row, modes=normalized_modes, file_type=file_type, cookie=cookie, timeout=timeout): index
            for index, row in enumerate(rows)
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return [row for row in results if row is not None]


