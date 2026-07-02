"""Live test harness for the cnki-search skill.

This script intentionally drives the public CLI in ``.claude/skills/cnki-search/run.py``
instead of importing workflow internals. Live execution must be explicitly
enabled with CNKI_LIVE_TEST=1 and a legitimate CNKI cookie seed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_PY = REPO_ROOT / ".claude" / "skills" / "cnki-search" / "run.py"
DEFAULT_DOWNLOAD_DIR = REPO_ROOT / "cnki-live-downloads"

MAX_RATE = 50.0
GUARDED_STATES = {
    "captcha",
    "http_captcha",
    "login_required",
    "detail_login_required",
    "facet_login_required",
    "auth_required",
    "permission_denied",
    "source_app_invalid",
    "format_mismatch",
    "login_loop",
    "account_risk",
    "security_check",
}
SENSITIVE_KEYS = {
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
    "legacy_params",
    "invoice",
    "cookie",
    "cookie_source",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run guarded live cnki-search tests against CNKI")
    parser.add_argument("--query", default="机器学习", help="Primary CNKI query")
    parser.add_argument("--download-dir", default=str(DEFAULT_DOWNLOAD_DIR), help="Directory used only when --include-downloads is set")
    parser.add_argument("--max-rate", type=float, default=5.0, help="Global token-bucket rate limit, max 50 req/s")
    parser.add_argument("--max-rows", type=int, default=10, help="Maximum rows used by downstream workflow actions")
    parser.add_argument("--include-downloads", action="store_true", help="Actually save PDF/CAJ files")
    parser.add_argument("--include-pressure", action="store_true", help="Run bounded live pressure phases")
    parser.add_argument("--pressure-profile", default="5:60,10:60,20:60,50:30", help="rate:seconds pairs for search pressure")
    parser.add_argument("--endpoint-pressure-seconds", type=float, default=10.0, help="Seconds per non-search pressure endpoint")
    parser.add_argument("--stop-on-guarded-error", dest="stop_on_guarded_error", action="store_true", default=True)
    parser.add_argument("--no-stop-on-guarded-error", dest="stop_on_guarded_error", action="store_false")
    parser.add_argument("--debug", action="store_true", help="Allow CLI debug output; disabled output is still sanitized")
    parser.add_argument("--dry-run", action="store_true", help="Validate harness arguments without contacting CNKI")
    parser.add_argument("--summary-out", default="", help="Optional path for the sanitized JSON summary")
    parser.add_argument("--keep-downloads", action="store_true", help="Keep verified downloaded files after the run")
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace, env: dict[str, str] | None = None, require_live: bool = True) -> list[str]:
    env = env or os.environ
    errors = []
    if args.max_rate <= 0:
        errors.append("--max-rate must be positive")
    if args.max_rate > MAX_RATE:
        errors.append("--max-rate must not exceed 50")
    if args.max_rows <= 0:
        errors.append("--max-rows must be positive")
    if args.endpoint_pressure_seconds < 0:
        errors.append("--endpoint-pressure-seconds must be non-negative")
    if require_live:
        if env.get("CNKI_LIVE_TEST") != "1":
            errors.append("CNKI_LIVE_TEST=1 is required for live tests")
        if env.get("CNKI_AUTO_IP_LOGIN", "1").strip().lower() in {"0", "false", "no", "off"} and not (
            env.get("CNKI_COOKIE") or env.get("CNKI_COOKIE_FILE")
        ):
            errors.append("CNKI_COOKIE or CNKI_COOKIE_FILE is required when CNKI_AUTO_IP_LOGIN is disabled")
    if args.include_downloads and not (args.download_dir or env.get("CNKI_DOWNLOAD_DIR")):
        errors.append("--include-downloads requires --download-dir or CNKI_DOWNLOAD_DIR")
    return errors


def sanitize_payload(value: Any, debug: bool = False) -> Any:
    if debug:
        return value
    if isinstance(value, dict):
        clean = {}
        for key, child in value.items():
            if key in SENSITIVE_KEYS:
                continue
            clean[key] = sanitize_payload(child, debug=False)
        return clean
    if isinstance(value, list):
        return [sanitize_payload(item, debug=False) for item in value]
    if isinstance(value, str):
        lowered = value.lower()
        if "cnki.net/" in lowered or "invoice=" in lowered or "export_id" in lowered:
            return "[redacted]"
    return value


def find_guarded_state(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("error", "download_status", "export_status", "status"):
            text = str(value.get(key, "") or "").strip()
            if text in GUARDED_STATES:
                return text
        for key in ("detail", "download_error", "export_error"):
            text = str(value.get(key, "") or "").lower()
            if any(state in text for state in GUARDED_STATES):
                return text
        for child in value.values():
            found = find_guarded_state(child)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_guarded_state(item)
            if found:
                return found
    return ""


def latency_stats(values: list[int]) -> dict[str, int]:
    if not values:
        return {"p50_ms": 0, "p95_ms": 0, "p99_ms": 0}
    sorted_values = sorted(values)

    def percentile(pct: float) -> int:
        if len(sorted_values) == 1:
            return sorted_values[0]
        index = min(len(sorted_values) - 1, max(0, round((len(sorted_values) - 1) * pct)))
        return int(sorted_values[index])

    return {"p50_ms": percentile(0.50), "p95_ms": percentile(0.95), "p99_ms": percentile(0.99)}


class RateLimiter:
    def __init__(self, rate: float):
        if rate <= 0:
            raise ValueError("rate must be positive")
        if rate > MAX_RATE:
            raise ValueError("rate must not exceed 50")
        self.interval = 1.0 / float(rate)
        self.next_at = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        if self.next_at > now:
            time.sleep(self.next_at - now)
        self.next_at = max(time.monotonic(), self.next_at) + self.interval


@dataclass
class CommandResult:
    label: str
    payload: dict[str, Any]
    returncode: int
    elapsed_ms: int
    stdout_preview: str = ""
    stderr_preview: str = ""


@dataclass
class CliRunner:
    env: dict[str, str]
    max_rate: float
    debug: bool = False
    limiter: RateLimiter = field(init=False)

    def __post_init__(self) -> None:
        self.limiter = RateLimiter(self.max_rate)

    def run(self, label: str, cli_args: list[str], env_overrides: dict[str, str] | None = None) -> CommandResult:
        self.limiter.wait()
        child_env = dict(self.env)
        child_env.update(env_overrides or {})
        if not self.debug:
            child_env.pop("CNKI_DEBUG", None)
        started = time.monotonic()
        proc = subprocess.run(
            [sys.executable, str(RUN_PY), *cli_args],
            cwd=str(REPO_ROOT),
            env=child_env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = {
                "status": "error",
                "action": label,
                "error": "invalid_json_output",
                "detail": proc.stdout[:500],
            }
        return CommandResult(
            label=label,
            payload=payload if isinstance(payload, dict) else {"status": "error", "error": "non_object_json"},
            returncode=proc.returncode,
            elapsed_ms=elapsed_ms,
            stdout_preview=proc.stdout[:500],
            stderr_preview=proc.stderr[:500],
        )


class LiveHarness:
    def __init__(self, args: argparse.Namespace, runner: CliRunner):
        self.args = args
        self.runner = runner
        self.steps: list[dict[str, Any]] = []
        self.latencies: list[int] = []
        self.workspace_id = ""
        self.run_id = ""
        self.blocked_state = ""
        self.failures: list[dict[str, Any]] = []
        self.file_checks: list[dict[str, Any]] = []
        self.store_checks: list[dict[str, Any]] = []

    def record(self, result: CommandResult, required_ok: bool = True) -> dict[str, Any]:
        payload = sanitize_payload(result.payload, debug=self.args.debug)
        guarded = find_guarded_state(result.payload)
        step = {
            "label": result.label,
            "returncode": result.returncode,
            "elapsed_ms": result.elapsed_ms,
            "status": payload.get("status", ""),
            "action": payload.get("action", result.label),
            "workspace_id": payload.get("workspace_id", ""),
            "run_id": payload.get("run_id", ""),
            "count": payload.get("count", 0),
            "error": payload.get("error", ""),
            "guarded_state": guarded,
            "warnings": payload.get("warnings", []),
        }
        self.steps.append(step)
        self.latencies.append(result.elapsed_ms)
        if guarded and not self.blocked_state:
            self.blocked_state = guarded
        if required_ok and result.returncode != 0 and not guarded:
            self.failures.append({"label": result.label, "error": step.get("error") or "command_failed"})
        return payload

    def should_stop(self) -> bool:
        return bool(self.args.stop_on_guarded_error and self.blocked_state)

    def run_cli(self, label: str, cli_args: list[str], required_ok: bool = True, env_overrides: dict[str, str] | None = None) -> dict[str, Any]:
        result = self.runner.run(label, cli_args, env_overrides=env_overrides)
        return self.record(result, required_ok=required_ok)

    def run(self) -> dict[str, Any]:
        env = {
            "CNKI_SEARCH_TRANSPORT": "http",
            "CNKI_SEARCH_HTTP_STRICT": "1",
            "CNKI_DOWNLOAD_DIR": str(Path(self.args.download_dir).resolve()),
        }
        max_rows = max(1, int(self.args.max_rows))
        top_details = min(5, max_rows)
        top_export = min(10, max_rows)

        primary = self.run_cli(
            "search_basic",
            ["search", self.args.query, "--page", "1", "--return-fields", "search_basic"],
            env_overrides=env,
        )
        self.workspace_id = str(primary.get("workspace_id") or "")
        self.run_id = str(primary.get("run_id") or "")
        if primary.get("status") == "ok" and int(primary.get("count") or 0) < 1:
            self.failures.append({"label": "search_basic", "error": "no_results"})
        if self.should_stop():
            return self.summary()

        fields = json.dumps(
            [
                {"field": "TI", "value": self.args.query},
                {"field": "KY", "op": "AND", "value": self.args.query},
            ],
            ensure_ascii=False,
        )
        self.run_cli(
            "search_multifield",
            ["search", "--fields", fields, "--page", "1", "--return-fields", "search_basic"],
            env_overrides=env,
        )
        if self.should_stop():
            return self.summary()

        self.run_cli(
            "search_combo_filters",
            [
                "search",
                self.args.query,
                "--doc-type",
                "journal",
                "--discipline",
                "信息科技",
                "--sort",
                "date",
                "--date-from",
                "2020",
                "--date-to",
                "2026",
                "--page",
                "1",
                "--return-fields",
                "search_basic",
            ],
            env_overrides=env,
        )
        if self.should_stop():
            return self.summary()

        if not self.workspace_id or not self.run_id:
            self.failures.append({"label": "mandatory_flow", "error": "missing_workspace_or_run_id"})
            return self.summary()

        stored_rows = self.load_stored_results(env)
        self.inspect_stored_direct_fields(stored_rows)

        self.run_cli("fetch_details", ["fetch_details", "--workspace", self.workspace_id, "--run", self.run_id, "--top", str(top_details), "--return-fields", "detail_full"], env_overrides=env)
        if self.should_stop():
            return self.summary()
        self.run_cli("discover_facets", ["discover_facets", "--workspace", self.workspace_id, "--run", self.run_id, "--group", "subdiscipline"], env_overrides=env)
        if self.should_stop():
            return self.summary()
        self.run_cli("export_quick", ["export", "--workspace", self.workspace_id, "--run", self.run_id, "--top", str(min(3, top_export)), "--mode", "GBTREFER", "MLA", "APA", "--return-fields", "export_full"], env_overrides=env)
        if self.should_stop():
            return self.summary()
        self.run_cli(
            "export_files",
            ["export", "--workspace", self.workspace_id, "--run", self.run_id, "--top", str(min(3, top_export)), "--mode", "BibTex", "EndNote", "NoteExpress", "Refworks", "NodeFirst", "--return-fields", "export_full"],
            env_overrides=env,
        )
        if self.should_stop():
            return self.summary()

        if self.args.include_downloads:
            self.run_downloads(env)
        else:
            self.steps.append({"label": "actual_downloads", "status": "skipped", "reason": "include_downloads_not_set"})

        if self.args.include_pressure:
            self.run_pressure(env)

        return self.summary()

    def workflow_root(self, env: dict[str, str]) -> Path:
        configured = (env.get("CNKI_WORKSPACE_DIR") or os.environ.get("CNKI_WORKSPACE_DIR") or "").strip()
        if configured:
            return Path(configured)
        return REPO_ROOT / ".claude" / "skills" / "cnki-search" / "cnki-workspaces"

    def load_stored_results(self, env: dict[str, str]) -> list[dict[str, Any]]:
        path = self.workflow_root(env) / self.workspace_id / "runs" / self.run_id / "results.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            self.store_checks.append({"label": "workspace_store_results", "status": "error", "error": type(exc).__name__})
            return []
        except json.JSONDecodeError:
            self.store_checks.append({"label": "workspace_store_results", "status": "error", "error": "invalid_json"})
            return []
        if not isinstance(payload, list):
            self.store_checks.append({"label": "workspace_store_results", "status": "error", "error": "not_a_list"})
            return []
        self.store_checks.append({"label": "workspace_store_results", "status": "ok", "row_count": len(payload)})
        return payload

    def inspect_stored_direct_fields(self, rows: list[dict[str, Any]]) -> None:
        direct_fields = {
            "detail_link": "detail_url",
            "export_token": "export_id",
            "pdf_link": "pdf_url",
            "caj_link": "caj_url",
            "generic_download_link": "download_url",
        }
        counts = {alias: sum(1 for row in rows if row.get(field)) for alias, field in direct_fields.items()}
        self.store_checks.append(
            {
                "label": "workspace_store_direct_fields",
                "status": "ok" if counts.get("detail_link", 0) > 0 else "missing",
                "counts": counts,
            }
        )

    def run_downloads(self, env: dict[str, str]) -> None:
        download_dir = Path(self.args.download_dir).resolve()
        for fmt in ("pdf", "caj"):
            if fmt == "caj":
                time.sleep(1.0)
            payload = self.run_cli(
                f"download_entry_{fmt}",
                [
                    "download",
                    "--workspace",
                    self.workspace_id,
                    "--run",
                    self.run_id,
                    "--top",
                    "1",
                    "--format",
                    fmt,
                    "--dir",
                    str(download_dir),
                    "--return-fields",
                    "download_full",
                ],
                env_overrides=env,
            )
            self.verify_download_payload(payload, fmt)
            if self.should_stop():
                return

    def verify_download_payload(self, payload: dict[str, Any], fmt: str) -> None:
        for row in payload.get("rows", []) or []:
            status = row.get("download_status")
            saved_to = row.get("saved_to", "")
            check = {"format": fmt, "status": status, "saved": bool(saved_to), "header_ok": False}
            if status == "downloaded" and saved_to:
                path = Path(saved_to)
                check["exists"] = path.exists()
                if path.exists():
                    prefix = path.read_bytes()[:8].lower()
                    if fmt == "pdf":
                        check["header_ok"] = prefix.startswith(b"%pdf")
                    else:
                        check["header_ok"] = prefix.startswith((b"caj", b"kdh", b"pk\x03\x04"))
                    if not self.args.keep_downloads:
                        path.unlink(missing_ok=True)
                if not check["header_ok"]:
                    self.failures.append({"label": f"download_entry_{fmt}", "error": "download_header_invalid"})
            elif saved_to:
                self.failures.append({"label": f"download_entry_{fmt}", "error": "guarded_download_saved_file"})
            self.file_checks.append(check)

    def run_pressure(self, env: dict[str, str]) -> None:
        phases = []
        for token in str(self.args.pressure_profile or "").split(","):
            if not token.strip():
                continue
            rate_text, _, seconds_text = token.partition(":")
            rate = float(rate_text)
            seconds = float(seconds_text or "0")
            if rate > min(MAX_RATE, self.args.max_rate):
                continue
            phases.append((rate, seconds))
        for rate, seconds in phases:
            limiter = RateLimiter(rate)
            started = time.monotonic()
            phase_latencies = []
            total = 0
            guarded = 0
            successes = 0
            while time.monotonic() - started < seconds:
                limiter.wait()
                result = self.runner.run(
                    f"pressure_search_{rate:g}",
                    ["search", self.args.query, "--page", "1", "--return-fields", "search_basic"],
                    env_overrides=env,
                )
                total += 1
                phase_latencies.append(result.elapsed_ms)
                found = find_guarded_state(result.payload)
                if found:
                    guarded += 1
                    self.blocked_state = self.blocked_state or found
                    break
                if result.returncode == 0:
                    successes += 1
            self.steps.append(
                {
                    "label": f"pressure_search_{rate:g}",
                    "status": "completed",
                    "rate": rate,
                    "duration_s": seconds,
                    "total_requests": total,
                    "success_count": successes,
                    "guarded_error_count": guarded,
                    **latency_stats(phase_latencies),
                }
            )
            if self.should_stop():
                break
        if self.should_stop() or not self.workspace_id or not self.run_id or self.args.endpoint_pressure_seconds <= 0:
            return
        endpoint_seconds = float(self.args.endpoint_pressure_seconds)
        endpoint_phases = [
            ("pressure_detail", min(20.0, self.args.max_rate), ["fetch_details", "--workspace", self.workspace_id, "--run", self.run_id, "--top", "1", "--refresh-existing"]),
            ("pressure_facet", min(10.0, self.args.max_rate), ["discover_facets", "--workspace", self.workspace_id, "--run", self.run_id, "--group", "subdiscipline"]),
            ("pressure_export", min(10.0, self.args.max_rate), ["export", "--workspace", self.workspace_id, "--run", self.run_id, "--top", "1", "--mode", "GBTREFER", "MLA", "APA"]),
        ]
        for label, rate, cli_args in endpoint_phases:
            self.run_endpoint_pressure(label, rate, endpoint_seconds, cli_args, env)
            if self.should_stop():
                break

    def run_endpoint_pressure(self, label: str, rate: float, seconds: float, cli_args: list[str], env: dict[str, str]) -> None:
        limiter = RateLimiter(rate)
        started = time.monotonic()
        phase_latencies = []
        total = 0
        guarded = 0
        successes = 0
        while time.monotonic() - started < seconds:
            limiter.wait()
            result = self.runner.run(label, cli_args, env_overrides=env)
            total += 1
            phase_latencies.append(result.elapsed_ms)
            found = find_guarded_state(result.payload)
            if found:
                guarded += 1
                self.blocked_state = self.blocked_state or found
                break
            if result.returncode == 0:
                successes += 1
        self.steps.append(
            {
                "label": label,
                "status": "completed",
                "rate": rate,
                "duration_s": seconds,
                "total_requests": total,
                "success_count": successes,
                "guarded_error_count": guarded,
                "saved_file_count": len([check for check in self.file_checks if check.get("saved")]),
                "no_error_page_save": not any(failure.get("error") == "guarded_download_saved_file" for failure in self.failures),
                **latency_stats(phase_latencies),
            }
        )

    def summary(self) -> dict[str, Any]:
        status = "ok"
        if self.failures:
            status = "failed"
        elif self.blocked_state:
            status = "blocked_by_cnki_guard"
        return sanitize_payload(
            {
                "status": status,
                "workspace_id": self.workspace_id,
                "run_id": self.run_id,
                "blocked_state": self.blocked_state,
                "step_count": len(self.steps),
                "steps": self.steps,
                "latency": latency_stats(self.latencies),
                "store_checks": self.store_checks,
                "file_checks": self.file_checks,
                "failures": self.failures,
                "include_downloads": bool(self.args.include_downloads),
                "include_pressure": bool(self.args.include_pressure),
            },
            debug=self.args.debug,
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    errors = validate_args(args, os.environ, require_live=not args.dry_run)
    if errors:
        print(json.dumps({"status": "error", "error": "invalid_live_prerequisites", "details": errors}, ensure_ascii=False, indent=2))
        return 2
    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "action": "dry_run",
                    "max_rate": args.max_rate,
                    "max_rows": args.max_rows,
                    "include_downloads": bool(args.include_downloads),
                    "include_pressure": bool(args.include_pressure),
                    "required_env": ["CNKI_LIVE_TEST=1", "optional CNKI_COOKIE/CNKI_COOKIE_FILE fallback", "CNKI_DOWNLOAD_DIR or --download-dir"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    child_env = dict(os.environ)
    child_env["CNKI_DOWNLOAD_DIR"] = str(Path(args.download_dir).resolve())
    runner = CliRunner(env=child_env, max_rate=args.max_rate, debug=args.debug)
    summary = LiveHarness(args, runner).run()
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.summary_out:
        summary_path = Path(args.summary_out)
        if not summary_path.is_absolute():
            summary_path = REPO_ROOT / summary_path
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if summary["status"] == "ok":
        return 0
    return 2 if summary["status"] == "blocked_by_cnki_guard" else 1


if __name__ == "__main__":
    raise SystemExit(main())
