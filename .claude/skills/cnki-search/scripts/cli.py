"""cnki-search public entrypoint."""

import argparse
import json
import os
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(SKILL_DIR, "src")
sys.path.insert(0, SRC_DIR)
sys.path.insert(0, SKILL_DIR)
sys.path.insert(0, os.path.dirname(SKILL_DIR))

from actions import (
    discover_facets_action,
    download_action,
    export_action,
    fetch_details_action,
    inspect_action,
    search_action,
)


def _configure_stdio():
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


URL_SENSITIVE_KEYS = frozenset([
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


def _strip_urls(obj, debug=False):
    """Recursively strip URL-sensitive keys from any JSON-serializable object."""
    if debug:
        return obj
    if isinstance(obj, dict):
        return {k: _strip_urls(v, debug=False) for k, v in obj.items() if k not in URL_SENSITIVE_KEYS}
    if isinstance(obj, list):
        return [_strip_urls(item, debug=False) for item in obj]
    return obj


def _parse_rows(values):
    result = []
    for value in values or []:
        for token in str(value).split(","):
            token = token.strip()
            if not token:
                continue
            if "-" in token:
                parts = token.split("-")
                if len(parts) != 2:
                    raise ValueError(f"invalid row range: {token}")
                try:
                    start = int(parts[0].strip())
                    end = int(parts[1].strip())
                except ValueError as exc:
                    raise ValueError(f"invalid row range: {token}") from exc
                if start <= 0 or end <= 0:
                    raise ValueError(f"row range must be positive: {token}")
                step = 1 if end >= start else -1
                result.extend(range(start, end + step, step))
                continue
            try:
                number = int(token)
            except ValueError as exc:
                raise ValueError(f"invalid row index: {token}") from exc
            if number <= 0:
                raise ValueError(f"row index must be positive: {token}")
            result.append(number)
    return result


def _parse_pages(value):
    if not value:
        return None
    return _parse_rows([value] if isinstance(value, str) else value)


def _add_workspace_args(parser, include_run=True):
    parser.add_argument("--workspace", help="Short-lived workspace id (default: CNKI_WORKSPACE_ID; search auto-creates when omitted)")
    if include_run:
        parser.add_argument("--run", dest="run_id", help="Search run id inside the workspace (default: active run)")


def build_parser():
    parser = argparse.ArgumentParser(description="CNKI search orchestration")
    subparsers = parser.add_subparsers(dest="action", required=True)

    search = subparsers.add_parser("search", help="Search CNKI papers and build a workflow result table")
    search.add_argument("query", nargs="?", default="", help="Search query or professional expression (can be omitted when --fields is given)")
    _add_workspace_args(search)
    search.add_argument("--label", default="", help="Research direction label included in the search signature")
    search.add_argument("--topic", default="", help="Research topic included in the search signature")
    search.add_argument("--page", type=int, default=1, help="Result page to return (default: 1)")
    search.add_argument("--pages", nargs="+", help="Result page range(s), e.g. 1-3 or 1 3")
    search.add_argument("--refresh", action="store_true", help="Refresh and overwrite the matching run instead of reusing cached pages")
    search.add_argument("--activate", dest="activate", action="store_true", default=True, help="Set the run as the workspace active run (default)")
    search.add_argument("--no-activate", dest="activate", action="store_false", help="Do not change the active run")
    search.add_argument("--output-limit", type=int, help="Limit returned rows after page selection; 0 means no extra limit")
    search.add_argument("--language", default="zh", help="zh, en, or both")
    search.add_argument("--return-fields", nargs="+", help="Field group(s) or field names to return")
    search.add_argument(
        "--search-mode",
        default="advsearch",
        choices=["advsearch", "professional", "author", "sentence"],
        help=(
            "advsearch: kns8s/AdvSearch grade search with query as 主题 (default). "
            "professional: kns8s/AdvSearch professional textarea (query = full expression). "
            "author: kns8s/AdvSearch author search (use --author / --affiliation). "
            "sentence: kns8s/AdvSearch sentence search (use --word1 / --word2 / --proximity)."
        ),
    )
    search.add_argument("--author", help="Author name for --search-mode author")
    search.add_argument("--affiliation", help="Author affiliation for --search-mode author")
    search.add_argument("--word1", help="First keyword for --search-mode sentence")
    search.add_argument("--word2", help="Second keyword for --search-mode sentence")
    search.add_argument(
        "--proximity",
        choices=["NEAR", "SEN"],
        default="NEAR",
        help="Proximity operator for --search-mode sentence (NEAR=同一句, SEN=同一段)",
    )
    search.add_argument(
        "--doc-type",
        default="all",
        choices=["all", "journal", "thesis", "phd", "masters", "conference", "domestic-conf", "intl-conf"],
        help=(
            "Filter by document type (AdvSearch modes only). "
            "all=总库(default), journal=学术期刊, thesis=学位论文, phd=博士, masters=硕士, "
            "conference=会议, domestic-conf=国内会议, intl-conf=国际会议"
        ),
    )
    search.add_argument(
        "--discipline",
        nargs="+",
        metavar="NAME_OR_CODE",
        help=(
            "Sidebar tree discipline filter at any depth (AdvSearch modes only). "
            "Accepts Chinese names (信息科技, 计算机软件及计算机应用) or tree codes (I, I138, I138_1). "
            "Multiple values combine with OR logic. Scope: A=基础科学 through J=经济与管理."
        ),
    )
    search.add_argument(
        "--subdiscipline",
        nargs="+",
        metavar="NAME_OR_CODE",
        help=(
            "(DEPRECATED — merged into --discipline) "
            "Result-page discipline facet codes or Chinese labels."
        ),
    )
    search.add_argument(
        "--quality",
        nargs="+",
        metavar="KEY",
        help=(
            "Source-category quality filter(s) (AdvSearch + --doc-type journal only). "
            "cssci=CSSCI, sci=SCI来源期刊, ei=EI来源期刊, pku=北大核心, "
            "cscd=CSCD, wjci=WJCI, ami=AMI"
        ),
    )
    search.add_argument(
        "--elite-uni",
        choices=["all", "first-class-uni", "first-class-disc"],
        help=(
            "Elite-university thesis filter (AdvSearch + thesis/phd/masters only). "
            "all=双一流, first-class-uni=一流大学, first-class-disc=一流学科"
        ),
    )
    search.add_argument(
        "--sort",
        default="date",
        choices=["date", "relevance", "citations", "downloads", "comprehensive"],
        help=(
            "Sort order for results (AdvSearch modes only). "
            "date=发表时间(default/newest-first), relevance=相关度, "
            "citations=被引频次, downloads=下载频次, comprehensive=综合"
        ),
    )
    search.add_argument(
        "--date-from",
        metavar="YEAR_OR_DATE",
        help="Start of publication date range, e.g. 2020 or 2020-01-01 (AdvSearch modes only)",
    )
    search.add_argument(
        "--date-to",
        metavar="YEAR_OR_DATE",
        help="End of publication date range, e.g. 2024 or 2024-12-31 (AdvSearch modes only)",
    )
    search.add_argument(
        "--fields",
        metavar="JSON",
        help=(
            "Multi-field query for advsearch mode. JSON array of "
            '{field, value, op?, precision?}. '
            "field: SU/TKA/KY/TI/FT/AU/FI/RP/AF/FU/AB/CO/RF/CLC/LY/DOI. "
            "op: AND(default)/OR/NOT. precision: exact(default)/fuzzy. "
            'Example: \'[{"field":"TI","value":"机器学习"},{"field":"KY","op":"AND","value":"深度学习"}]\''
        ),
    )
    search.add_argument(
        "--form-filters",
        nargs="+",
        metavar="KEY",
        help=(
            "Pre-submit form filter checkboxes (AdvSearch modes only). "
            "oa=OA出版, fund=基金文献, enhanced=增强出版, online_first=网络首发"
        ),
    )
    search.add_argument("--debug", action="store_true", help="Include URL-sensitive fields (detail_url, raw_url, export_id) in output")

    fetch = subparsers.add_parser("fetch_details", help="Fetch detail pages for selected workspace rows")
    _add_workspace_args(fetch)
    fetch.add_argument("--rows", nargs="+", help="Result-row global ranks, e.g. 1 5, 1,5, or 1-5 (optional if --top or --pending-only is used)")
    fetch.add_argument("--top", type=int, metavar="N", help="Fetch details for the first N results (after --pending-only filter if combined)")
    fetch.add_argument("--pending-only", action="store_true", help="Only fetch rows whose detail_status is still 'pending'")
    fetch.add_argument("--sample", type=int, metavar="N", help="Randomly sample N rows from the resolved candidate set")
    fetch.add_argument("--return-fields", nargs="+", help="Field group(s) or field names to return")
    fetch.add_argument("--refresh-existing", action="store_true", help="Re-fetch details even if already stored")
    fetch.add_argument("--debug", action="store_true", help="Include URL-sensitive fields (detail_url, raw_url, export_id) in output")

    download = subparsers.add_parser("download", help="Download paper PDF/CAJ files for selected workspace rows")
    _add_workspace_args(download)
    download.add_argument("--rows", nargs="+", help="Result-row global ranks, e.g. 1 5, 1,5, or 1-5 (optional if --top or --pending-only is used)")
    download.add_argument("--top", type=int, metavar="N", help="Download first N results")
    download.add_argument("--pending-only", action="store_true", help="Only download rows not yet downloaded")
    download.add_argument("--sample", type=int, metavar="N", help="Randomly sample N rows from the resolved candidate set")
    download.add_argument("--format", default="pdf", choices=["pdf", "caj"], help="Download format (default: pdf)")
    download.add_argument("--dir", default=None, metavar="PATH", help="Download directory (default: <project-root>/cnki-search-download/PDF or CAJ; relative paths resolve from project root)")
    download.add_argument("--return-fields", nargs="+", help="Field group(s) or field names to return (download_basic, download_full)")
    download.add_argument("--redownload", action="store_true", help="Re-download even if already downloaded")
    download.add_argument("--concurrency", type=int, metavar="N", help="HTTP direct download concurrency (default: CNKI_DOWNLOAD_MAX_CONCURRENCY or 4)")
    download.add_argument("--debug", action="store_true", help="Include URL-sensitive fields in output")

    export = subparsers.add_parser("export", help="Export citations or bibliography text for selected workspace rows via HTTP")
    _add_workspace_args(export)
    export.add_argument("--rows", nargs="+", help="Result-row global ranks, e.g. 1 5, 1,5, or 1-5")
    export.add_argument("--top", type=int, metavar="N", help="Export first N results")
    export.add_argument("--sample", type=int, metavar="N", help="Randomly sample N rows from the resolved candidate set")
    export.add_argument(
        "--mode",
        nargs="+",
        default=["GBTREFER", "MLA", "APA"],
        help="Export format(s): GBTREFER MLA APA BibTex EndNote NoteExpress Refworks NodeFirst REFER NEW",
    )
    export.add_argument("--file-type", choices=["txt", "xls", "doc"], default="txt", help="FileToText output type for file-style formats")
    export.add_argument("--return-fields", nargs="+", help="Field group(s) or field names to return (export_basic, export_full)")
    export.add_argument("--refresh-existing", action="store_true", help="Re-export even if a matching workspace artifact exists")
    export.add_argument("--debug", action="store_true", help="Include URL-sensitive fields in output")

    discover = subparsers.add_parser("discover_facets", help="Replay a stored workspace search and read result-page facet options")
    _add_workspace_args(discover)
    discover.add_argument(
        "--group",
        choices=["subdiscipline"],
        default="subdiscipline",
        help="Facet group to inspect on the result page",
    )
    discover.add_argument("--debug", action="store_true", help="Include URL-sensitive fields in output")

    inspect = subparsers.add_parser("inspect", help="Inspect stored workspace state")
    _add_workspace_args(inspect)
    inspect.add_argument("--rows", nargs="+", help="Optional result-row global ranks, e.g. 1 5, 1,5, or 1-5")
    inspect.add_argument("--page", type=int, help="Return one stored result page")
    inspect.add_argument("--return-fields", nargs="+", help="Field group(s) or field names to return")
    inspect.add_argument("--view", choices=["summary", "runs", "rows"], default="rows", help="Inspect workspace summary, runs, or row projections")
    inspect.add_argument("--debug", action="store_true", help="Include URL-sensitive fields (detail_url, raw_url, export_id) in output")

    return parser


def main():
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.action == "search":
            if args.page and args.page <= 0:
                parser.error("--page must be a positive integer")
            _mode = args.search_mode
            _extra: dict = {}
            if _mode == "author":
                _extra = {"author": args.author or args.query, "affiliation": args.affiliation or ""}
            elif _mode == "sentence":
                _extra = {
                    "word1": args.word1 or args.query,
                    "word2": args.word2 or "",
                    "proximity": args.proximity or "NEAR",
                }
            payload = search_action(
                query=args.query,
                language=args.language,
                page_limit=1,
                return_fields=args.return_fields,
                search_mode=_mode,
                extra=_extra,
                doc_type=args.doc_type,
                discipline=args.discipline,
                subdiscipline=args.subdiscipline,
                quality=args.quality,
                elite_uni=args.elite_uni,
                sort=args.sort,
                date_from=args.date_from,
                date_to=args.date_to,
                fields=args.fields,
                form_filters=args.form_filters,
                workspace_id=args.workspace,
                run_id=args.run_id,
                label=args.label,
                topic=args.topic,
                page=args.page,
                pages=_parse_pages(args.pages),
                refresh=bool(args.refresh),
                activate=bool(args.activate),
                output_limit=args.output_limit,
                debug=bool(args.debug),
            )
        elif args.action == "fetch_details":
            payload = fetch_details_action(
                workspace_id=args.workspace,
                run_id=args.run_id,
                rows=_parse_rows(args.rows) if args.rows else None,
                top=args.top,
                pending_only=bool(args.pending_only),
                sample=args.sample,
                return_fields=args.return_fields,
                refresh_existing=bool(args.refresh_existing),
                debug=bool(args.debug),
            )
        elif args.action == "download":
            payload = download_action(
                workspace_id=args.workspace,
                run_id=args.run_id,
                rows=_parse_rows(args.rows) if args.rows else None,
                top=args.top,
                pending_only=bool(args.pending_only),
                sample=args.sample,
                fmt=args.format,
                download_dir=args.dir,
                return_fields=args.return_fields,
                redownload=bool(args.redownload),
                direct_concurrency=args.concurrency,
                debug=bool(args.debug),
            )
        elif args.action == "export":
            payload = export_action(
                workspace_id=args.workspace,
                run_id=args.run_id,
                rows=_parse_rows(args.rows) if args.rows else None,
                top=args.top,
                sample=args.sample,
                modes=args.mode,
                file_type=args.file_type,
                return_fields=args.return_fields,
                refresh_existing=bool(args.refresh_existing),
                debug=bool(args.debug),
            )
        elif args.action == "discover_facets":
            payload = discover_facets_action(
                workspace_id=args.workspace,
                run_id=args.run_id,
                group=args.group,
                debug=bool(args.debug),
            )
        else:
            payload = inspect_action(
                workspace_id=args.workspace,
                run_id=args.run_id,
                rows=_parse_rows(args.rows) if args.rows else None,
                return_fields=args.return_fields,
                view=args.view,
                page=args.page,
                debug=bool(args.debug),
            )
    except ValueError as exc:
        payload = {
            "status": "error",
            "workspace_id": "",
            "run_id": "",
            "action": args.action,
            "count": 0,
            "summary": {},
            "rows": [],
            "returned_fields": [],
            "warnings": [],
            "error": "invalid_arguments",
            "detail": str(exc),
        }

    payload = _strip_urls(payload, debug=bool(getattr(args, 'debug', False)))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())


