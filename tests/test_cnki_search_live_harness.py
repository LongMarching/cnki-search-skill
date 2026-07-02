import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = REPO_ROOT / "tests" / "live" / "cnki_search_live.py"
spec = importlib.util.spec_from_file_location("cnki_search_live", HARNESS_PATH)
cnki_search_live = importlib.util.module_from_spec(spec)
sys.modules["cnki_search_live"] = cnki_search_live
spec.loader.exec_module(cnki_search_live)


class FakeRunner:
    def __init__(self, guarded_first=False, guarded_pdf_download=False):
        self.calls = []
        self.guarded_first = guarded_first
        self.guarded_pdf_download = guarded_pdf_download

    def run(self, label, cli_args, env_overrides=None):
        self.calls.append((label, list(cli_args), dict(env_overrides or {})))
        if self.guarded_first and label == "search_basic":
            payload = {
                "status": "error",
                "workspace_id": "cws-guarded",
                "run_id": "run-guarded",
                "action": "search",
                "error": "http_captcha",
                "detail": "captcha",
            }
            return cnki_search_live.CommandResult(label, payload, 1, 10)
        if self.guarded_pdf_download and label == "download_entry_pdf":
            payload = {
                "status": "ok",
                "workspace_id": "cws-test",
                "run_id": "run-test",
                "action": "download",
                "count": 1,
                "summary": {},
                "rows": [{"global_rank": 1, "title": "T", "download_status": "login_required", "saved_to": ""}],
                "warnings": [{"row": 1, "error": "login_required"}],
            }
            return cnki_search_live.CommandResult(label, payload, 0, 5)
        payload = {
            "status": "ok",
            "workspace_id": "cws-test",
            "run_id": "run-test",
            "action": label,
            "count": 1,
            "summary": {"search_transport": "http"},
            "rows": [{"global_rank": 1, "title": "T"}],
            "warnings": [],
        }
        return cnki_search_live.CommandResult(label, payload, 0, 5)


class CnkiSearchLiveHarnessTests(unittest.TestCase):
    def test_max_rate_above_50_is_rejected(self):
        args = cnki_search_live.parse_args(["--max-rate", "51"])
        env = {"CNKI_LIVE_TEST": "1", "CNKI_COOKIE": "x"}

        errors = cnki_search_live.validate_args(args, env)

        self.assertTrue(any("50" in error for error in errors))

    def test_rate_limiter_sleeps_to_enforce_request_interval(self):
        limiter = cnki_search_live.RateLimiter(2.0)

        with mock.patch.object(cnki_search_live.time, "monotonic", side_effect=[0.0, 0.0, 0.1, 0.5]):
            with mock.patch.object(cnki_search_live.time, "sleep") as sleep:
                limiter.wait()
                limiter.wait()

        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.4, places=3)

    def test_live_prerequisites_require_live_flag_only_when_ip_login_enabled(self):
        args = cnki_search_live.parse_args([])

        errors = cnki_search_live.validate_args(args, {})

        self.assertTrue(any("CNKI_LIVE_TEST" in error for error in errors))
        self.assertFalse(any("CNKI_COOKIE" in error for error in errors))

    def test_live_prerequisites_require_cookie_when_ip_login_disabled(self):
        args = cnki_search_live.parse_args([])

        errors = cnki_search_live.validate_args(args, {"CNKI_LIVE_TEST": "1", "CNKI_AUTO_IP_LOGIN": "0"})

        self.assertTrue(any("CNKI_COOKIE" in error for error in errors))

    def test_dry_run_validation_does_not_require_live_cookie(self):
        args = cnki_search_live.parse_args(["--dry-run", "--max-rate", "10"])

        errors = cnki_search_live.validate_args(args, {}, require_live=not args.dry_run)

        self.assertEqual(errors, [])

    def test_sensitive_fields_are_removed_from_summary(self):
        payload = {
            "workspace_id": "cws-test",
            "run_id": "run-test",
            "rows": [
                {
                    "title": "T",
                    "detail_url": "https://kns.cnki.net/detail",
                    "export_id": "secret-export",
                    "invoice": "secret-invoice",
                }
            ],
            "final_url": "https://bar.cnki.net/download",
        }

        clean = cnki_search_live.sanitize_payload(payload)
        serialized = str(clean)

        self.assertIn("title", clean["rows"][0])
        self.assertNotIn("detail_url", clean["rows"][0])
        self.assertNotIn("export_id", clean["rows"][0])
        self.assertNotIn("invoice", clean["rows"][0])
        self.assertNotIn("cnki.net", serialized)

    def test_guarded_error_stops_mandatory_flow(self):
        args = cnki_search_live.parse_args([])
        harness = cnki_search_live.LiveHarness(args, FakeRunner(guarded_first=True))

        summary = harness.run()

        self.assertEqual(summary["status"], "blocked_by_cnki_guard")
        self.assertEqual(summary["blocked_state"], "http_captcha")
        self.assertEqual([call[0] for call in harness.runner.calls], ["search_basic"])

    def test_include_downloads_not_set_skips_actual_saves(self):
        args = cnki_search_live.parse_args(["--max-rows", "3"])
        harness = cnki_search_live.LiveHarness(args, FakeRunner())

        summary = harness.run()

        labels = [call[0] for call in harness.runner.calls]
        self.assertEqual(summary["status"], "ok")
        self.assertNotIn("download_entry_pdf", labels)
        self.assertNotIn("download_entry_caj", labels)
        self.assertTrue(any(step.get("label") == "actual_downloads" and step.get("status") == "skipped" for step in summary["steps"]))

    def test_include_downloads_exercises_download_entries(self):
        args = cnki_search_live.parse_args(["--max-rows", "1", "--include-downloads"])
        runner = FakeRunner()
        harness = cnki_search_live.LiveHarness(args, runner)

        summary = harness.run()

        labels = [call[0] for call in runner.calls]
        self.assertEqual(summary["status"], "ok")
        self.assertIn("download_entry_pdf", labels)
        self.assertIn("download_entry_caj", labels)
        pdf_call = next(call for call in runner.calls if call[0] == "download_entry_pdf")
        self.assertIn("--format", pdf_call[1])
        self.assertEqual(pdf_call[1][pdf_call[1].index("--format") + 1], "pdf")

    def test_include_downloads_stops_after_guarded_pdf_download(self):
        args = cnki_search_live.parse_args(["--max-rows", "1", "--include-downloads"])
        runner = FakeRunner(guarded_pdf_download=True)
        harness = cnki_search_live.LiveHarness(args, runner)

        summary = harness.run()

        labels = [call[0] for call in runner.calls]
        self.assertEqual(summary["status"], "blocked_by_cnki_guard")
        self.assertIn("download_entry_pdf", labels)
        self.assertNotIn("download_entry_caj", labels)


if __name__ == "__main__":
    unittest.main()
