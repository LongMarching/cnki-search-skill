---
name: cnki-search
description: Use when the user wants to 搜论文 查论文 知网检索 CNKI搜索 下载论文 批量下载 论文详情 论文摘要, or needs CNKI paper search results, paper details, PDF/CAJ downloads, citation exports, facets, or workspace inspection
argument-hint: "search|fetch_details|download|export|discover_facets|inspect"
---

# CNKI Search

Use this skill from the project root. In the distributed Claude layout, the
skill lives at `.claude/skills/cnki-search`.

```powershell
python .claude/skills/cnki-search/run.py --help
```

All commands print structured JSON. Use `status`, `workspace_id`, `run_id`,
`summary`, `rows`, `warnings`, and `error/detail` from that JSON instead of
screen-scraping text.

Guarded CNKI states such as captcha, login-required, permission-denied,
source-app-invalid, empty-body, or format-mismatch are returned as structured
errors. Do not try to bypass those states; report them and refresh legitimate
access state if needed.

## Setup

PowerShell:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
python .claude/skills/cnki-search/run.py search "机器学习" --page 1
```

Bash:

```bash
export PYTHONIOENCODING=utf-8
python .claude/skills/cnki-search/run.py search "机器学习" --page 1
```

Cookie seeding tries CNKI IP-login first unless `CNKI_AUTO_IP_LOGIN=0`. If that
does not return a cookie, it falls back to `CNKI_COOKIE`, then
`CNKI_COOKIE_FILE`.

Useful environment variables for normal use: `CNKI_WORKSPACE_ID`,
`CNKI_WORKSPACE_DIR`, `CNKI_DOWNLOAD_DIR`, `CNKI_DOWNLOAD_MAX_CONCURRENCY`.
Advanced tuning variables: `CNKI_WORKSPACE_TTL_HOURS`,
`CNKI_WORKSPACE_LOCK_TIMEOUT`, `CNKI_HTTP_TIMEOUT`,
`CNKI_HTTP_DOWNLOAD_TIMEOUT`, `CNKI_HTTP_RETRIES`,
`CNKI_HTTP_RETRY_BACKOFF`, `CNKI_HTTP_MAX_CONCURRENCY`,
`CNKI_HTTP_PAGE_SIZE`, `CNKI_HTTP_MAX_PAGES_PER_COMMAND`,
`CNKI_DETAIL_MAX_CONCURRENCY`, `CNKI_EXPORT_MAX_CONCURRENCY`,
`CNKI_DETAIL_STOP_ON_CAPTCHA`, `CNKI_USER_AGENT`, `CNKI_REFERER`,
`CNKI_EXPORT_BATCH_QUICK`.

## Workflow

1. Run `search` first. It creates a short-lived workspace automatically when
   `--workspace` and `CNKI_WORKSPACE_ID` are absent.
2. Reuse the returned `workspace_id` and `run_id` for details, exports,
   downloads, facets, and inspection.
3. Use `--run` whenever a workspace has multiple runs or when handing work to
   subagents.
4. URL-sensitive fields are hidden by default. Use `--debug` only for
   diagnosis.
5. Workspaces expire after 12 hours by default.

For parallel subagents, the parent should search once, then give each subagent
the same `workspace_id`, the exact `run_id`, and non-overlapping row ranges:

```powershell
python .claude/skills/cnki-search/run.py fetch_details --workspace WORKSPACE --run RUN --rows 1-20
python .claude/skills/cnki-search/run.py export --workspace WORKSPACE --run RUN --rows 21-40
python .claude/skills/cnki-search/run.py download --workspace WORKSPACE --run RUN --rows 1-5 --format pdf
```

Subagents should not rely on the active run, should not refresh search runs
unless explicitly asked, and should return the JSON payload.

## Row Selection

Commands that operate on stored rows accept:

- `--rows 1 5 9`
- `--rows 1,5,9`
- `--rows 1-10`
- `--top 5`
- `--pending-only` when supported
- `--sample N` for a random sample from the resolved candidate set

## search

Search CNKI and create or update a run inside a workspace.

```powershell
python .claude/skills/cnki-search/run.py search [query] [options]
```

Core options:

- `query`: search query or professional expression. Optional when `--fields`
  is provided, or for author/sentence mode when mode-specific fields are used.
- `--workspace WORKSPACE`: workspace id. Defaults to `CNKI_WORKSPACE_ID`; search
  auto-creates when omitted.
- `--run RUN`: run id inside the workspace. Defaults to the active run when
  applicable.
- `--label LABEL`: research direction label included in the run signature.
- `--topic TOPIC`: research topic included in the run signature.
- `--page N`: result page to return. Default: `1`.
- `--pages 1-3` or `--pages 1 3`: load one or more pages into the same run.
- `--refresh`: refresh and overwrite the matching run instead of reusing cached
  pages.
- `--activate`: set this run as the workspace active run. Default.
- `--no-activate`: do not change the workspace active run.
- `--output-limit N`: limit returned rows after page selection; `0` means no
  extra limit.
- `--language zh|en|both`: language scope. Default: `zh`.
- `--return-fields FIELD...`: field groups or individual field names.
- `--debug`: include URL-sensitive fields.

Search mode options:

- `--search-mode advsearch`: advanced search. Default.
- `--search-mode professional`: professional expression search; `query` is the
  full expression.
- `--search-mode author`: author search. Use `--author` and optionally
  `--affiliation`; if `--author` is absent, `query` is used as the author.
- `--search-mode sentence`: sentence search. Use `--word1`, `--word2`, and
  `--proximity NEAR|SEN`; if `--word1` is absent, `query` is used as word1.
- `--author AUTHOR`: author name for author mode.
- `--affiliation AFFILIATION`: affiliation filter for author mode.
- `--word1 WORD`: first sentence-search keyword.
- `--word2 WORD`: second sentence-search keyword.
- `--proximity NEAR|SEN`: `NEAR` means same sentence; `SEN` means same
  paragraph. Default: `NEAR`.

Filters:

- `--doc-type all|journal|thesis|phd|masters|conference|domestic-conf|intl-conf`
- `--discipline NAME_OR_CODE...`: discipline names or tree codes such as
  `信息科技`, `计算机软件及计算机应用`, `I`, `I138`, `I138_1`.
- `--subdiscipline NAME_OR_CODE...`: deprecated alias merged into
  `--discipline`; only use for compatibility with older prompts.
- `--quality KEY...`: journal quality filters. Supported keys: `cssci`, `sci`,
  `ei`, `pku`, `cscd`, `wjci`, `ami`.
- `--elite-uni all|first-class-uni|first-class-disc`: thesis elite-university
  filters for `thesis`, `phd`, or `masters`.
- `--sort date|relevance|citations|downloads|comprehensive`
- `--date-from YEAR_OR_DATE`
- `--date-to YEAR_OR_DATE`
- `--fields JSON`: multi-field advanced query. JSON array of
  `{field,value,op?,precision?}`. Valid field codes:
  `SU`, `TKA`, `KY`, `TI`, `FT`, `AU`, `FI`, `RP`, `AF`, `FU`, `AB`, `CO`,
  `RF`, `CLC`, `LY`, `DOI`. `op` is `AND`, `OR`, or `NOT`; default `AND`.
  `precision` is `exact` or `fuzzy`; default `exact`.
- `--form-filters KEY...`: `oa`, `fund`, `enhanced`, `online_first`.

Verified search examples:

```powershell
# Basic advanced search.
python .claude/skills/cnki-search/run.py search "机器学习" --page 1 --output-limit 5 --return-fields search_basic

# Multi-field search. In PowerShell, wrap the JSON in single quotes.
python .claude/skills/cnki-search/run.py search --fields '[{"field":"TI","value":"机器学习"},{"field":"KY","op":"AND","value":"深度学习"}]' --page 1 --output-limit 5 --return-fields search_basic

# Filtered journal search.
python .claude/skills/cnki-search/run.py search "大数据" --doc-type journal --quality cssci --discipline 信息科技 --date-from 2020 --sort citations --output-limit 5

# Professional search. This syntax was live-verified with CNKI HTTP search.
python .claude/skills/cnki-search/run.py search "SU='机器学习'" --search-mode professional --output-limit 5

# Author search.
python .claude/skills/cnki-search/run.py search --search-mode author --author "张伟" --output-limit 5

# Sentence search. NEAR is same sentence; SEN is same paragraph.
python .claude/skills/cnki-search/run.py search --search-mode sentence --word1 "机器学习" --word2 "医学影像" --proximity NEAR --output-limit 5
```

On 2026-07-01, the examples above were checked against live HTTP search and
returned `status: ok`, result rows, and `summary.search_transport: http`.

## fetch_details

Fetch detail pages for selected rows, parse detail fields, and write discovered
PDF/CAJ direct links back to the run.

```powershell
python .claude/skills/cnki-search/run.py fetch_details --workspace WORKSPACE --run RUN --rows 1,3-5,10
python .claude/skills/cnki-search/run.py fetch_details --workspace WORKSPACE --run RUN --top 5
python .claude/skills/cnki-search/run.py fetch_details --workspace WORKSPACE --run RUN --pending-only
```

Options:

- `--workspace WORKSPACE`
- `--run RUN`
- `--rows ROWS...`
- `--top N`
- `--pending-only`: only rows whose `detail_status` is still `pending`.
- `--sample N`
- `--return-fields FIELD...`
- `--refresh-existing`: re-fetch details even if already stored.
- `--debug`

## download

Download PDF or CAJ files for selected rows. Missing direct links are refreshed
through detail metadata automatically.

```powershell
python .claude/skills/cnki-search/run.py download --workspace WORKSPACE --run RUN --rows 1-3 --format pdf
python .claude/skills/cnki-search/run.py download --workspace WORKSPACE --run RUN --top 3 --format caj
python .claude/skills/cnki-search/run.py download --workspace WORKSPACE --run RUN --pending-only --format pdf
```

Options:

- `--workspace WORKSPACE`
- `--run RUN`
- `--rows ROWS...`
- `--top N`
- `--pending-only`: only rows not yet downloaded.
- `--sample N`
- `--format pdf|caj`: default `pdf`.
- `--dir PATH`: download directory. Default is
  `<project-root>/cnki-search-download/PDF` for PDF and
  `<project-root>/cnki-search-download/CAJ` for CAJ; relative paths resolve
  from the project root.
- `--return-fields FIELD...`: usually `download_basic` or `download_full`.
- `--redownload`: download again even if already downloaded.
- `--concurrency N`: direct HTTP download concurrency. Defaults to
  `CNKI_DOWNLOAD_MAX_CONCURRENCY` or `4`.
- `--debug`

Protected pages, login redirects, non-file responses, and wrong-format
responses are reported as structured row errors such as `login_required`,
`permission_denied`, `format_mismatch`, or `source_app_invalid`.

## export

Export citations or bibliography text for selected rows.

```powershell
python .claude/skills/cnki-search/run.py export --workspace WORKSPACE --run RUN --rows 1-5 --mode GBTREFER MLA APA
python .claude/skills/cnki-search/run.py export --workspace WORKSPACE --run RUN --top 3 --mode BibTex EndNote NoteExpress Refworks NodeFirst
python .claude/skills/cnki-search/run.py export --workspace WORKSPACE --run RUN --rows 1 --mode REFER NEW --file-type txt
```

Options:

- `--workspace WORKSPACE`
- `--run RUN`
- `--rows ROWS...`
- `--top N`
- `--sample N`
- `--mode MODE...`: `GBTREFER`, `MLA`, `APA`, `BibTex`, `EndNote`,
  `NoteExpress`, `Refworks`, `NodeFirst`, `REFER`, `NEW`.
- `--file-type txt|xls|doc`: FileToText output type for file-style formats.
  Default: `txt`.
- `--return-fields FIELD...`: usually `export_basic` or `export_full`.
- `--refresh-existing`: re-export even if a matching workspace artifact exists.
- `--debug`

Matching exports are reused inside the workspace unless `--refresh-existing` is
passed.

## discover_facets

Replay a stored search and inspect available result-page facet options.

```powershell
python .claude/skills/cnki-search/run.py discover_facets --workspace WORKSPACE --run RUN --group subdiscipline
```

Options:

- `--workspace WORKSPACE`
- `--run RUN`
- `--group subdiscipline`: currently the only supported group.
- `--debug`

## inspect

Recover workspace/run context, list runs, or retrieve stored rows without
rerunning search.

```powershell
python .claude/skills/cnki-search/run.py inspect --workspace WORKSPACE --view summary
python .claude/skills/cnki-search/run.py inspect --workspace WORKSPACE --view runs
python .claude/skills/cnki-search/run.py inspect --workspace WORKSPACE --run RUN --view rows --page 2
python .claude/skills/cnki-search/run.py inspect --workspace WORKSPACE --run RUN --rows 21-40 --return-fields search_basic
```

Options:

- `--workspace WORKSPACE`
- `--run RUN`
- `--rows ROWS...`
- `--page N`
- `--return-fields FIELD...`
- `--view summary|runs|rows`: default `rows`.
- `--debug`

## Return Fields

Use `--return-fields` with a group name, a comma-separated list, repeated
tokens, individual field names, or `all`.

| Group | Fields |
| --- | --- |
| `search_basic` | `row_id`, `global_rank`, `page_no`, `page_row_no`, `title`, `authors`, `date`, `journal`, `database`, `citations`, `downloads`, `is_online_first` |
| `search_extended` | `search_basic` plus `detail_url`, `export_id`, `pdf_url`, `caj_url`, `download_url` |
| `detail_basic` | `row_id`, `global_rank`, `title`, `authors`, `journal`, `date`, `abstract`, `keywords`, `fund`, `classification`, `pub_info`, `citation_info`, `detail_status`, `detail_error` |
| `detail_full` | `detail_basic` plus `authors_structured`, `affiliations`, `toc`, `raw_url` |
| `download_basic` | `row_id`, `global_rank`, `title`, `download_status`, `download_format`, `saved_to`, `filename`, `download_error` |
| `download_full` | `download_basic` plus `download_transport`, `download_strategy` |
| `export_basic` | `row_id`, `global_rank`, `title`, `export_status`, `export_modes`, `exports`, `export_error`, `mode_errors` |
| `export_full` | `export_basic` plus `export_transport`, `export_batch` |

URL-sensitive fields are removed unless `--debug` is set.

## Common Errors

| Error | Meaning | What to do |
| --- | --- | --- |
| `workspace_required` | Non-search command has no workspace. | Run `search` first or pass `--workspace`. |
| `run_required` | Workspace has no active run or multiple runs are ambiguous. | Pass `--run`. |
| `workspace_lock_timeout` | Another writer held the run lock too long. | Retry or split row ranges. |
| `no_selection` | Missing `--rows`, `--top`, or `--pending-only`. | Provide a row selection. |
| `direct_url_missing` | No direct PDF/CAJ order link was found. | Let `download` refresh details or run `fetch_details`. |
| `format_mismatch` | A direct order link returned HTML/JSON or another non-file response. | Treat as guarded/non-file response. |
| `login_required` / `captcha` / `permission_denied` | CNKI access state blocked the request. | Refresh legitimate access state, reduce request scope, or report the guarded result. |
