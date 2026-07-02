"""HTTP-only shared helpers for cnki-search."""

from __future__ import annotations

import json
import os
import pathlib
import urllib.error
import urllib.request

IP_LOGIN_URL = "https://login.cnki.net/TopLoginCore/api/loginapi/IpLoginFlushPo"

ADVSEARCH_MODES = {"advsearch", "professional", "author", "sentence"}

DOC_TYPE_MAP: dict[str, str] = {
    "all": "WD0FTY92",
    "journal": "YSTT4HG0",
    "thesis": "LSTPFY1C",
    "phd": "RMJLXHZ3",
    "masters": "JQIRZIYA",
    "conference": "JUP3MUPD",
    "domestic-conf": "1UR4K4HZ",
    "intl-conf": "BPBAFJ5S",
}

DISCIPLINE_CODES: frozenset[str] = frozenset("ABCDEFGHIJ")
JOURNAL_QUALITY_KEYS: frozenset[str] = frozenset({"cssci", "sci", "ei", "pku", "cscd", "wjci", "ami"})

ELITE_UNI_MAP: dict[str, str] = {
    "all": "EL=1*2",
    "first-class-uni": "EL=1",
    "first-class-disc": "EL=2",
}

THESIS_DOC_TYPES: frozenset[str] = frozenset({"thesis", "phd", "masters"})

SORT_KEY_MAP: dict[str, str] = {
    "relevance": "相关度",
    "date": "发表时间",
    "citations": "被引",
    "downloads": "下载",
    "comprehensive": "综合",
}

FORM_FILTER_MAP: dict[str, str] = {
    "oa": "OA=1",
    "fund": "JJWX=Y",
    "enhanced": "NPM=ZQ",
    "online_first": "WLSF=2 || NOT!WXZT=2",
}

FACET_GROUP_FIELD_MAP: dict[str, str] = {
    "subdiscipline": "CCL",
}

ADVSEARCH_FIELD_CODES: frozenset[str] = frozenset({
    "SU", "TKA", "KY", "TI", "FT",
    "AU", "FI", "RP", "AF", "FU",
    "AB", "CO", "RF", "CLC", "LY", "DOI",
})


class CookieSeedError(Exception):
    def __init__(self, code: str, detail: str = "", **extra) -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail
        self.extra = extra


def _load_cookie_seed():
    cookie, source = _ip_login_cookie_seed()
    if cookie:
        return cookie, source

    fallback_reason = source or "ip_login_unavailable"

    cookie = os.environ.get("CNKI_COOKIE", "").strip()
    if cookie:
        return cookie, f"env_fallback_after_{fallback_reason}"

    cookie_file = os.environ.get("CNKI_COOKIE_FILE", "").strip()
    if cookie_file:
        try:
            cookie = pathlib.Path(cookie_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise CookieSeedError("cookie_seed_read_failed", str(exc), cookie_file=cookie_file) from exc
        if cookie:
            return cookie, f"file_fallback_after_{fallback_reason}"

    return "", fallback_reason


def _cookie_header_from_set_cookie(headers):
    cookies = {}
    for header in headers:
        first = str(header or "").split(";", 1)[0].strip()
        if not first or "=" not in first:
            continue
        name, value = first.split("=", 1)
        name = name.strip()
        if name:
            cookies[name] = value
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def _ip_login_cookie_seed():
    if os.environ.get("CNKI_AUTO_IP_LOGIN", "1").strip().lower() in {"0", "false", "no", "off"}:
        return "", "disabled"
    request = urllib.request.Request(
        IP_LOGIN_URL,
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Safari/537.36"
            ),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace").strip().strip("()")
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            if not payload.get("IsSuccess"):
                return "", "ip_login_failed"
            cookie = _cookie_header_from_set_cookie(response.headers.get_all("Set-Cookie") or [])
            return cookie, "ip_login" if cookie else "ip_login_no_cookie"
    except (urllib.error.URLError, TimeoutError, OSError):
        return "", "ip_login_error"
