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
sys.path.insert(0, str(REPO_ROOT / "tests"))

from adapters import details as http_details
from adapters import downloads as http_downloads
from adapters import facets as http_facets
from actions import _workflow_impl as workflow_core
from core.state_store import WorkspaceStore
from cnki_search_test_helpers import create_workspace_run


class HttpDownloadAdapterTests(unittest.TestCase):
    def test_http_direct_download_saves_verified_file_response(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            row = {"row_id": "row-0001", "global_rank": 1, "title": "标题", "caj_url": "https://bar.cnki.net/bar/download/order?id=caj-token"}
            response = http_downloads.HttpDownloadResponse(
                status=200,
                url="https://download.cnki.net/file",
                headers={"Content-Disposition": "attachment; filename=paper.caj"},
                body=b"CAJ\x00binary",
            )
            with mock.patch.object(http_downloads, "_load_cookie_seed", return_value=("COOKIE=1", "test")):
                with mock.patch.object(http_downloads, "_request_bytes", return_value=response) as request:
                    item = http_downloads.download_direct_row(row, fmt="caj", download_dir=tmpdir)
            saved_bytes = Path(item["saved_to"]).read_bytes()

        self.assertEqual(item["status"], "downloaded")
        self.assertEqual(item["download_transport"], "http_direct")
        self.assertEqual(saved_bytes, b"CAJ\x00binary")
        self.assertEqual(request.call_args.args[0], row["caj_url"])

    def test_http_direct_download_classifies_login_html_without_saving(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            row = {"row_id": "row-0001", "global_rank": 1, "title": "T", "pdf_url": "https://bar.cnki.net/order"}
            response = http_downloads.HttpDownloadResponse(
                status=200,
                url="https://login.cnki.net/",
                headers={"Content-Type": "text/html"},
                body="请登录后继续访问".encode("utf-8"),
            )
            with mock.patch.object(http_downloads, "_load_cookie_seed", return_value=("COOKIE=1", "test")):
                with mock.patch.object(http_downloads, "_request_bytes", return_value=response):
                    item = http_downloads.download_direct_row(row, fmt="pdf", download_dir=tmpdir)
            saved_files = list(Path(tmpdir).iterdir())

        self.assertEqual(item["status"], "error")
        self.assertEqual(item["error"], "login_required")
        self.assertEqual(saved_files, [])

    def test_http_direct_rows_use_configured_concurrency_and_preserve_order(self):
        rows = [
            {"row_id": "row-0001", "global_rank": 1, "pdf_url": "https://bar.cnki.net/order/1"},
            {"row_id": "row-0002", "global_rank": 2, "pdf_url": "https://bar.cnki.net/order/2"},
        ]
        created_workers = []

        class FakeFuture:
            def __init__(self, value):
                self.value = value

            def result(self):
                return self.value

        class FakeExecutor:
            def __init__(self, max_workers):
                created_workers.append(max_workers)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def submit(self, fn, row, **kwargs):
                return FakeFuture({"row_id": row["row_id"], "global_rank": row["global_rank"], "status": "downloaded", "format": "PDF"})

        with mock.patch.dict(os.environ, {"CNKI_DOWNLOAD_MAX_CONCURRENCY": "2"}):
            with mock.patch.object(http_downloads, "_load_cookie_seed", return_value=("COOKIE=1", "test")):
                with mock.patch.object(http_downloads, "ThreadPoolExecutor", FakeExecutor):
                    with mock.patch.object(http_downloads, "as_completed", side_effect=lambda futures: list(futures)):
                        output = http_downloads.download_direct_rows(rows, fmt="pdf", download_dir="D:/downloads")

        self.assertEqual(created_workers, [2])
        self.assertEqual([item["global_rank"] for item in output], [1, 2])


class HttpDetailAdapterTests(unittest.TestCase):
    def test_detail_classifier_allows_detail_page_with_login_prompts(self):
        html = """
        <html><head><title>论文标题 - 中国知网</title></head>
        <body><div class="wx-tit"><h1>论文标题</h1></div><input id="abstract_text" value="摘要内容">请登录后观看</body></html>
        """
        response = http_details.HttpResponse("https://kns.cnki.net/kcms2/article/abstract?v=x", 200, {}, html, 10)

        self.assertEqual(http_details.classify_detail_response(response), ("ok", ""))

    def test_detail_parser_extracts_core_fields_and_download_links(self):
        html = """
        <div class="wx-tit"><h1>标题 A 网络首发</h1></div>
        <h3 class="author"><a>张三1</a><a>李四2</a></h3>
        <h3 class="author"><a>机构一</a><a>机构二</a></h3>
        <input id="abstract_text" value="摘要内容">
        <p class="keywords"><a>机器学习;</a><a>深度学习</a></p>
        <p class="fund">基金项目</p>
        <div class="clc-code">TP391</div>
        <div class="doc-top"><a>期刊 A</a></div>
        <span class="head-time">2024-01-01</span>
        <a id="pdfDown" href="/bar/download/order?id=pdf-token">PDF下载</a>
        <a id="cajDown" href="/bar/download/order?id=caj-token">CAJ下载</a>
        """

        parsed = http_details.parse_detail_html(html, "https://kns.cnki.net/kcms2/article/abstract?v=x")

        self.assertEqual(parsed["title"], "标题 A")
        self.assertEqual(parsed["abstract"], "摘要内容")
        self.assertEqual(parsed["keywords"], ["机器学习", "深度学习"])
        self.assertEqual(parsed["authors_structured"][0]["name"], "张三")
        self.assertEqual(parsed["affiliations"], ["机构一", "机构二"])
        self.assertEqual(parsed["pdf_url"], "https://kns.cnki.net/bar/download/order?id=pdf-token")
        self.assertEqual(parsed["caj_url"], "https://kns.cnki.net/bar/download/order?id=caj-token")


class WorkspaceDownloadActionTests(unittest.TestCase):
    def test_download_action_uses_http_direct_and_updates_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkspaceStore(tmpdir)
            results = [{
                "row_id": "row-0001",
                "global_rank": 1,
                "page_no": 1,
                "page_row_no": 1,
                "title": "标题",
                "detail_url": "https://kns.cnki.net/detail/1",
                "caj_url": "https://bar.cnki.net/bar/download/order?id=caj-token",
                "download_status": "pending",
            }]
            create_workspace_run(store, results=results)
            direct_item = {"row_id": "row-0001", "global_rank": 1, "status": "downloaded", "format": "CAJ", "saved_to": str(Path(tmpdir) / "paper.caj"), "filename": "paper.caj", "download_transport": "http_direct"}
            with mock.patch.object(workflow_core, "download_direct_rows", return_value=[direct_item]) as direct_download:
                payload = workflow_core.download_action(workspace_id="cws-test", run_id="run-test", rows=[1], fmt="caj", download_dir=tmpdir, return_fields=["download_full"], store=store)
            saved_results = store.load_results("cws-test", "run-test")
            artifacts = store.load_artifacts("cws-test", "run-test")

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["rows"][0]["download_status"], "downloaded")
        self.assertEqual(saved_results[0]["download_path"], str(Path(tmpdir) / "paper.caj"))
        self.assertIn("1", artifacts["download"])
        direct_download.assert_called_once()

    def test_pdf_download_fetches_missing_pdf_link_before_direct_download(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkspaceStore(tmpdir)
            results = [{"row_id": "row-0001", "global_rank": 1, "page_no": 1, "page_row_no": 1, "title": "标题", "detail_url": "https://kns.cnki.net/detail/1", "download_status": "pending"}]
            create_workspace_run(store, results=results)
            detail_item = {"row_id": "row-0001", "global_rank": 1, "status": "ok", "pdf_url": "https://bar.cnki.net/bar/download/order?id=pdf-token"}
            direct_item = {"row_id": "row-0001", "global_rank": 1, "status": "downloaded", "format": "PDF", "saved_to": str(Path(tmpdir) / "paper.pdf"), "filename": "paper.pdf", "download_transport": "http_direct"}
            with mock.patch.object(workflow_core, "fetch_detail_rows", return_value=[detail_item]) as fetch_details:
                with mock.patch.object(workflow_core, "download_direct_rows", return_value=[direct_item]) as direct_download:
                    payload = workflow_core.download_action(workspace_id="cws-test", run_id="run-test", rows=[1], fmt="pdf", download_dir=tmpdir, store=store)
            saved_results = store.load_results("cws-test", "run-test")

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(saved_results[0]["pdf_url"], "https://bar.cnki.net/bar/download/order?id=pdf-token")
        fetch_details.assert_called_once()
        direct_download.assert_called_once()

    def test_downloaded_row_with_missing_file_is_not_reused_as_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkspaceStore(tmpdir)
            missing_path = str(Path(tmpdir) / "missing.pdf")
            results = [{
                "row_id": "row-0001",
                "global_rank": 1,
                "page_no": 1,
                "page_row_no": 1,
                "title": "标题",
                "pdf_url": "https://bar.cnki.net/bar/download/order?id=pdf-token",
                "download_status": "downloaded",
                "download_path": missing_path,
            }]
            create_workspace_run(store, results=results)
            direct_item = {"row_id": "row-0001", "global_rank": 1, "status": "error", "error": "format_mismatch", "format": "PDF"}
            with mock.patch.object(workflow_core, "download_direct_rows", return_value=[direct_item]) as direct_download:
                payload = workflow_core.download_action(workspace_id="cws-test", run_id="run-test", rows=[1], fmt="pdf", download_dir=tmpdir, store=store)

        self.assertEqual(payload["rows"][0]["download_status"], "error")
        self.assertEqual(payload["rows"][0]["download_error"], "format_mismatch")
        direct_download.assert_called_once()


class WorkspaceDetailAndFacetTests(unittest.TestCase):
    def test_fetch_details_uses_http_and_hides_urls_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkspaceStore(tmpdir)
            results = [{"row_id": "row-0001", "global_rank": 1, "page_no": 1, "page_row_no": 1, "title": "标题", "journal": "期刊 A", "detail_url": "https://kns.cnki.net/kcms2/article/abstract?v=x", "detail_status": "pending"}]
            create_workspace_run(store, results=results)
            fake_rows = [{"row_id": "row-0001", "global_rank": 1, "status": "ok", "title": "标题", "abstract": "摘要", "keywords": ["机器学习"], "fund": "", "journal": "", "pdf_url": "https://bar.cnki.net/bar/download/order?id=pdf"}]
            with mock.patch.object(workflow_core, "fetch_detail_rows", return_value=fake_rows) as fetch_rows:
                payload = workflow_core.fetch_details_action(workspace_id="cws-test", run_id="run-test", rows=[1], return_fields=["detail_full", "pdf_url"], store=store)
            saved_results = store.load_results("cws-test", "run-test")
            saved_details = store.load_details("cws-test", "run-test")

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["rows"][0]["abstract"], "摘要")
        self.assertEqual(payload["rows"][0]["journal"], "期刊 A")
        self.assertNotIn("pdf_url", payload["rows"][0])
        self.assertEqual(saved_results[0]["pdf_url"], "https://bar.cnki.net/bar/download/order?id=pdf")
        self.assertEqual(saved_details["detail-row-0001"]["journal"], "期刊 A")
        fetch_rows.assert_called_once()

    def test_fetch_details_reuses_existing_detail_without_http(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkspaceStore(tmpdir)
            results = [{"row_id": "row-0001", "global_rank": 1, "page_no": 1, "page_row_no": 1, "title": "标题", "detail_status": "ok", "detail_ref": "detail-row-0001"}]
            create_workspace_run(store, results=results)
            store.save_details("cws-test", "run-test", {"detail-row-0001": {"row_id": "row-0001", "global_rank": 1, "abstract": "已有摘要"}})
            with mock.patch.object(workflow_core, "fetch_detail_rows") as fetch_rows:
                payload = workflow_core.fetch_details_action(workspace_id="cws-test", run_id="run-test", rows=[1], store=store)

        self.assertEqual(payload["rows"][0]["abstract"], "已有摘要")
        fetch_rows.assert_not_called()

    def test_http_facet_parser_reads_checkbox_items(self):
        html = """
        <dl><dt><b>学科</b></dt><dd field="CCL" tit="学科">
          <li><input type="checkbox" value="I138" title="自动化技术"><span>(12)</span></li>
          <li style="display:none"><input type="checkbox" value="I139" title="计算机"><span>(3)</span></li>
        </dd></dl>
        """

        group_label, items = http_facets.parse_facet_html(html, "CCL")

        self.assertEqual(group_label, "学科")
        self.assertEqual(items[0]["code"], "I138")
        self.assertEqual(items[0]["count"], 12)
        self.assertFalse(items[1]["visible"])

    def test_discover_facets_uses_workspace_run_signature(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkspaceStore(tmpdir)
            create_workspace_run(store, results=[{"row_id": "row-0001", "global_rank": 1, "page_no": 1, "page_row_no": 1, "title": "标题"}])
            fake = {"facet_group": "subdiscipline", "group_label": "学科", "items": [{"index": 0, "code": "I138", "label": "自动化", "count": 1, "checked": False, "visible": True}], "warnings": []}
            with mock.patch.object(workflow_core, "run_http_facets", return_value=fake) as http_facet:
                payload = workflow_core.discover_facets_action(workspace_id="cws-test", run_id="run-test", store=store)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["summary"]["facet_group"], "subdiscipline")
        self.assertEqual(payload["rows"][0]["code"], "I138")
        http_facet.assert_called_once()


if __name__ == "__main__":
    unittest.main()
