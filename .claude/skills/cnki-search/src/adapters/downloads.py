"""Direct HTTP download helpers for CNKI paper order links."""

from __future__ import annotations

import os
import pathlib
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from core.http import _load_cookie_seed

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Safari/537.36"
)
DEFAULT_REFERER = "https://kns.cnki.net/"

CAPTCHA_RE = re.compile(r"tcaptcha|captcha\.qq\.com|验证码|安全验证|人机验证|拖动滑块", re.I)
LOGIN_RE = re.compile(r"未登录|请登录|登录后|用户登录|会员登录|login\.cnki", re.I)
PERMISSION_RE = re.compile(r"无权限|没有权限|权限不足|未订购|机构未购买|余额不足|forbidden|not authorized", re.I)
SOURCE_APP_INVALID_RE = re.compile(r"会话已经结束|请返回原页面刷新|服务端出现未知错误|ErrorMsg\.html", re.I)


@dataclass
class HttpDownloadResponse:
    status: int
    url: str
    headers: dict[str, str]
    body: bytes


class HttpDownloadUnavailable(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _direct_url(row: dict[str, Any], fmt: str) -> str:
    if fmt == "caj":
        return str(row.get("caj_url") or row.get("download_url") or "").strip()
    return str(row.get("pdf_url") or "").strip()


def _safe_filename(value: str, fallback: str) -> str:
    name = str(value or fallback or "cnki-download").strip()
    name = re.sub(r"\s*网络首发\s*$", "", name)
    name = re.sub(r"\s*MT翻译.*$", "", name)
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120] or "cnki-download"


def _extension(headers: dict[str, str], fmt: str) -> str:
    disposition = headers.get("Content-Disposition", "")
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition, re.I)
    if match:
        suffix = pathlib.Path(urllib.parse.unquote(match.group(1))).suffix.strip(".")
        if suffix:
            return suffix.lower()
    return "pdf" if fmt == "pdf" else "caj"


def _download_concurrency(value: int | None = None) -> int:
    if value is None:
        value = int(os.environ.get("CNKI_DOWNLOAD_MAX_CONCURRENCY", "4"))
    return max(1, int(value))


def _write_unique_bytes(target_dir: pathlib.Path, title: str, ext: str, suffix: Any, body: bytes) -> pathlib.Path:
    candidates = [target_dir / f"{title}.{ext}"]
    if suffix:
        candidates.append(target_dir / f"{title}-{suffix}.{ext}")
    for copy_no in range(1, 100):
        candidates.append(target_dir / f"{title}-copy-{copy_no}.{ext}")

    last_error: OSError | None = None
    for path in candidates:
        try:
            with path.open("xb") as handle:
                handle.write(body)
            return path
        except FileExistsError:
            continue
        except OSError as exc:
            last_error = exc
            break
    if last_error:
        raise last_error
    raise FileExistsError(f"could not reserve a unique filename for {title}.{ext}")


def _request_bytes(url: str, cookie: str, referer: str, timeout: float) -> HttpDownloadResponse:
    headers = {
        "User-Agent": os.environ.get("CNKI_USER_AGENT", DEFAULT_USER_AGENT),
        "Referer": referer or DEFAULT_REFERER,
        "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "identity",
        "Cookie": cookie,
    }
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            return HttpDownloadResponse(
                status=int(getattr(response, "status", 200) or 200),
                url=response.geturl(),
                headers=dict(response.headers.items()),
                body=response.read(),
            )
    except urllib.error.HTTPError as exc:
        return HttpDownloadResponse(
            status=int(exc.code or 0),
            url=getattr(exc, "url", url),
            headers=dict(exc.headers.items()) if exc.headers else {},
            body=exc.read() if hasattr(exc, "read") else b"",
        )
    except urllib.error.URLError as exc:
        raise HttpDownloadUnavailable("network_error", str(exc.reason or exc)) from exc
    except TimeoutError as exc:
        raise HttpDownloadUnavailable("timeout", str(exc)) from exc


def _looks_like_file(response: HttpDownloadResponse, fmt: str) -> bool:
    body = response.body or b""
    content_type = response.headers.get("Content-Type", "").lower()
    disposition = response.headers.get("Content-Disposition", "").lower()
    if fmt == "pdf" and body.startswith(b"%PDF"):
        return True
    if fmt == "caj" and body[:8].upper().startswith((b"CAJ", b"KDH")):
        return True
    if "attachment" in disposition:
        return len(body) > 0
    if fmt == "pdf" and "pdf" in content_type and len(body) > 0:
        return True
    if fmt == "caj" and ("octet-stream" in content_type or "caj" in content_type) and len(body) > 0:
        return True
    return False


def _classify_response(response: HttpDownloadResponse, fmt: str) -> tuple[str, str]:
    status = int(response.status or 0)
    probe = (response.body or b"")[:8192].decode("utf-8", errors="ignore")
    if CAPTCHA_RE.search(probe):
        return "captcha", "download captcha required"
    if LOGIN_RE.search(probe) or "login.cnki" in response.url.lower():
        return "login_required", "download login required"
    if SOURCE_APP_INVALID_RE.search(probe) or "errormsg.html" in response.url.lower():
        return "source_app_invalid", "download order link expired or missing source page context"
    if PERMISSION_RE.search(probe):
        return "permission_denied", "download permission denied"
    if status in {401, 403}:
        return "permission_denied", f"download HTTP {status}"
    if status >= 400:
        return "http_error", f"download HTTP {status}"
    if not _looks_like_file(response, fmt):
        return "format_mismatch", "download response was not a file"
    return "", ""


def download_direct_row(
    row: dict[str, Any],
    fmt: str = "pdf",
    download_dir: str | os.PathLike[str] = "",
    cookie: str | None = None,
    cookie_source: str = "",
    timeout: float | None = None,
) -> dict[str, Any]:
    fmt = (fmt or "pdf").lower()
    url = _direct_url(row, fmt)
    base = {
        "row_id": row.get("row_id", ""),
        "global_rank": row.get("global_rank", 0),
        "format": fmt.upper(),
        "download_transport": "http_direct",
        "download_strategy": f"http_direct_{fmt}",
    }
    if not url:
        return {**base, "status": "error", "error": "direct_url_missing", "detail": "no direct download URL available"}
    if cookie is None:
        try:
            cookie, cookie_source = _load_cookie_seed()
        except Exception as exc:
            return {**base, "status": "error", "error": "cookie_seed_failed", "detail": str(exc)}
    if not str(cookie or "").strip():
        return {**base, "status": "error", "error": "cookie_unavailable", "detail": f"no HTTP cookie available from {cookie_source or 'configured sources'}"}

    timeout = float(timeout if timeout is not None else os.environ.get("CNKI_HTTP_DOWNLOAD_TIMEOUT", "30"))
    try:
        response = _request_bytes(url, cookie, str(row.get("detail_url") or DEFAULT_REFERER), timeout)
    except HttpDownloadUnavailable as exc:
        return {**base, "status": "error", "error": exc.code, "detail": exc.detail}

    error, detail = _classify_response(response, fmt)
    if error:
        return {**base, "status": "error", "error": error, "detail": detail}

    target_dir = pathlib.Path(download_dir or os.environ.get("CNKI_DOWNLOAD_DIR", "cnki-downloads"))
    target_dir.mkdir(parents=True, exist_ok=True)
    title = _safe_filename(str(row.get("title") or ""), str(row.get("global_rank") or row.get("row_id") or "cnki-download"))
    ext = _extension(response.headers, fmt)
    try:
        path = _write_unique_bytes(target_dir, title, ext, row.get("global_rank") or row.get("row_id") or "", response.body)
    except OSError as exc:
        return {**base, "status": "error", "error": "file_write_failed", "detail": str(exc)}
    return {
        **base,
        "status": "downloaded",
        "title": title,
        "filename": path.name,
        "saved_to": str(path),
    }


def download_direct_rows(
    rows: list[dict[str, Any]],
    fmt: str = "pdf",
    download_dir: str | os.PathLike[str] = "",
    concurrency: int | None = None,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    worker_count = min(_download_concurrency(concurrency), len(rows))
    try:
        cookie, cookie_source = _load_cookie_seed()
    except Exception as exc:
        return [
            {
                "row_id": row.get("row_id", ""),
                "global_rank": row.get("global_rank", 0),
                "format": (fmt or "pdf").upper(),
                "download_transport": "http_direct",
                "download_strategy": f"http_direct_{(fmt or 'pdf').lower()}",
                "status": "error",
                "error": "cookie_seed_failed",
                "detail": str(exc),
            }
            for row in rows
        ]
    timeout = float(os.environ.get("CNKI_HTTP_DOWNLOAD_TIMEOUT", "30"))
    if worker_count <= 1:
        return [
            download_direct_row(row, fmt=fmt, download_dir=download_dir, cookie=cookie, cookie_source=cookie_source, timeout=timeout)
            for row in rows
        ]

    results: list[dict[str, Any] | None] = [None] * len(rows)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(
                download_direct_row,
                row,
                fmt=fmt,
                download_dir=download_dir,
                cookie=cookie,
                cookie_source=cookie_source,
                timeout=timeout,
            ): index
            for index, row in enumerate(rows)
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return [result for result in results if result is not None]


