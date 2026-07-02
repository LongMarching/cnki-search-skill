"""Static CNKI discipline code/name mapping.

Source: navi.cnki.net discipline sidebar (Advanced Search page), crawled 2026-05-07.
Total: 3207 nodes (2730 leaf nodes, max depth 5).

Usage:
    from core.discipline_map import code_to_name, name_to_code, resolve_code

    code_to_name["I138"]          # "计算机软件及计算机应用"
    name_to_code.get("计算机软件及计算机应用")  # "I138"
    resolve_code("自动化技术")    # "I140"
"""

import json
import os

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Lazy-loaded from the packaged compact code/name resource.
_CODE_TO_NAME = None


def _load_code_to_name():
    global _CODE_TO_NAME
    if _CODE_TO_NAME is not None:
        return _CODE_TO_NAME
    _path = os.path.join(SKILL_DIR, "resources", "discipline_codes.json")
    with open(_path, "r", encoding="utf-8") as f:
        _CODE_TO_NAME = json.load(f)
    return _CODE_TO_NAME


def _build_maps():
    c2n = dict(_load_code_to_name())
    n2c = {}
    for code, name in c2n.items():
        norm = name.replace(" ", "").strip()
        if norm not in n2c:
            n2c[norm] = code
    return c2n, n2c


def code_to_name():
    """Return full {code: name} dict (3207 entries)."""
    c2n, _ = _build_maps()
    return c2n


def name_to_code():
    """Return {name: code} dict. Collisions: first-seen wins (depth-0 first)."""
    _, n2c = _build_maps()
    return n2c


def resolve_code(value):
    """Resolve a subdiscipline value to a CNKI code.

    Accepts both codes (e.g. "I138") and Chinese names
    (e.g. "计算机软件及计算机应用"). Returns the code string,
    or None if not found.
    """
    if not value or not value.strip():
        return None
    value = value.strip()
    # Direct code match (e.g. "I138")
    c2n, n2c = _build_maps()
    if value.upper() in c2n:
        return value.upper()
    # Exact name match (normalized)
    norm = value.replace(" ", "")
    if norm in n2c:
        return n2c[norm]
    # Substring match (last resort) — only for Chinese or multi-char values.
    # Single Latin letters would match arbitrary positions inside Chinese names
    # (e.g. "M" matches "CoMputer" inside a discipline name). Skip those.
    if len(norm) >= 2:
        for stored_name, code in n2c.items():
            if norm in stored_name or stored_name in norm:
                return code
    return None


def list_codes(prefix=None):
    """Return list of (code, name) tuples, optionally filtered by code prefix.

    Examples:
        list_codes("I")    # All 信息科技 codes
        list_codes("I138") # All sub-codes under 计算机软件及计算机应用
    """
    c2n, _ = _build_maps()
    if prefix:
        prefix = prefix.upper()
        return [(c, n) for c, n in sorted(c2n.items()) if c.upper().startswith(prefix)]
    return sorted(c2n.items())


