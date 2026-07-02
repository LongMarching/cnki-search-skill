"""Direct HTTP facet discovery for cnki-search AdvSearch results."""

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
from dataclasses import dataclass
from html import unescape
from typing import Any

from adapters import search as http_search
from core.http import FACET_GROUP_FIELD_MAP, _load_cookie_seed

GROUP_RESULT_URL = "https://kns.cnki.net/kns8s/group/result"
ADVSEARCH_REFERER = "https://kns.cnki.net/kns8s/AdvSearch"
DEFAULT_USER_AGENT = http_search.DEFAULT_USER_AGENT

CAPTCHA_RE = re.compile(r"tcaptcha|captcha\.qq\.com|验证码|安全验证|人机验证|拖动滑块|/verify/home", re.I)
LOGIN_RE = re.compile(r"未登录|请登录|登录后|用户登录|login\.cnki", re.I)
NO_PERMISSION_RE = re.compile(r"无权限|权限不足|未订购|机构未购买|not authorized|forbidden", re.I)


class HttpFacetUnavailable(Exception):
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
        raise HttpFacetUnavailable("cookie_seed_failed", str(exc)) from exc
    cookie = str(cookie or "").strip()
    if not cookie:
        raise HttpFacetUnavailable("cookie_unavailable", f"no HTTP cookie available from {source or 'configured sources'}")
    return cookie, source or "unknown"


def _request(url: str, cookie: str, form: dict[str, str], timeout: float) -> HttpResponse:
    headers = {
        "User-Agent": os.environ.get("CNKI_USER_AGENT", DEFAULT_USER_AGENT),
        "Referer": ADVSEARCH_REFERER,
        "Origin": "https://kns.cnki.net",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, identity",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Cookie": cookie,
    }
    started = time.monotonic()
    request = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            raw = response.read()
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                raw = gzip.decompress(raw)
            return HttpResponse(response.geturl(), int(response.status), {k: v for k, v in response.headers.items()}, raw.decode("utf-8", errors="replace"), int((time.monotonic() - started) * 1000))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        if exc.headers.get("Content-Encoding", "").lower() == "gzip":
            raw = gzip.decompress(raw)
        return HttpResponse(exc.geturl(), int(exc.code), {k: v for k, v in exc.headers.items()}, raw.decode("utf-8", errors="replace"), int((time.monotonic() - started) * 1000), str(exc))
    except Exception as exc:  # noqa: BLE001
        return HttpResponse(url, 0, {}, "", int((time.monotonic() - started) * 1000), f"{type(exc).__name__}: {exc}")


def _strip_html(value: str) -> str:
    value = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", value or "")
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _extract_attr(attrs: str, name: str) -> str:
    match = re.search(rf"""{re.escape(name)}\s*=\s*(['"])(.*?)\1""", attrs or "", re.I | re.S)
    return unescape(match.group(2)).strip() if match else ""


def _parse_count(value: str) -> int:
    match = re.search(r"([\d,]+)", value or "")
    if not match:
        return 0
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return 0


def classify_facet_response(response: HttpResponse) -> tuple[str, str]:
    text = (response.body or "")[:250000]
    combined = text + " " + response.url
    if response.error and response.status == 0:
        return "network_error", response.error
    if CAPTCHA_RE.search(combined):
        return "captcha", "captcha"
    if LOGIN_RE.search(combined):
        return "login_required", "facet login required"
    if NO_PERMISSION_RE.search(combined) or response.status == 403:
        return "permission_denied", "permission denied"
    if response.status >= 400:
        return "http_error", f"http {response.status}"
    if "input" not in text and "checkbox" not in text:
        return "facet_group_not_available", "facet response has no checkbox items"
    return "ok", ""


def parse_facet_html(html: str, group_field: str) -> tuple[str, list[dict[str, Any]]]:
    group_label = ""
    group_match = re.search(rf"""(?is)<dd\b(?=[^>]*\bfield\s*=\s*(['"]){re.escape(group_field)}\1)([^>]*)>(.*?)</dd>""", html or "")
    body = group_match.group(3) if group_match else html
    if group_match:
        group_label = _extract_attr(group_match.group(2), "tit")
    items = []
    for index, match in enumerate(re.finditer(r"(?is)<li\b([^>]*)>(.*?)</li>", body or "")):
        li_attrs, li_body = match.groups()
        input_match = re.search(r"(?is)<input\b([^>]*)>", li_body)
        if not input_match:
            continue
        input_attrs = input_match.group(1)
        code = (_extract_attr(input_attrs, "value") or _extract_attr(input_attrs, "data-val")).upper()
        title = _extract_attr(input_attrs, "title") or _extract_attr(input_attrs, "text")
        label = title or _strip_html(re.sub(r"(?is)<span\b.*?</span>", " ", li_body))
        count = _parse_count(_strip_html(" ".join(re.findall(r"(?is)<span\b[^>]*>(.*?)</span>", li_body))))
        style = _extract_attr(li_attrs, "style")
        items.append(
            {
                "index": index,
                "code": code,
                "label": re.sub(r"\([\d,]+\)", "", label).strip(),
                "count": count,
                "checked": bool(re.search(r"\bchecked\b", input_attrs, re.I)),
                "visible": "display:none" not in style.replace(" ", "").lower(),
            }
        )
    return group_label, [item for item in items if item.get("code") or item.get("label")]


def _facet_query_json(spec: http_search.SearchSpec) -> dict[str, Any]:
    query_json = http_search.QueryJsonBuilder(spec).build()
    query_json["SearchFrom"] = 99
    resource = str(query_json.get("Resource") or "")
    classid = str(query_json.get("Classid") or "")
    products = http_search.REFRESH_PRODUCTS_BY_CLASSID.get(classid) or http_search.REFRESH_PRODUCTS_BY_RESOURCE.get(resource)
    if products:
        query_json["Products"] = products
    if classid in http_search.REFRESH_VIEW_BY_CLASSID:
        query_json["View"] = http_search.REFRESH_VIEW_BY_CLASSID[classid]
    elif resource in http_search.REFRESH_VIEW_BY_RESOURCE:
        query_json["View"] = http_search.REFRESH_VIEW_BY_RESOURCE[resource]
    return query_json


def run_http_facets(spec: http_search.SearchSpec, group: str = "subdiscipline", cookie: str | None = None, timeout: float | None = None) -> dict[str, Any]:
    group_field = FACET_GROUP_FIELD_MAP.get(group)
    if not group_field:
        raise HttpFacetUnavailable("invalid_facet_group", f"unsupported facet group: {group}", group=group)
    if cookie is None:
        cookie, source = _load_http_cookie()
    else:
        source = "provided"
    timeout = float(timeout if timeout is not None else os.environ.get("CNKI_HTTP_TIMEOUT", "25"))
    query_json = _facet_query_json(spec)
    form = {
        "queryJson": json.dumps(query_json, ensure_ascii=False, separators=(",", ":")),
        "aside": f"（主题：{spec.query}）" if spec.query else "",
        "subject": "",
        "language": "",
        "uniplatform": "",
        "groupIds": "",
    }
    response = _request(GROUP_RESULT_URL, cookie, form, timeout)
    state, detail = classify_facet_response(response)
    if state != "ok":
        raise HttpFacetUnavailable(f"facet_{state}", detail, http_status=response.status)
    group_label, items = parse_facet_html(response.body, group_field)
    if not group_label and f'field="{group_field}"' not in response.body and f"field='{group_field}'" not in response.body:
        raise HttpFacetUnavailable("facet_group_not_available", f"no facet items for {group_field}")
    return {
        "transport": "http",
        "cookie_source": source,
        "facet_group": group,
        "facet_field": group_field,
        "group_label": group_label,
        "items": items,
        "warnings": [],
        "http_status": response.status,
        "elapsed_ms": response.elapsed_ms,
    }


