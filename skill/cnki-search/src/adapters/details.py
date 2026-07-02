"""Guarded HTTP detail-page parser for cnki-search."""

from __future__ import annotations

import gzip
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

DEFAULT_REFERER = "https://kns.cnki.net/"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Safari/537.36"
)

LOGIN_RE = re.compile(r"未登录|请登录|登录后|用户登录|login\.cnki", re.I)
CAPTCHA_RE = re.compile(r"tcaptcha|captcha\.qq\.com|验证码|安全验证|人机验证|拖动滑块|/verify/home", re.I)
NO_PERMISSION_RE = re.compile(r"无权限|权限不足|未订购|机构未购买|not authorized|forbidden", re.I)
DETAIL_MARKER_RE = re.compile(r"abstract_text|abstract-text|h1-scholar|wx-tit|brief|doc-scholar|pdfDown|cajDown", re.I)


class HttpDetailUnavailable(Exception):
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


def _load_http_cookie() -> tuple[str, str]:
    try:
        cookie, source = _load_cookie_seed()
    except Exception as exc:  # noqa: BLE001
        raise HttpDetailUnavailable("cookie_seed_failed", str(exc)) from exc
    cookie = str(cookie or "").strip()
    if not cookie:
        raise HttpDetailUnavailable("cookie_unavailable", f"no HTTP cookie available from {source or 'configured sources'}")
    return cookie, source or "unknown"


def _request(url: str, cookie: str, timeout: float, referer: str = DEFAULT_REFERER) -> HttpResponse:
    headers = {
        "User-Agent": os.environ.get("CNKI_USER_AGENT", DEFAULT_USER_AGENT),
        "Referer": referer,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, identity",
        "Cookie": cookie,
    }
    started = time.monotonic()
    request = urllib.request.Request(url, headers=headers, method="GET")
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


def strip_html(value: str) -> str:
    value = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", value or "")
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", unescape(line)).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _extract_attr(attrs: str, name: str) -> str:
    match = re.search(rf"""{re.escape(name)}\s*=\s*(['"])(.*?)\1""", attrs or "", re.I | re.S)
    return unescape(match.group(2)).strip() if match else ""


def _first_tag_text(html: str, selectors: tuple[str, ...]) -> str:
    for pattern in selectors:
        match = re.search(pattern, html or "", re.I | re.S)
        if match:
            return strip_html(match.group(1))
    return ""


def _first_input_value(html: str, element_id: str) -> str:
    match = re.search(rf"""(?is)<input\b(?=[^>]*\bid\s*=\s*(['"]){re.escape(element_id)}\1)([^>]*)>""", html or "")
    if not match:
        return ""
    return _extract_attr(match.group(2), "value")


def _links(html: str, base_url: str) -> list[dict[str, str]]:
    output = []
    for match in re.finditer(r"(?is)<a\b([^>]*)>(.*?)</a>", html or ""):
        attrs, inner = match.groups()
        href = _extract_attr(attrs, "href")
        output.append(
            {
                "href": urllib.parse.urljoin(base_url, href) if href else "",
                "text": strip_html(inner),
                "id": _extract_attr(attrs, "id"),
                "class": _extract_attr(attrs, "class"),
                "title": _extract_attr(attrs, "title"),
                "attrs": attrs,
            }
        )
    return output


def extract_order_links(detail_html: str, base_url: str) -> dict[str, str]:
    links = _links(detail_html, base_url)

    def score(link: dict[str, str], fmt: str) -> int:
        haystack = " ".join(str(link.get(key, "")) for key in ("href", "text", "id", "class", "title"))
        if "download/order" not in haystack:
            return -1
        points = 0
        if fmt == "pdf" and link.get("id") == "pdfDown":
            points += 100
        if fmt == "caj" and link.get("id") == "cajDown":
            points += 100
        if re.search(fmt, haystack, re.I):
            points += 20
        if re.search(r"下载|download", haystack, re.I):
            points += 10
        return points

    result: dict[str, str] = {}
    for fmt in ("pdf", "caj"):
        candidates = sorted(((score(link, fmt), link) for link in links), key=lambda item: item[0], reverse=True)
        if candidates and candidates[0][0] >= 0:
            result[fmt] = candidates[0][1]["href"]
    return result


def _links_in_block(html: str, block_pattern: str) -> list[str]:
    match = re.search(block_pattern, html or "", re.I | re.S)
    if not match:
        return []
    return [link["text"].rstrip(";；") for link in _links(match.group(1), "") if link.get("text")]


def _author_sections(html: str) -> tuple[list[dict[str, str]], list[str]]:
    sections = re.findall(r"(?is)<h3\b[^>]*class\s*=\s*(['\"])[^'\"]*\bauthor\b[^'\"]*\1[^>]*>(.*?)</h3>", html or "")
    if not sections:
        return [], []
    authors = []
    for link in _links(sections[0][1], ""):
        text = link.get("text", "")
        if not text:
            continue
        match = re.match(r"(.+?)(\d+)?$", text)
        authors.append({"name": (match.group(1) if match else text).strip(), "affiliationNum": (match.group(2) if match and match.group(2) else "")})
    affiliations = [link["text"] for link in _links(sections[1][1], "") if link.get("text")] if len(sections) > 1 else []
    return authors, affiliations


def _citation_info(html: str) -> dict[str, dict[str, int | str]]:
    output: dict[str, dict[str, int | str]] = {}
    for match in re.finditer(r"(?is)<li\b([^>]*)>(.*?)</li>", html or ""):
        attrs, inner = match.groups()
        data_id = _extract_attr(attrs, "data-id")
        if not data_id:
            continue
        text = strip_html(inner)
        count_match = re.search(r"(\d+)", text)
        output[data_id] = {
            "label": re.sub(r"\d+", "", text).strip(),
            "count": int(count_match.group(1)) if count_match else 0,
        }
    return output


def classify_detail_response(response: HttpResponse) -> tuple[str, str]:
    text = (response.body or "")[:250000]
    combined = text + " " + response.url
    has_detail_markers = bool(DETAIL_MARKER_RE.search(text))
    if response.error and response.status == 0:
        return "network_error", response.error
    if CAPTCHA_RE.search(combined) and not has_detail_markers:
        return "captcha", "captcha"
    if LOGIN_RE.search(combined) and not has_detail_markers:
        return "login_required", "detail login required"
    if NO_PERMISSION_RE.search(combined) or response.status == 403:
        return "permission_denied", "permission denied"
    if response.status >= 400:
        return "http_error", f"http {response.status}"
    if not has_detail_markers:
        return "detail_not_found", "detail page markers not found"
    return "ok", ""


def parse_detail_html(html: str, final_url: str) -> dict[str, Any]:
    title = _first_tag_text(
        html,
        (
            r"""<[^>]+class\s*=\s*['"][^'"]*\bh1-scholar\b[^'"]*['"][^>]*>(.*?)</[^>]+>""",
            r"""<div\b[^>]*class\s*=\s*['"][^'"]*\bwx-tit\b[^'"]*['"][^>]*>.*?<h1[^>]*>(.*?)</h1>""",
            r"""<h1[^>]*>(.*?)</h1>""",
        ),
    )
    title = re.sub(r"\s*(附视频|网络首发|MT翻译.*)$", "", title).strip()
    abstract = _first_input_value(html, "abstract_text") or _first_tag_text(
        html,
        (
            r"""<[^>]+class\s*=\s*['"][^'"]*\babstract-text\b[^'"]*['"][^>]*>(.*?)</[^>]+>""",
            r"""<div\b[^>]*id\s*=\s*['"]ChDivSummary['"][^>]*>(.*?)</div>""",
            r"""<[^>]+class\s*=\s*['"][^'"]*\bsummary\b[^'"]*['"][^>]*>(.*?)</[^>]+>""",
        ),
    )
    authors, affiliations = _author_sections(html)
    keywords = _links_in_block(html, r"""(?is)<p\b[^>]*class\s*=\s*['"][^'"]*\bkeywords\b[^'"]*['"][^>]*>(.*?)</p>""")
    fund = _first_tag_text(html, (r"""<p\b[^>]*class\s*=\s*['"][^'"]*\bfunds\b[^'"]*['"][^>]*>(.*?)</p>""",))
    classification = _first_tag_text(html, (r"""<[^>]+class\s*=\s*['"][^'"]*\bclc-code\b[^'"]*['"][^>]*>(.*?)</[^>]+>""",))
    pub_info = _first_tag_text(html, (r"""<[^>]+class\s*=\s*['"][^'"]*\bhead-time\b[^'"]*['"][^>]*>(.*?)</[^>]+>""",))
    toc = _first_tag_text(
        html,
        (
            r"""<[^>]+class\s*=\s*['"][^'"]*\bcatalog-list\b[^'"]*['"][^>]*>(.*?)</[^>]+>""",
            r"""<[^>]+class\s*=\s*['"][^'"]*\bcatalog-listDiv\b[^'"]*['"][^>]*>(.*?)</[^>]+>""",
        ),
    )
    doc_top = _first_tag_text(html, (r"""<[^>]+class\s*=\s*['"][^'"]*\bdoc-top\b[^'"]*['"][^>]*>.*?<a[^>]*>(.*?)</a>""",))

    order_links = extract_order_links(html, final_url)
    return {
        "title": title,
        "authors_structured": authors,
        "affiliations": affiliations,
        "abstract": abstract,
        "keywords": keywords,
        "fund": fund,
        "classification": classification,
        "journal": "" if toc else doc_top,
        "pub_info": pub_info,
        "is_online_first": bool(re.search(r"icon-shoufa|网络首发", html or "", re.I)),
        "toc": toc,
        "citation_info": _citation_info(html),
        "raw_url": final_url,
        "pdf_url": order_links.get("pdf", ""),
        "caj_url": order_links.get("caj", ""),
    }


def fetch_detail_row(row: dict[str, Any], cookie: str | None = None, timeout: float | None = None) -> dict[str, Any]:
    detail_url = str(row.get("detail_url") or row.get("raw_url") or "").strip()
    base = {"row_id": row.get("row_id", ""), "global_rank": row.get("global_rank", "")}
    if not detail_url:
        return {**base, "status": "error", "error": "missing_detail_url", "detail": "missing_detail_url", "attempts": 0}
    if cookie is None:
        cookie, _source = _load_http_cookie()
    timeout = float(timeout if timeout is not None else os.environ.get("CNKI_HTTP_TIMEOUT", "25"))
    response = _request(detail_url, cookie, timeout)
    state, detail = classify_detail_response(response)
    if state != "ok":
        return {
            **base,
            "status": "error",
            "error": state,
            "detail": detail,
            "raw_url": detail_url,
            "final_url": response.url,
            "http_status": response.status,
            "attempts": 1,
        }
    parsed = parse_detail_html(response.body, response.url)
    if not (parsed.get("title") or parsed.get("abstract") or parsed.get("pdf_url") or parsed.get("caj_url")):
        return {**base, "status": "error", "error": "detail_parse_empty", "detail": "no useful detail fields found", "raw_url": detail_url, "final_url": response.url, "http_status": response.status, "attempts": 1}
    return {**base, "status": "ok", "attempts": 1, **parsed}


def fetch_detail_rows(rows: list[dict[str, Any]], cookie: str | None = None) -> list[dict[str, Any]]:
    if cookie is None:
        cookie, _source = _load_http_cookie()
    timeout = float(os.environ.get("CNKI_HTTP_TIMEOUT", "25"))
    concurrency = max(1, int(os.environ.get("CNKI_DETAIL_MAX_CONCURRENCY", "4")))
    if concurrency <= 1 or len(rows) <= 1:
        output = []
        for index, row in enumerate(rows):
            result = fetch_detail_row(row, cookie=cookie, timeout=timeout)
            output.append(result)
            if result.get("error") == "captcha" and os.environ.get("CNKI_DETAIL_STOP_ON_CAPTCHA", "1").strip().lower() not in {"0", "false", "no", "off"}:
                for skipped in rows[index + 1:]:
                    output.append({"row_id": skipped.get("row_id", ""), "global_rank": skipped.get("global_rank", ""), "status": "error", "error": "skipped_after_captcha", "detail": "captcha_short_circuit", "attempts": 0})
                break
        return output
    results: list[dict[str, Any] | None] = [None] * len(rows)
    with ThreadPoolExecutor(max_workers=min(concurrency, len(rows))) as executor:
        futures = {executor.submit(fetch_detail_row, row, cookie=cookie, timeout=timeout): index for index, row in enumerate(rows)}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return [row for row in results if row is not None]


