"""Direct HTTP AdvSearch transport for cnki-search.

This module models the result-list endpoints emitted by kns8s/AdvSearch.
It intentionally stays stdlib-only so the distributed skill keeps a small
dependency footprint.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html import unescape
from typing import Any

from core.http import _load_cookie_seed

BRIEF_GRID_URL = "https://kns.cnki.net/kns8s/brief/grid"
BRIEF_SENQUERY_URL = "https://kns.cnki.net/kns8s/brief/senquery"
ADVSEARCH_REFERER = "https://kns.cnki.net/kns8s/AdvSearch"
PRODUCT_STR = (
    "YSTT4HG0,LSTPFY1C,RMJLXHZ3,JQIRZIYA,JUP3MUPD,1UR4K4HZ,BPBAFJ5S,"
    "R79MZMCB,MPMFIG1A,EMRPGLPA,J708GVCE,ML4DRIDX,WQ0UVIAA,NB3BWEHK,"
    "XVLO76FD,HR1YT1Z9,BLZOG7CK,PWFIRAGL,NN3FJMUV,NLBO1Z6R,"
)
KUA_KU_CROSSDB = "YSTT4HG0,LSTPFY1C,JUP3MUPD,MPMFIG1A,EMRPGLPA,WQ0UVIAA,BLZOG7CK,PWFIRAGL,NN3FJMUV,NLBO1Z6R"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Safari/537.36"
)

DOC_TYPE_RESOURCE = {
    "all": ("WD0FTY92", "CROSSDB"),
    "journal": ("YSTT4HG0", "JOURNAL"),
    "thesis": ("LSTPFY1C", "DISSERTATION"),
    "phd": ("RMJLXHZ3", "DISSERTATION"),
    "masters": ("JQIRZIYA", "DISSERTATION"),
    "conference": ("JUP3MUPD", "CONFERENCE"),
    "domestic-conf": ("1UR4K4HZ", "CONFERENCE"),
    "intl-conf": ("BPBAFJ5S", "CONFERENCE"),
}
FIELD_TITLES = {
    "SU": "主题",
    "TKA": "篇关摘",
    "KY": "关键词",
    "TI": "篇名",
    "FT": "全文",
    "AU": "作者",
    "FI": "第一作者",
    "RP": "通讯作者",
    "AF": "作者单位",
    "FU": "基金",
    "AB": "摘要",
    "CO": "小标题",
    "RF": "参考文献",
    "CLC": "分类号",
    "LY": "文献来源",
    "DOI": "DOI",
}
QUALITY_ITEMS = {
    "cssci": ("CSSCI", "CSI", "Y"),
    "sci": ("SCI", "SI", "Y"),
    "ei": ("EI", "EI", "Y"),
    "pku": ("北大核心", "HX", "Y"),
    "cscd": ("CSCD", "CSD", "Y"),
    "wjci": ("WJCI", "LYBSM", "Y"),
    "ami": ("AMI", "AMI", "Y"),
}
ELITE_UNI_ITEMS = {
    "all": ("双一流", "1 * 2"),
    "first-class-uni": ("一流大学", "1"),
    "first-class-disc": ("一流学科", "2"),
}
FORM_FILTER_ITEMS = {
    "oa": ("OA出版", "OA", "1"),
    "fund": ("基金文献", "JJWX", "Y"),
    "enhanced": ("增强出版", "NPM", "ZQ"),
}
SORT_FIELD_MAP = {
    "date": ("PT", "desc"),
    "relevance": ("FFD", "desc"),
    "citations": ("CF", "desc"),
    "downloads": ("DFR", "desc"),
    "comprehensive": ("ZH", "desc"),
}
INITIAL_VIEW_BY_RESOURCE = {
    "CROSSDB": "changeDBCh",
    "JOURNAL": "changeDBCh",
}
REFRESH_PRODUCTS_BY_CLASSID = {
    "LSTPFY1C": "CDFD,CMFD",
    "RMJLXHZ3": "CDFD",
    "JQIRZIYA": "CMFD",
    "JUP3MUPD": "CPFD,IPFD,CPVD",
    "1UR4K4HZ": "CPFD",
    "BPBAFJ5S": "IPFD",
}
REFRESH_PRODUCTS_BY_RESOURCE = {
    "CROSSDB": "CJFQ,CAPJ,ZHYX,CJTL,CDFD,CMFD,CPFD,IPFD,CPVD,CCND,WBFD,SCSF,SCHF,SCSD,SNAD,CCJD,CJFN,CCVD",
    "JOURNAL": "CJFQ,CAPJ,CJTL",
}
REFRESH_VIEW_BY_CLASSID = {
    "JUP3MUPD": "",
}
REFRESH_VIEW_BY_RESOURCE = {
    "CROSSDB": "changeDBCh",
    "JOURNAL": "changeDBOnlyFT",
}
SEARCH_TYPE_MAP = {"advsearch": 1, "author": 3, "professional": 4, "sentence": 5}
LOGIC_MAP = {"AND": 0, "OR": 1, "NOT": 2}

CAPTCHA_RE = re.compile(r"tcaptcha|captcha\.qq\.com|验证码|安全验证|人机验证|拖动滑块", re.I)
LOGIN_RE = re.compile(r"未登录|请登录|登录后|用户登录|login\.cnki", re.I)
NO_PERMISSION_RE = re.compile(r"无权限|权限不足|未订购|机构未购买|not authorized|forbidden", re.I)
NO_RESULTS_RE = re.compile(r"暂无数据|未找到|没有找到|抱歉，暂无数据", re.I)

_HTTP_SEMAPHORE = threading.BoundedSemaphore(max(1, int(os.environ.get("CNKI_HTTP_MAX_CONCURRENCY", "12"))))


class HttpSearchUnavailable(Exception):
    def __init__(self, code: str, detail: str = "", **extra: Any):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code
        self.extra = extra


@dataclass
class SearchSpec:
    query: str
    language: str = "zh"
    page_limit: int = 1
    search_mode: str = "advsearch"
    extra: dict[str, Any] = field(default_factory=dict)
    doc_type: str = "all"
    discipline: list[str] = field(default_factory=list)
    quality: list[str] = field(default_factory=list)
    elite_uni: str | None = None
    sort: str = "date"
    date_from: str | None = None
    date_to: str | None = None
    fields: list[dict[str, Any]] | None = None
    form_filters: list[str] = field(default_factory=list)
    page_size: int = 20


@dataclass
class HttpResponse:
    url: str
    status: int
    headers: dict[str, str]
    body: str
    elapsed_ms: int
    error: str = ""


def _strip_html(value: str) -> str:
    value = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", value or "")
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _extract_attr(tag_attrs: str, attr: str) -> str:
    match = re.search(rf"""{re.escape(attr)}\s*=\s*(['"])(.*?)\1""", tag_attrs or "", re.I | re.S)
    return unescape(match.group(2)).strip() if match else ""


def _html_links(html: str, base_url: str) -> list[dict[str, str]]:
    links = []
    for match in re.finditer(r"(?is)<a\b([^>]*)>(.*?)</a>", html or ""):
        attrs, inner = match.groups()
        href = unescape(_extract_attr(attrs, "href") or "").strip()
        if not href:
            continue
        href_lower = href.lower()
        if href_lower in {"#", "javascript:void(0)", "javascript:;"} or href_lower.startswith(("javascript:", "mailto:")):
            continue
        links.append({
            "href": urllib.parse.urljoin(base_url, href),
            "text": _strip_html(inner),
            "attrs": attrs,
        })
    return links


def _link_haystack(link: dict[str, str]) -> str:
    attrs = link.get("attrs", "")
    return " ".join(
        str(value or "")
        for value in (
            link.get("href", ""),
            link.get("text", ""),
            _extract_attr(attrs, "id"),
            _extract_attr(attrs, "class"),
            _extract_attr(attrs, "title"),
        )
    )


def _extract_action_links(links: list[dict[str, str]]) -> dict[str, str]:
    def pick(predicate) -> str:
        for link in links:
            haystack = _link_haystack(link)
            if predicate(haystack, link):
                return link.get("href", "")
        return ""

    pdf_url = pick(
        lambda haystack, link: bool(re.search(r"pdfDown|PDF下载|pdf\s*下载", haystack, re.I))
        or ("download/order" in link.get("href", "").lower() and bool(re.search(r"\bpdf\b", haystack, re.I)))
    )
    caj_url = pick(
        lambda haystack, link: bool(re.search(r"cajDown|CAJ下载|caj\s*下载", haystack, re.I))
        or ("download/order" in link.get("href", "").lower() and bool(re.search(r"\bcaj\b", haystack, re.I)))
    )
    download_url = pick(
        lambda haystack, link: "download/order" in link.get("href", "").lower()
        or bool(re.search(r"下载|download", haystack, re.I))
    )
    if not caj_url and download_url:
        caj_url = download_url

    return {
        key: value
        for key, value in {
            "pdf_url": pdf_url,
            "caj_url": caj_url,
            "download_url": download_url,
        }.items()
        if value
    }


def query_item(key: Any, title: str, field_name: str, operator: Any, value: str, value2: str = "", logic: int = 0) -> dict[str, Any]:
    return {
        "Key": key,
        "Title": title,
        "Logic": int(logic),
        "Field": field_name,
        "Operator": operator,
        "Value": value,
        "Value2": value2,
    }


def child_group(key: str, title: str, items: list[dict[str, Any]], logic: int = 0) -> dict[str, Any]:
    return {"Key": key, "Title": title, "Logic": int(logic), "Items": items, "ChildItems": []}


def _discipline_name(code: str) -> str:
    try:
        from core.discipline_map import code_to_name

        return code_to_name().get(code, code)
    except Exception:
        return code


def normalize_field_specs(fields: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    specs = []
    for index, raw in enumerate(fields or [], start=1):
        if not isinstance(raw, dict):
            continue
        field_code = str(raw.get("field") or raw.get("Field") or "SU").upper()
        precision = str(raw.get("precision", "")).lower()
        operator = str(raw.get("operator") or raw.get("Operator") or ("FUZZY" if precision in {"fuzzy", "%"} else "DEFAULT")).upper()
        logic_text = str(raw.get("op") or raw.get("logic") or "AND").upper()
        specs.append({
            "index": index,
            "field": field_code,
            "title": str(raw.get("title") or raw.get("Title") or FIELD_TITLES.get(field_code, field_code)),
            "operator": operator,
            "value": str(raw.get("value") or raw.get("Value") or ""),
            "logic": LOGIC_MAP.get(logic_text, 0),
        })
    return specs


class QueryJsonBuilder:
    def __init__(self, spec: SearchSpec):
        self.spec = spec
        self.warnings: list[dict[str, Any]] = []

    def build(self) -> dict[str, Any]:
        classid, resource = DOC_TYPE_RESOURCE.get(self.spec.doc_type, DOC_TYPE_RESOURCE["all"])
        groups = [self._subject_group(), self._control_group()]
        navi = self._navi_group()
        if navi:
            groups.append(navi)
        query_json = {
            "Platform": "",
            "Resource": resource,
            "Classid": classid,
            "Products": "",
            "QNode": {"QGroup": groups},
            "ExScope": "1",
            "SearchType": SEARCH_TYPE_MAP.get(self.spec.search_mode, 1),
            "Rlang": self._rlang(resource),
            "KuaKuCode": KUA_KU_CROSSDB if resource == "CROSSDB" else "",
            "Expands": {},
            "SearchFrom": 5 if self.spec.sort != "date" else 1,
        }
        if resource in INITIAL_VIEW_BY_RESOURCE:
            query_json["View"] = INITIAL_VIEW_BY_RESOURCE[resource]
        return query_json

    def _rlang(self, resource: str) -> str:
        if resource == "DISSERTATION":
            return "Chinese"
        return {"zh": "CHINESE", "en": "ENG", "both": "BOTH"}.get(self.spec.language, "CHINESE")

    def _subject_group(self) -> dict[str, Any]:
        group = {"Key": "Subject", "Title": "", "Logic": 0, "Items": [], "ChildItems": []}
        if self.spec.search_mode == "professional":
            group["Items"].append(query_item("Expert", "", "EXPERT", 0, self.spec.query))
        elif self.spec.search_mode == "sentence":
            proximity = str(self.spec.extra.get("proximity") or "NEAR").upper()
            word1 = str(self.spec.extra.get("word1") or self.spec.query)
            word2 = str(self.spec.extra.get("word2") or "")
            group["Items"].append(query_item("sentence0", "同一句" if proximity == "NEAR" else "同段", "FT", proximity, word1, word2))
        elif self.spec.search_mode == "author":
            author = str(self.spec.extra.get("author") or self.spec.query)
            group["ChildItems"].append(child_group("input[data-tipid=gradetxt-1]", "作者", [
                query_item("input[data-tipid=gradetxt-1]", "作者", "AU", "DEFAULT", author)
            ]))
            affiliation = str(self.spec.extra.get("affiliation") or "")
            if affiliation:
                group["ChildItems"].append(child_group("input[data-tipid=gradetxt-2]", "作者单位", [
                    query_item("input[data-tipid=gradetxt-2]", "作者单位", "AF", "FUZZY", affiliation)
                ]))
        else:
            specs = normalize_field_specs(self.spec.fields)
            if not specs:
                specs = [{"index": 1, "field": "SU", "title": "主题", "operator": "TOPRANK", "value": self.spec.query, "logic": 0}]
            for item in specs:
                key = f"input[data-tipid=gradetxt-{item['index']}]"
                group["ChildItems"].append(child_group(key, item["title"], [
                    query_item(key, item["title"], item["field"], item["operator"], item["value"], logic=item["logic"])
                ]))

        for value in self.spec.form_filters or []:
            if value == "online_first":
                group["ChildItems"].append(child_group("网络首发", "", [
                    query_item("网络首发", "网络首发", "WLSF", "DEFAULT", "2 "),
                    query_item("网络首发", "网络首发", "WXZT", "DEFAULT", "2", logic=2),
                ]))
                continue
            mapping = FORM_FILTER_ITEMS.get(value)
            if mapping:
                title, field_name, item_value = mapping
                key = ".extend-indent-labels>.colorful-lable" if value in {"oa", "enhanced"} else ".extend-indent-labels>.default-label"
                group["ChildItems"].append(child_group(key, "", [query_item(key, title, field_name, "DEFAULT", item_value)]))
        return group

    def _control_group(self) -> dict[str, Any]:
        group = {"Key": "ControlGroup", "Title": "", "Logic": 0, "Items": [], "ChildItems": []}
        quality_items = []
        for value in self.spec.quality or []:
            mapping = QUALITY_ITEMS.get(value)
            if mapping:
                title, field_name, item_value = mapping
                quality_items.append(query_item(len(quality_items), title, field_name, "DEFAULT", item_value, logic=1))
        if quality_items:
            group["ChildItems"].append(child_group(".extend-tit-checklist", "", quality_items))
        if self.spec.elite_uni:
            mapping = ELITE_UNI_ITEMS.get(self.spec.elite_uni)
            if mapping:
                title, item_value = mapping
                group["ChildItems"].append(child_group(".extend-tit-checklist", "", [
                    query_item(0, title, "EL", "DEFAULT", item_value, logic=1)
                ]))
        if self.spec.date_from or self.spec.date_to:
            group["ChildItems"].append(child_group("span[value=PT]", "", [
                query_item("span[value=PT]", "发表时间", "PT", 7, self.spec.date_from or "", self.spec.date_to or "")
            ]))
        return group

    def _navi_group(self) -> dict[str, Any] | None:
        if not self.spec.discipline:
            return None
        group = {"Key": "NaviParam", "Title": "", "Logic": 0, "Items": [], "ChildItems": []}
        for code in self.spec.discipline:
            clean = str(code).strip().upper()
            group["Items"].append(query_item("naviScope", f"文献分类：{_discipline_name(clean)}", "CCL", "DEFAULT", f"{clean}?"))
        return group


class SearchFormBuilder:
    def __init__(self, spec: SearchSpec, query_json: dict[str, Any]):
        self.spec = spec
        self.query_json = query_json

    def _query_json_for_request(self, request_kind: str) -> dict[str, Any]:
        query_json = json.loads(json.dumps(self.query_json, ensure_ascii=False))
        if request_kind == "page":
            query_json["SearchFrom"] = 4
        elif request_kind == "sort":
            query_json["SearchFrom"] = 5
        else:
            query_json["SearchFrom"] = 1
        if request_kind in {"sort", "page"}:
            resource = str(query_json.get("Resource") or "")
            classid = str(query_json.get("Classid") or "")
            products = REFRESH_PRODUCTS_BY_CLASSID.get(classid) or REFRESH_PRODUCTS_BY_RESOURCE.get(resource)
            if products:
                query_json["Products"] = products
            if classid in REFRESH_VIEW_BY_CLASSID:
                query_json["View"] = REFRESH_VIEW_BY_CLASSID[classid]
            elif resource in REFRESH_VIEW_BY_RESOURCE:
                query_json["View"] = REFRESH_VIEW_BY_RESOURCE[resource]
            else:
                query_json.pop("View", None)
        return query_json

    def build(self, page: int, turnpage: str = "", request_kind: str | None = None) -> tuple[dict[str, str], list[dict[str, Any]]]:
        warnings: list[dict[str, Any]] = []
        if request_kind is None:
            request_kind = "page" if int(page) > 1 else ("sort" if self.spec.sort != "date" else "initial")
        if request_kind not in {"initial", "sort", "page"}:
            raise ValueError(f"unknown request_kind {request_kind!r}")
        query_json = self._query_json_for_request(request_kind)
        if request_kind == "initial":
            sort_field, sort_type = "", ""
        else:
            sort_field, sort_type = SORT_FIELD_MAP.get(self.spec.sort, SORT_FIELD_MAP["date"])
        form = {
            "boolSearch": "false" if request_kind in {"sort", "page"} else "true",
            "QueryJson": json.dumps(query_json, ensure_ascii=False, separators=(",", ":")),
            "pageNum": str(int(page)),
            "pageSize": str(int(self.spec.page_size)),
            "dstyle": "listmode",
            "sortField": sort_field,
            "sortType": sort_type,
            "productStr": PRODUCT_STR if self.query_json.get("Resource") == "CROSSDB" else "",
            "searchFrom": "",
            "subject": "",
            "language": "",
            "uniplatform": "",
            "CurPage": str(int(page)),
        }
        if request_kind == "page":
            form.pop("CurPage", None)
            form["boolSortSearch"] = "false"
        elif request_kind == "sort":
            form["boolSortSearch"] = "true"
        if self.spec.search_mode == "sentence":
            form["sentenceSearch"] = "true"
        elif request_kind in {"sort", "page"} and query_json.get("Resource") == "CROSSDB":
            form["sentenceSearch"] = "false"
        if turnpage:
            form["turnpage"] = turnpage
        elif request_kind == "page":
            warnings.append({
                "code": "http_pagination_without_turnpage",
                "detail": "pageNum/CurPage pagination is used without a hardcoded turnpage; invalid page bodies are returned as guarded HTTP errors",
                "page": page,
            })
        elif request_kind == "sort":
            warnings.append({
                "code": "http_sort_without_turnpage",
                "detail": "sort request is more stable after a first-submit response supplies hidTurnPage",
                "sort": self.spec.sort,
            })
        return form, warnings


def endpoint_for_search_mode(search_mode: str) -> str:
    return BRIEF_SENQUERY_URL if search_mode == "sentence" else BRIEF_GRID_URL


def _detect_state(response: HttpResponse) -> str:
    text = (response.body or "")[:250000]
    has_rows = any(marker in text for marker in ("result-table-list", "result-detail-list", "/kcms2/article/abstract"))
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
    if LOGIN_RE.search(text + response.url) and not has_rows:
        return "login_required"
    if NO_PERMISSION_RE.search(text):
        return "no_permission"
    if NO_RESULTS_RE.search(text):
        return "no_results"
    if has_rows:
        return "ok"
    if response.status >= 400:
        return "http_error"
    if not text.strip():
        return "empty_body"
    return "ok"


def _request(method: str, url: str, cookie: str, form: dict[str, str], timeout: float) -> HttpResponse:
    data = urllib.parse.urlencode(form).encode("utf-8")
    headers = {
        "User-Agent": os.environ.get("CNKI_USER_AGENT", DEFAULT_USER_AGENT),
        "Referer": os.environ.get("CNKI_REFERER", ADVSEARCH_REFERER),
        "Origin": "https://kns.cnki.net",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, identity",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
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
    except Exception as exc:  # noqa: BLE001 - caller needs a structured transport error.
        return HttpResponse(url=url, status=0, headers={}, body="", elapsed_ms=int((time.monotonic() - started) * 1000), error=f"{type(exc).__name__}: {exc}")


def _load_http_cookie() -> tuple[str, str]:
    try:
        cookie, source = _load_cookie_seed()
    except Exception as exc:  # noqa: BLE001
        raise HttpSearchUnavailable("cookie_seed_failed", str(exc)) from exc
    cookie = str(cookie or "").strip()
    if not cookie:
        raise HttpSearchUnavailable("cookie_unavailable", f"no HTTP cookie available from {source or 'configured sources'}")
    return cookie, source or "unknown"


def _extract_cells(row_html: str) -> list[dict[str, str]]:
    cells = []
    for match in re.finditer(r"(?is)<td\b([^>]*)>(.*?)</td>", row_html or ""):
        attrs, inner = match.groups()
        cells.append({"attrs": attrs, "class": _extract_attr(attrs, "class"), "html": inner, "text": _strip_html(inner)})
    return cells


def _cell_by_class(cells: list[dict[str, str]], class_name: str) -> str:
    for cell in cells:
        classes = set(re.split(r"\s+", cell.get("class", "")))
        if class_name in classes:
            return cell.get("text", "")
    return ""


def _cell_html_by_class(cells: list[dict[str, str]], class_name: str) -> str:
    for cell in cells:
        classes = set(re.split(r"\s+", cell.get("class", "")))
        if class_name in classes:
            return cell.get("html", "")
    return ""


def _parse_table_rows(html: str, base_url: str, profile: str) -> list[dict[str, Any]]:
    rows = []
    for row_html in re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>", html or ""):
        if "/kcms2/article/abstract" not in row_html and "fz14" not in row_html:
            continue
        links = _html_links(row_html, base_url)
        title_link = next((link for link in links if "/kcms2/article/abstract" in link["href"]), links[0] if links else {})
        cells = _extract_cells(row_html)
        raw_texts = [cell["text"] for cell in cells]
        author_html = _cell_html_by_class(cells, "author")
        author_links = _html_links(author_html, base_url)
        authors = "; ".join(link["text"] for link in author_links if link["text"]) or _cell_by_class(cells, "author")
        export_match = re.search(r"""(?is)<input\b[^>]*class=['"][^'"]*\bcbItem\b[^'"]*['"][^>]*>""", row_html)
        export_id = _extract_attr(export_match.group(0), "value") if export_match else ""

        row = {
            "title": title_link.get("text", ""),
            "authors": authors,
            "journal": _cell_by_class(cells, "source"),
            "date": _cell_by_class(cells, "date"),
            "database": _cell_by_class(cells, "data"),
            "citations": _cell_by_class(cells, "quote"),
            "downloads": _cell_by_class(cells, "download"),
            "detail_url": title_link.get("href", ""),
            "export_id": export_id,
            "is_online_first": "marktip" in row_html,
            "parser_profile": profile,
            "raw_cells": raw_texts[:12],
        }
        if not row["journal"] and len(raw_texts) >= 4:
            row["journal"] = raw_texts[3]
        if not row["date"]:
            row["date"] = next((text for text in raw_texts if re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", text)), "")
        if not row["citations"] and len(raw_texts) >= 6:
            row["citations"] = raw_texts[-3] if raw_texts[-3].isdigit() else ""
        if not row["downloads"] and len(raw_texts) >= 7:
            row["downloads"] = raw_texts[-2] if raw_texts[-2].isdigit() else ""
        row.update(_extract_action_links(links))
        rows.append(row)
    return rows


def _parse_sentence_rows(html: str, base_url: str) -> list[dict[str, Any]]:
    rows = []
    for row_html in re.findall(r"(?is)<dd\b[^>]*>(.*?)</dd>", html or ""):
        if "/kcms2/article/abstract" not in row_html and "fz14" not in row_html:
            continue
        links = _html_links(row_html, base_url)
        title_link = next((link for link in links if "/kcms2/article/abstract" in link["href"]), links[0] if links else {})
        base_match = re.search(r"""(?is)<[^>]*class=['"][^'"]*\bbaseinfo\b[^'"]*['"][^>]*>(.*?)</[^>]+>""", row_html)
        baseinfo = _strip_html(base_match.group(1)) if base_match else _strip_html(row_html)
        author_match = re.search(r"作者：\s*(.+?)(?:\s*【|\s+来源：|$)", baseinfo)
        journal_match = re.search(r"来源：\s*(.+?)(?:\s+\d{4}|\s+发表时间：|$)", baseinfo)
        date_match = re.search(r"(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)", baseinfo)
        context_match = re.search(r"""(?is)<h5\b[^>]*>(.*?)</h5>""", row_html)
        export_match = re.search(r"""(?is)<input\b[^>]*class=['"][^'"]*\bcbItem\b[^'"]*['"][^>]*>""", row_html)
        row = {
            "title": title_link.get("text", ""),
            "authors": author_match.group(1).strip() if author_match else "",
            "journal": journal_match.group(1).strip() if journal_match else "",
            "date": date_match.group(1).strip() if date_match else "",
            "database": "",
            "citations": "",
            "downloads": "",
            "detail_url": title_link.get("href", ""),
            "export_id": _extract_attr(export_match.group(0), "value") if export_match else "",
            "is_online_first": False,
            "sentence_context": _strip_html(context_match.group(1)) if context_match else "",
            "parser_profile": "sentence_list",
            "raw_text": _strip_html(row_html)[:500],
        }
        row.update(_extract_action_links(links))
        rows.append(row)
    return rows


def parser_profile_for(spec: SearchSpec) -> str:
    if spec.search_mode == "sentence":
        return "sentence_list"
    if spec.doc_type in {"thesis", "phd", "masters"}:
        return "thesis_table"
    return "grid_table"


def parse_result_html(html: str, base_url: str, spec: SearchSpec) -> dict[str, Any]:
    profile = parser_profile_for(spec)
    rows = _parse_sentence_rows(html, base_url) if profile == "sentence_list" else _parse_table_rows(html, base_url, profile)
    total_match = re.search(r"(?is)(?:共找到|找到)\s*(?:</?[^>]+>\s*)*([\d,]+)\s*(?:</?[^>]+>\s*)*(?:条|篇)", html or "")
    if not total_match:
        total_match = re.search(r"(?:找到|共)\s*([\d,]+)\s*(?:条|篇)", _strip_html(html or ""))
    page_match = re.search(r"(?is)countPageMark[^>]*>\s*([^<]+)", html or "")
    turnpage_match = re.search(r"""(?is)\bid=["']hidTurnPage["'][^>]*\bvalue=["']([^"']+)["']""", html or "")
    return {
        "total": total_match.group(1) if total_match else "",
        "page": _strip_html(page_match.group(1)) if page_match else "",
        "turnpage": unescape(turnpage_match.group(1)).strip() if turnpage_match else "",
        "results": rows,
        "result_format": profile,
    }


def run_http_advsearch_workflow(spec: SearchSpec) -> dict[str, Any]:
    cookie, cookie_source = _load_http_cookie()
    query_builder = QueryJsonBuilder(spec)
    query_json = query_builder.build()
    form_builder = SearchFormBuilder(spec, query_json)
    endpoint = endpoint_for_search_mode(spec.search_mode)
    timeout = float(os.environ.get("CNKI_HTTP_TIMEOUT", "25"))
    max_retries = max(0, int(os.environ.get("CNKI_HTTP_RETRIES", "2")))
    backoff = max(0.0, float(os.environ.get("CNKI_HTTP_RETRY_BACKOFF", "0.75")))

    pages = []
    warnings = list(query_builder.warnings)
    total = ""
    turnpage = ""
    no_results_payload = {
        "query": spec.query,
        "language": spec.language,
        "search_mode": spec.search_mode,
        "doc_type": DOC_TYPE_RESOURCE.get(spec.doc_type, ("", ""))[0],
        "discipline": spec.discipline,
        "quality": spec.quality,
        "elite_uni": spec.elite_uni,
        "sort": spec.sort,
        "date_from": spec.date_from,
        "date_to": spec.date_to,
        "form_filters": spec.form_filters,
        "fields": spec.fields,
        "page_count": 1,
        "total": 0,
        "noResultsMessage": "抱歉，暂无数据，请稍后重试。",
        "pages": [],
        "warnings": warnings,
        "transport": "http",
        "cookie_source": cookie_source,
    }

    def execute_form(form: dict[str, str], page_no: int) -> dict[str, Any] | None:
        response = None
        state = "network_error"
        for attempt in range(max_retries + 1):
            with _HTTP_SEMAPHORE:
                response = _request("POST", endpoint, cookie, form, timeout)
            state = _detect_state(response)
            if state in {"ok", "no_results"}:
                break
            if attempt < max_retries and state in {"network_error", "rate_limited", "http_error", "empty_body"}:
                time.sleep(backoff * (2 ** attempt))
                continue
            break
        if response is None:
            raise HttpSearchUnavailable("http_request_not_attempted")
        if state == "no_results":
            return None
        if state != "ok":
            raise HttpSearchUnavailable(
                f"http_{state}",
                f"direct HTTP search returned state {state}",
                http_status=response.status,
                elapsed_ms=response.elapsed_ms,
            )
        parsed = parse_result_html(response.body, response.url, spec)
        if not parsed["results"] and not parsed.get("total"):
            raise HttpSearchUnavailable(
                "http_unknown_result_structure",
                "direct HTTP response did not contain recognizable result rows or total count",
                http_status=response.status,
                content_length=len(response.body or ""),
                page_no=page_no,
            )
        return parsed

    if spec.sort != "date":
        form, form_warnings = form_builder.build(1, request_kind="initial")
        warnings.extend(form_warnings)
        parsed = execute_form(form, 1)
        if parsed is None:
            no_results_payload["warnings"] = warnings
            return no_results_payload
        total = total or parsed.get("total", "")
        turnpage = parsed.get("turnpage") or turnpage

    for page_no in range(1, max(1, int(spec.page_limit)) + 1):
        request_kind = "sort" if page_no == 1 and spec.sort != "date" else ("page" if page_no > 1 else "initial")
        form, form_warnings = form_builder.build(page_no, turnpage=turnpage, request_kind=request_kind)
        warnings.extend(form_warnings)
        parsed = execute_form(form, page_no)
        if parsed is None:
            if pages:
                break
            no_results_payload["warnings"] = warnings
            return no_results_payload
        total = total or parsed.get("total", "")
        turnpage = parsed.get("turnpage") or turnpage
        pages.append({"page_no": page_no, **parsed})
        if len(parsed["results"]) < int(spec.page_size):
            break

    return {
        "query": spec.query,
        "language": spec.language,
        "search_mode": spec.search_mode,
        "doc_type": DOC_TYPE_RESOURCE.get(spec.doc_type, ("", ""))[0],
        "discipline": spec.discipline,
        "quality": spec.quality,
        "elite_uni": spec.elite_uni,
        "sort": spec.sort,
        "date_from": spec.date_from,
        "date_to": spec.date_to,
        "form_filters": spec.form_filters,
        "fields": spec.fields,
        "page_count": len(pages),
        "total": total,
        "pages": pages,
        "warnings": warnings,
        "transport": "http",
        "endpoint": endpoint,
        "cookie_source": cookie_source,
    }


