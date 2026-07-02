"""Fieldset resolution and row projection for cnki-search responses."""

SEARCH_BASIC_FIELDS = [
    "row_id",
    "global_rank",
    "page_no",
    "page_row_no",
    "title",
    "authors",
    "date",
    "journal",
    "database",
    "citations",
    "downloads",
    "is_online_first",
]

SEARCH_EXTENDED_FIELDS = SEARCH_BASIC_FIELDS + [
    "detail_url",
    "export_id",
    "pdf_url",
    "caj_url",
    "download_url",
]

DETAIL_BASIC_FIELDS = [
    "row_id",
    "global_rank",
    "title",
    "authors",
    "journal",
    "date",
    "abstract",
    "keywords",
    "fund",
    "classification",
    "pub_info",
    "citation_info",
    "detail_status",
    "detail_error",
]

DETAIL_FULL_FIELDS = DETAIL_BASIC_FIELDS + [
    "authors_structured",
    "affiliations",
    "toc",
    "raw_url",
]

DOWNLOAD_BASIC_FIELDS = [
    "row_id",
    "global_rank",
    "title",
    "download_status",
    "download_format",
    "saved_to",
    "filename",
    "download_error",
]

DOWNLOAD_FULL_FIELDS = DOWNLOAD_BASIC_FIELDS + [
    "download_transport",
    "download_strategy",
]

EXPORT_BASIC_FIELDS = [
    "row_id",
    "global_rank",
    "title",
    "export_status",
    "export_modes",
    "exports",
    "export_error",
    "mode_errors",
]

EXPORT_FULL_FIELDS = EXPORT_BASIC_FIELDS + [
    "export_transport",
    "export_batch",
]

FIELD_GROUPS = {
    "search_basic": SEARCH_BASIC_FIELDS,
    "search_extended": SEARCH_EXTENDED_FIELDS,
    "detail_basic": DETAIL_BASIC_FIELDS,
    "detail_full": DETAIL_FULL_FIELDS,
    "download_basic": DOWNLOAD_BASIC_FIELDS,
    "download_full": DOWNLOAD_FULL_FIELDS,
    "export_basic": EXPORT_BASIC_FIELDS,
    "export_full": EXPORT_FULL_FIELDS,
}

ALL_FIELDS = sorted({
    *SEARCH_EXTENDED_FIELDS,
    *DETAIL_FULL_FIELDS,
    *DOWNLOAD_FULL_FIELDS,
    *EXPORT_FULL_FIELDS,
    "detail_ref",
    "download_path",
    "fetched_at",
})


def _dedupe(items):
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def resolve_return_fields(values, default_group):
    if not values:
        return list(FIELD_GROUPS[default_group])

    tokens = []
    for value in values:
        if value is None:
            continue
        for token in str(value).split(","):
            token = token.strip()
            if token:
                tokens.append(token)

    resolved = []
    for token in tokens:
        if token in FIELD_GROUPS:
            resolved.extend(FIELD_GROUPS[token])
            continue
        if token == "all":
            resolved.extend(ALL_FIELDS)
            continue
        if token not in ALL_FIELDS:
            raise ValueError(f"unsupported return field: {token}")
        resolved.append(token)

    return _dedupe(resolved)


def merge_row_with_detail(row, detail):
    merged = dict(row or {})
    if detail:
        merged.update(detail)
    return merged


URL_SENSITIVE_FIELDS = frozenset([
    "detail_url",
    "export_id",
    "raw_url",
    "url",
    "rawUrl",
    "pdf_url",
    "caj_url",
    "download_url",
    "order_url",
    "final_url",
    "route_url",
])


def strip_url_fields(returned_fields, rows, debug=False):
    """Remove URL-sensitive fields from output unless debug mode is on."""
    if debug:
        return returned_fields, rows
    clean_fields = [field for field in returned_fields if field not in URL_SENSITIVE_FIELDS]
    clean_rows = [{key: value for key, value in (row or {}).items() if key not in URL_SENSITIVE_FIELDS} for row in rows]
    return clean_fields, clean_rows


def project_row(source, fields):
    return {field: source.get(field) for field in fields}
