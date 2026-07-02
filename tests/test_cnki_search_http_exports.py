import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / ".claude" / "skills" / "cnki-search"
sys.path.insert(0, str(SKILL_DIR / "src"))
sys.path.insert(0, str(SKILL_DIR.parent))

from adapters import exports as http_exports
from actions import _workflow_impl as workflow_core
from core.state_store import WorkspaceStore
from cnki_search_test_helpers import create_workspace_run


class HttpExportTests(unittest.TestCase):
    def test_mode_normalization_accepts_aliases_and_dedupes(self):
        self.assertEqual(
            http_exports.normalize_modes(["gbt", "APA", "notefirst", "NodeFirst", "bibtex"]),
            ["GBTREFER", "APA", "NodeFirst", "BibTex"],
        )

    def test_quick_citation_uses_three_mode_bundle_and_filters_requested_mode(self):
        calls = []

        def fake_request(method, url, cookie, form, timeout, referer=http_exports.EXPORT_REFERER):
            calls.append({"method": method, "url": url, "form": dict(form), "referer": referer})
            body = json.dumps(
                {
                    "code": 1,
                    "msg": "返回成功",
                    "data": [
                        {"mode": "GBTREFER", "value": ["<b>gbt</b><br>"]},
                        {"mode": "MLA", "value": ["mla<br>"]},
                        {"mode": "APA", "value": ["apa<br>"]},
                    ],
                },
                ensure_ascii=False,
            )
            return http_exports.HttpResponse(url=url, status=200, headers={}, body=body, elapsed_ms=1)

        with mock.patch.object(http_exports, "_request", side_effect=fake_request):
            row = http_exports.export_row({"row_id": "row-1", "global_rank": 1, "title": "T", "export_id": "EXP"}, modes=["MLA"], cookie="COOKIE", timeout=1)

        self.assertEqual(row["export_status"], "ok")
        self.assertEqual(row["exports"], {"MLA": "mla"})
        self.assertEqual(calls[0]["url"], http_exports.GET_EXPORT_URL)
        self.assertEqual(calls[0]["form"]["displaymode"], "GBTREFER,MLA,APA")

    def test_file_export_uses_endnote_endpoint_and_text_body(self):
        calls = []

        def fake_request(method, url, cookie, form, timeout, referer=http_exports.EXPORT_REFERER):
            calls.append({"method": method, "url": url, "form": dict(form), "referer": referer})
            return http_exports.HttpResponse(url=url, status=200, headers={}, body="%0 Journal Article\n%A 张三", elapsed_ms=1)

        with mock.patch.object(http_exports, "_request", side_effect=fake_request):
            row = http_exports.export_row({"row_id": "row-1", "global_rank": 1, "title": "T", "export_id": "EXP"}, modes=["EndNote"], cookie="COOKIE", timeout=1)

        self.assertEqual(row["export_status"], "ok")
        self.assertIn("%0 Journal Article", row["exports"]["EndNote"])
        self.assertEqual(calls[0]["url"], http_exports.ENDNOTE_FILE_TO_TEXT_URL)
        self.assertEqual(calls[0]["form"]["DisplayMode"], "EndNote")
        self.assertEqual(calls[0]["form"]["Type"], "txt")

    def test_missing_export_id_is_structured_row_error(self):
        row = http_exports.export_row({"row_id": "row-1", "global_rank": 1, "title": "T"}, modes=["GBTREFER"], cookie="COOKIE", timeout=1)

        self.assertEqual(row["export_status"], "error")
        self.assertEqual(row["export_error"], "missing_export_id")

    def test_export_rows_uses_bounded_concurrency_and_preserves_order(self):
        rows = [
            {"row_id": "row-1", "global_rank": 1, "title": "A", "export_id": "EXP1"},
            {"row_id": "row-2", "global_rank": 2, "title": "B", "export_id": "EXP2"},
        ]

        def fake_export_row(row, modes=None, file_type="txt", cookie=None, timeout=None):
            return {"row_id": row["row_id"], "global_rank": row["global_rank"], "export_status": "ok"}

        with mock.patch.dict(os.environ, {"CNKI_EXPORT_MAX_CONCURRENCY": "2"}):
            with mock.patch.object(http_exports, "_load_http_cookie", return_value=("COOKIE", "test")):
                with mock.patch.object(http_exports, "export_row", side_effect=fake_export_row) as export_row:
                    output = http_exports.export_rows(rows, modes=["GBTREFER"])

        self.assertEqual([row["row_id"] for row in output], ["row-1", "row-2"])
        self.assertEqual(export_row.call_count, 2)

    def test_export_rows_batches_quick_citations_and_maps_by_title(self):
        rows = [
            {"row_id": "row-1", "global_rank": 1, "title": "关于统计 学习 理论与支持向量机", "export_id": "EXP1"},
            {"row_id": "row-2", "global_rank": 2, "title": "支持向量机理论与算法研究综述", "export_id": "EXP2"},
        ]

        def fake_request(method, url, cookie, form, timeout, referer=http_exports.EXPORT_REFERER):
            self.assertEqual(form["filename"], "EXP1,EXP2")
            body = json.dumps(
                {
                    "code": 1,
                    "data": [
                        {
                            "mode": "GBTREFER",
                            "value": [
                                "[1]丁世飞.支持向量机理论与算法研究综述[J].期刊.<br>",
                                "[2]张学工.关于统计学习理论与支持向量机[J].期刊.<br>",
                            ],
                        },
                        {
                            "mode": "MLA",
                            "value": [
                                "[1]丁世飞.\"支持向量机理论与算法研究综述.\"期刊.<br>",
                                "[2]张学工.\"关于统计学习理论与支持向量机.\"期刊.<br>",
                            ],
                        },
                    ],
                },
                ensure_ascii=False,
            )
            return http_exports.HttpResponse(url=url, status=200, headers={}, body=body, elapsed_ms=1)

        with mock.patch.object(http_exports, "_load_http_cookie", return_value=("COOKIE", "test")):
            with mock.patch.object(http_exports, "_request", side_effect=fake_request) as request:
                output = http_exports.export_rows(rows, modes=["GBTREFER", "MLA"])

        self.assertEqual([row["row_id"] for row in output], ["row-1", "row-2"])
        self.assertIn("关于统计学习理论", output[0]["exports"]["GBTREFER"])
        self.assertIn("支持向量机理论", output[1]["exports"]["GBTREFER"])
        self.assertTrue(output[0]["export_batch"])
        self.assertEqual(request.call_count, 1)


class WorkflowExportActionTests(unittest.TestCase):
    def test_export_action_uses_http_adapter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkspaceStore(tmpdir)
            results = [
                {
                    "row_id": "row-0001",
                    "global_rank": 1,
                    "page_no": 1,
                    "page_row_no": 1,
                    "title": "标题",
                    "authors": "张三",
                    "date": "2024",
                    "journal": "期刊",
                    "citations": "1",
                    "export_id": "EXP1",
                }
            ]
            create_workspace_run(store, results=results)

            fake_rows = [
                {
                    "row_id": "row-0001",
                    "global_rank": 1,
                    "title": "标题",
                    "export_status": "ok",
                    "export_modes": ["BibTex"],
                    "exports": {"BibTex": "@article{x}"},
                    "export_error": "",
                    "mode_errors": {},
                    "export_transport": "http",
                }
            ]
            with mock.patch.object(workflow_core, "export_rows", return_value=fake_rows) as export_rows:
                payload = workflow_core.export_action(workspace_id="cws-test", run_id="run-test", rows=[1], modes=["BibTex"], return_fields=["export_full"], store=store)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["action"], "export")
        self.assertEqual(payload["workspace_id"], "cws-test")
        self.assertEqual(payload["run_id"], "run-test")
        self.assertEqual(payload["rows"][0]["exports"]["BibTex"], "@article{x}")
        self.assertEqual(payload["rows"][0]["export_transport"], "http")
        export_rows.assert_called_once()

    def test_export_action_reuses_workspace_artifact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkspaceStore(tmpdir)
            results = [{"row_id": "row-0001", "global_rank": 1, "page_no": 1, "page_row_no": 1, "title": "标题", "export_id": "EXP1"}]
            create_workspace_run(store, results=results)
            fake_rows = [{"row_id": "row-0001", "global_rank": 1, "title": "标题", "export_status": "ok", "export_modes": ["GBTREFER"], "exports": {"GBTREFER": "ref"}, "export_error": "", "mode_errors": {}}]
            with mock.patch.object(workflow_core, "export_rows", return_value=fake_rows) as export_rows:
                first = workflow_core.export_action(workspace_id="cws-test", run_id="run-test", rows=[1], modes=["GBTREFER"], store=store)
                second = workflow_core.export_action(workspace_id="cws-test", run_id="run-test", rows=[1], modes=["GBTREFER"], store=store)

        self.assertEqual(first["rows"][0]["exports"]["GBTREFER"], "ref")
        self.assertEqual(second["rows"][0]["exports"]["GBTREFER"], "ref")
        export_rows.assert_called_once()


if __name__ == "__main__":
    unittest.main()
