import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skill" / "cnki-search"
sys.path.insert(0, str(SKILL_DIR / "src"))
sys.path.insert(0, str(SKILL_DIR.parent))

from adapters import search as http_search
from actions import _workflow_impl as workflow_core
from core.state_store import WorkspaceStore


class StrictHttpOnlyTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "CNKI_SEARCH_TRANSPORT": "http",
                "CNKI_SEARCH_HTTP_STRICT": "1",
            },
            clear=False,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)


class HttpSearchBuilderTests(StrictHttpOnlyTestCase):
    def test_query_json_covers_multifield_filters_and_discipline(self):
        spec = http_search.SearchSpec(
            query="",
            fields=[
                {"field": "TI", "value": "机器学习", "precision": "exact"},
                {"field": "KY", "value": "深度学习", "precision": "fuzzy", "op": "OR"},
            ],
            doc_type="journal",
            discipline=["I138"],
            quality=["pku"],
            date_from="2020-01-01",
            date_to="2024-12-31",
            form_filters=["oa", "fund", "online_first"],
        )
        query_json = http_search.QueryJsonBuilder(spec).build()

        self.assertEqual(query_json["Resource"], "JOURNAL")
        self.assertEqual(query_json["Classid"], "YSTT4HG0")
        self.assertEqual(query_json["SearchType"], 1)
        qgroups = query_json["QNode"]["QGroup"]
        subject_children = qgroups[0]["ChildItems"]
        self.assertEqual(subject_children[0]["Items"][0]["Field"], "TI")
        self.assertEqual(subject_children[1]["Items"][0]["Operator"], "FUZZY")
        self.assertEqual(subject_children[1]["Items"][0]["Logic"], 1)
        self.assertTrue(any(item["Items"][0]["Field"] == "OA" for item in subject_children))
        self.assertTrue(any(group["Key"] == "NaviParam" and group["Items"][0]["Value"] == "I138?" for group in qgroups))
        self.assertEqual(qgroups[1]["ChildItems"][0]["Items"][0]["Field"], "HX")
        self.assertEqual(qgroups[1]["ChildItems"][1]["Items"][0]["Field"], "PT")

    def test_query_json_preserves_all_supported_field_codes(self):
        fields = [
            {"field": field, "value": f"value-{index}", "precision": "exact", "op": "AND"}
            for index, field in enumerate(http_search.FIELD_TITLES, start=1)
        ]
        spec = http_search.SearchSpec(query="", fields=fields)
        query_json = http_search.QueryJsonBuilder(spec).build()
        subject_children = query_json["QNode"]["QGroup"][0]["ChildItems"]

        self.assertEqual([child["Items"][0]["Field"] for child in subject_children], list(http_search.FIELD_TITLES))
        self.assertEqual([child["Items"][0]["Value"] for child in subject_children], [f"value-{index}" for index in range(1, len(fields) + 1)])

    def test_sentence_endpoint_and_form(self):
        spec = http_search.SearchSpec(
            query="机器学习",
            search_mode="sentence",
            extra={"word1": "机器学习", "word2": "深度学习", "proximity": "SEN"},
            page_limit=2,
        )
        query_json = http_search.QueryJsonBuilder(spec).build()
        form, warnings = http_search.SearchFormBuilder(spec, query_json).build(page=2)

        self.assertEqual(http_search.endpoint_for_search_mode("sentence"), http_search.BRIEF_SENQUERY_URL)
        self.assertEqual(query_json["SearchType"], 5)
        self.assertEqual(query_json["QNode"]["QGroup"][0]["Items"][0]["Operator"], "SEN")
        self.assertEqual(form["sentenceSearch"], "true")
        self.assertEqual(form["pageNum"], "2")
        self.assertNotIn("turnpage", form)
        self.assertTrue(any(item["code"] == "http_pagination_without_turnpage" for item in warnings))
        form_with_turnpage, warnings_with_turnpage = http_search.SearchFormBuilder(spec, query_json).build(page=2, turnpage="DYNAMIC")
        self.assertEqual(form_with_turnpage["turnpage"], "DYNAMIC")
        self.assertFalse(any(item["code"] == "http_pagination_without_turnpage" for item in warnings_with_turnpage))

    def test_sort_form_mapping(self):
        expected = {
            "relevance": "FFD",
            "citations": "CF",
            "downloads": "DFR",
            "comprehensive": "ZH",
        }
        for sort, sort_field in expected.items():
            with self.subTest(sort=sort):
                spec = http_search.SearchSpec(query="机器学习", doc_type="journal", sort=sort)
                query_json = http_search.QueryJsonBuilder(spec).build()
                form, warnings = http_search.SearchFormBuilder(spec, query_json).build(
                    page=1,
                    turnpage="DYNAMIC",
                    request_kind="sort",
                )
                form_query_json = json.loads(form["QueryJson"])

                self.assertEqual(query_json["SearchFrom"], 5)
                self.assertEqual(form_query_json["SearchFrom"], 5)
                self.assertEqual(form_query_json["Products"], "CJFQ,CAPJ,CJTL")
                self.assertEqual(form_query_json["View"], "changeDBOnlyFT")
                self.assertEqual(form["sortField"], sort_field)
                self.assertEqual(form["sortType"], "desc")
                self.assertEqual(form["boolSortSearch"], "true")
                self.assertEqual(form["turnpage"], "DYNAMIC")
                self.assertEqual(warnings, [])

    def test_page_form_uses_captured_turnpage_model(self):
        spec = http_search.SearchSpec(query="机器学习", doc_type="journal", page_limit=2)
        query_json = http_search.QueryJsonBuilder(spec).build()
        form, warnings = http_search.SearchFormBuilder(spec, query_json).build(
            page=2,
            turnpage="DYNAMIC",
            request_kind="page",
        )
        form_query_json = json.loads(form["QueryJson"])

        self.assertEqual(form["pageNum"], "2")
        self.assertNotIn("CurPage", form)
        self.assertEqual(form["turnpage"], "DYNAMIC")
        self.assertEqual(form["sortField"], "PT")
        self.assertEqual(form["sortType"], "desc")
        self.assertEqual(form["boolSearch"], "false")
        self.assertEqual(form["boolSortSearch"], "false")
        self.assertEqual(form_query_json["SearchFrom"], 4)
        self.assertEqual(form_query_json["Products"], "CJFQ,CAPJ,CJTL")
        self.assertEqual(form_query_json["View"], "changeDBOnlyFT")
        self.assertEqual(warnings, [])

    def test_crossdb_page_form_uses_captured_refresh_products(self):
        spec = http_search.SearchSpec(query="机器学习", doc_type="all", page_limit=2)
        query_json = http_search.QueryJsonBuilder(spec).build()
        form, warnings = http_search.SearchFormBuilder(spec, query_json).build(
            page=2,
            turnpage="DYNAMIC",
            request_kind="page",
        )
        form_query_json = json.loads(form["QueryJson"])

        self.assertEqual(form_query_json["Resource"], "CROSSDB")
        self.assertEqual(form_query_json["SearchFrom"], 4)
        self.assertEqual(
            form_query_json["Products"],
            "CJFQ,CAPJ,ZHYX,CJTL,CDFD,CMFD,CPFD,IPFD,CPVD,CCND,WBFD,SCSF,SCHF,SCSD,SNAD,CCJD,CJFN,CCVD",
        )
        self.assertEqual(form_query_json["View"], "changeDBCh")
        self.assertEqual(form["productStr"], http_search.PRODUCT_STR)
        self.assertEqual(form["sentenceSearch"], "false")
        self.assertEqual(form["sortField"], "PT")
        self.assertNotIn("CurPage", form)
        self.assertEqual(warnings, [])

    def test_sentence_page_form_uses_senquery_refresh_shape(self):
        spec = http_search.SearchSpec(
            query="机器学习",
            search_mode="sentence",
            extra={"word1": "机器学习", "word2": "深度学习", "proximity": "NEAR"},
            page_limit=2,
        )
        query_json = http_search.QueryJsonBuilder(spec).build()
        form, warnings = http_search.SearchFormBuilder(spec, query_json).build(
            page=2,
            turnpage="DYNAMIC",
            request_kind="page",
        )
        form_query_json = json.loads(form["QueryJson"])

        self.assertEqual(http_search.endpoint_for_search_mode("sentence"), http_search.BRIEF_SENQUERY_URL)
        self.assertEqual(form["sentenceSearch"], "true")
        self.assertEqual(form["productStr"], http_search.PRODUCT_STR)
        self.assertEqual(form_query_json["Resource"], "CROSSDB")
        self.assertEqual(form_query_json["Products"], "CJFQ,CAPJ,ZHYX,CJTL,CDFD,CMFD,CPFD,IPFD,CPVD,CCND,WBFD,SCSF,SCHF,SCSD,SNAD,CCJD,CJFN,CCVD")
        self.assertEqual(form_query_json["SearchFrom"], 4)
        self.assertEqual(form["sortField"], "PT")
        self.assertNotIn("CurPage", form)
        self.assertEqual(warnings, [])

    def test_thesis_page_form_omits_view_and_uses_dissertation_products(self):
        spec = http_search.SearchSpec(query="机器学习", doc_type="thesis", page_limit=2)
        query_json = http_search.QueryJsonBuilder(spec).build()
        self.assertNotIn("View", query_json)
        form, warnings = http_search.SearchFormBuilder(spec, query_json).build(
            page=2,
            turnpage="DYNAMIC",
            request_kind="page",
        )
        form_query_json = json.loads(form["QueryJson"])

        self.assertEqual(form_query_json["Resource"], "DISSERTATION")
        self.assertEqual(form_query_json["Products"], "CDFD,CMFD")
        self.assertNotIn("View", form_query_json)
        self.assertEqual(form_query_json["SearchFrom"], 4)
        self.assertEqual(form["productStr"], "")
        self.assertEqual(form["sortField"], "PT")
        self.assertNotIn("CurPage", form)
        self.assertEqual(warnings, [])

    def test_dissertation_subtype_page_products_are_classid_specific(self):
        expected = {
            "thesis": ("LSTPFY1C", "CDFD,CMFD"),
            "phd": ("RMJLXHZ3", "CDFD"),
            "masters": ("JQIRZIYA", "CMFD"),
        }
        for doc_type, (classid, products) in expected.items():
            with self.subTest(doc_type=doc_type):
                spec = http_search.SearchSpec(query="机器学习", doc_type=doc_type, page_limit=2)
                query_json = http_search.QueryJsonBuilder(spec).build()
                form, warnings = http_search.SearchFormBuilder(spec, query_json).build(
                    page=2,
                    turnpage="DYNAMIC",
                    request_kind="page",
                )
                form_query_json = json.loads(form["QueryJson"])

                self.assertEqual(form_query_json["Classid"], classid)
                self.assertEqual(form_query_json["Resource"], "DISSERTATION")
                self.assertEqual(form_query_json["Products"], products)
                self.assertNotIn("View", form_query_json)
                self.assertEqual(form_query_json["SearchFrom"], 4)
                self.assertEqual(form["sortField"], "PT")
                self.assertEqual(warnings, [])

    def test_conference_subtype_page_products_are_classid_specific(self):
        expected = {
            "conference": ("JUP3MUPD", "CPFD,IPFD,CPVD", ""),
            "domestic-conf": ("1UR4K4HZ", "CPFD", None),
            "intl-conf": ("BPBAFJ5S", "IPFD", None),
        }
        for doc_type, (classid, products, view) in expected.items():
            with self.subTest(doc_type=doc_type):
                spec = http_search.SearchSpec(query="机器学习", doc_type=doc_type, page_limit=2)
                query_json = http_search.QueryJsonBuilder(spec).build()
                self.assertNotIn("View", query_json)
                form, warnings = http_search.SearchFormBuilder(spec, query_json).build(
                    page=2,
                    turnpage="DYNAMIC",
                    request_kind="page",
                )
                form_query_json = json.loads(form["QueryJson"])

                self.assertEqual(form_query_json["Classid"], classid)
                self.assertEqual(form_query_json["Resource"], "CONFERENCE")
                self.assertEqual(form_query_json["Products"], products)
                if view is None:
                    self.assertNotIn("View", form_query_json)
                else:
                    self.assertEqual(form_query_json["View"], view)
                self.assertEqual(form_query_json["SearchFrom"], 4)
                self.assertEqual(form["sortField"], "PT")
                self.assertNotIn("CurPage", form)
                self.assertEqual(warnings, [])

    def test_sorted_page_form_combines_sort_field_with_resource_refresh_state(self):
        cases = [
            (
                http_search.SearchSpec(query="机器学习", doc_type="all", sort="downloads"),
                "DFR",
                "CROSSDB",
                "CJFQ,CAPJ,ZHYX,CJTL,CDFD,CMFD,CPFD,IPFD,CPVD,CCND,WBFD,SCSF,SCHF,SCSD,SNAD,CCJD,CJFN,CCVD",
                "changeDBCh",
                "false",
            ),
            (
                http_search.SearchSpec(query="机器学习", doc_type="thesis", sort="relevance"),
                "FFD",
                "DISSERTATION",
                "CDFD,CMFD",
                None,
                None,
            ),
            (
                http_search.SearchSpec(query="机器学习", doc_type="conference", sort="comprehensive"),
                "ZH",
                "CONFERENCE",
                "CPFD,IPFD,CPVD",
                "",
                None,
            ),
            (
                http_search.SearchSpec(
                    query="机器学习",
                    search_mode="sentence",
                    extra={"word1": "机器学习", "word2": "深度学习", "proximity": "NEAR"},
                    sort="citations",
                ),
                "CF",
                "CROSSDB",
                "CJFQ,CAPJ,ZHYX,CJTL,CDFD,CMFD,CPFD,IPFD,CPVD,CCND,WBFD,SCSF,SCHF,SCSD,SNAD,CCJD,CJFN,CCVD",
                "changeDBCh",
                "true",
            ),
        ]
        for spec, sort_field, resource, products, view, sentence_search in cases:
            with self.subTest(resource=resource, sort=spec.sort, mode=spec.search_mode):
                query_json = http_search.QueryJsonBuilder(spec).build()
                sort_form, sort_warnings = http_search.SearchFormBuilder(spec, query_json).build(
                    page=1,
                    turnpage="DYNAMIC",
                    request_kind="sort",
                )
                page_form, page_warnings = http_search.SearchFormBuilder(spec, query_json).build(
                    page=2,
                    turnpage="DYNAMIC",
                    request_kind="page",
                )
                sort_query_json = json.loads(sort_form["QueryJson"])
                page_query_json = json.loads(page_form["QueryJson"])

                self.assertEqual(sort_form["sortField"], sort_field)
                self.assertEqual(sort_form["boolSearch"], "false")
                self.assertEqual(sort_form["boolSortSearch"], "true")
                self.assertEqual(sort_query_json["SearchFrom"], 5)
                self.assertEqual(page_form["sortField"], sort_field)
                self.assertEqual(page_form["boolSearch"], "false")
                self.assertEqual(page_form["boolSortSearch"], "false")
                self.assertNotIn("CurPage", page_form)
                self.assertEqual(page_query_json["SearchFrom"], 4)
                self.assertEqual(page_query_json["Resource"], resource)
                self.assertEqual(page_query_json["Products"], products)
                if view is None:
                    self.assertNotIn("View", page_query_json)
                else:
                    self.assertEqual(page_query_json["View"], view)
                if sentence_search is None:
                    self.assertNotIn("sentenceSearch", page_form)
                else:
                    self.assertEqual(page_form["sentenceSearch"], sentence_search)
                self.assertEqual(sort_warnings, [])
                self.assertEqual(page_warnings, [])


class HttpSearchParserTests(StrictHttpOnlyTestCase):
    def test_grid_table_parser_profile(self):
        html = """
        <span class="pagerTitleCell">找到 1 条结果</span><span class="countPageMark">1/1</span>
        <input id="hidTurnPage" type="hidden" value="TURN123">
        <table class="result-table-list"><tbody>
          <tr>
            <td>1</td>
            <td class="name">
              <input class="cbItem" value="EXP1">
              <a class="fz14" href="/kcms2/article/abstract?v=abc">标题 A</a>
              <a id="pdfDown" href="/bar/download/order?id=pdf-token">PDF下载</a>
              <a id="cajDown" href="/bar/download/order?id=caj-token">CAJ下载</a>
              <a class="icon-html" href="https://x.cnki.net/read/readonline.ashx?filename=ABC&tablename=CJFDTOTAL&dbcode=CJFD">HTML阅读</a>
            </td>
            <td class="author">张三; 李四</td>
            <td class="source"><a>期刊 A</a></td>
            <td class="date">2024-01-02</td>
            <td class="data">CJFD</td>
            <td class="quote">10</td>
            <td class="download">20</td>
          </tr>
        </tbody></table>
        """
        parsed = http_search.parse_result_html(html, "https://kns.cnki.net/kns8s/brief/grid", http_search.SearchSpec(query="x"))

        self.assertEqual(parsed["total"], "1")
        self.assertEqual(parsed["turnpage"], "TURN123")
        self.assertEqual(parsed["result_format"], "grid_table")
        self.assertEqual(parsed["results"][0]["title"], "标题 A")
        self.assertEqual(parsed["results"][0]["authors"], "张三; 李四")
        self.assertEqual(parsed["results"][0]["citations"], "10")
        self.assertEqual(parsed["results"][0]["export_id"], "EXP1")
        self.assertEqual(parsed["results"][0]["pdf_url"], "https://kns.cnki.net/bar/download/order?id=pdf-token")
        self.assertEqual(parsed["results"][0]["caj_url"], "https://kns.cnki.net/bar/download/order?id=caj-token")

    def test_grid_table_parser_ignores_javascript_download_buttons(self):
        html = """
        <span class="pagerTitleCell">找到 1 条结果</span><span class="countPageMark">1/1</span>
        <table class="result-table-list"><tbody>
          <tr>
            <td>1</td>
            <td class="name">
              <input class="cbItem" value="EXP1">
              <a class="fz14" href="/kcms2/article/abstract?v=abc">标题 A</a>
              <a id="cajDown" href="javascript:void(0)">CAJ下载</a>
            </td>
            <td class="author">张三</td>
            <td class="source"><a>期刊 A</a></td>
            <td class="date">2024-01-02</td>
            <td class="data">CJFD</td>
            <td class="quote">10</td>
            <td class="download">20</td>
          </tr>
        </tbody></table>
        """
        parsed = http_search.parse_result_html(html, "https://kns.cnki.net/kns8s/brief/grid", http_search.SearchSpec(query="x"))

        self.assertNotIn("caj_url", parsed["results"][0])
        self.assertNotIn("download_url", parsed["results"][0])

    def test_sentence_parser_profile(self):
        html = """
        <span class="pagerTitleCell">找到 1 条结果</span>
        <dl class="result-detail-list">
          <dd>
            <h6><a class="fz14" href="/kcms2/article/abstract?v=sen">句子结果</a></h6>
            <div class="baseinfo">作者：王五 来源：期刊 B 2023-05-06</div>
            <h5>机器学习 与 深度学习 出现在同一句。</h5>
            <input class="cbItem" value="SEN1">
          </dd>
        </dl>
        """
        spec = http_search.SearchSpec(query="x", search_mode="sentence")
        parsed = http_search.parse_result_html(html, "https://kns.cnki.net/kns8s/brief/senquery", spec)

        self.assertEqual(parsed["result_format"], "sentence_list")
        self.assertEqual(parsed["results"][0]["title"], "句子结果")
        self.assertEqual(parsed["results"][0]["authors"], "王五")
        self.assertEqual(parsed["results"][0]["journal"], "期刊 B")
        self.assertEqual(parsed["results"][0]["date"], "2023-05-06")
        self.assertIn("同一句", parsed["results"][0]["sentence_context"])

    def test_thesis_table_profile(self):
        html = """
        <span class="pagerTitleCell">找到 1 条结果</span>
        <table class="result-table-list"><tbody>
          <tr>
            <td>1</td><td class="name"><a class="fz14" href="/kcms2/article/abstract?v=t">论文 T</a></td>
            <td class="author">赵六</td><td class="source">大学 C</td><td class="date">2022</td>
          </tr>
        </tbody></table>
        """
        spec = http_search.SearchSpec(query="x", doc_type="thesis")
        parsed = http_search.parse_result_html(html, "https://kns.cnki.net/kns8s/brief/grid", spec)

        self.assertEqual(parsed["result_format"], "thesis_table")
        self.assertEqual(parsed["results"][0]["journal"], "大学 C")


class WorkflowHttpIntegrationTests(StrictHttpOnlyTestCase):
    def test_search_action_uses_http_transport(self):
        fake_payload = {
            "transport": "http",
            "endpoint": http_search.BRIEF_GRID_URL,
            "cookie_source": "test",
            "page_count": 1,
            "total": "1",
            "pages": [
                {
                    "page_no": 1,
                    "page": "1/1",
                    "result_format": "grid_table",
                    "results": [
                        {
                            "title": "标题 A",
                            "authors": "张三",
                            "journal": "期刊 A",
                            "date": "2024",
                            "database": "CJFD",
                            "citations": "1",
                            "downloads": "2",
                            "detail_url": "https://example.invalid/detail",
                            "export_id": "EXP1",
                            "pdf_url": "https://example.invalid/download/pdf",
                        }
                    ],
                }
            ],
            "warnings": [],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkspaceStore(tmpdir)
            with mock.patch.object(workflow_core, "run_http_advsearch_workflow", return_value=fake_payload) as search:
                payload = workflow_core.search_action("机器学习", workspace_id="cws-test", store=store)
            stored_results = store.load_results(payload["workspace_id"], payload["run_id"])

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["summary"]["search_transport"], "http")
        self.assertEqual(payload["workspace_id"], "cws-test")
        self.assertEqual(payload["cache_status"], "miss")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["rows"][0]["title"], "标题 A")
        self.assertEqual(payload["rows"][0]["downloads"], "2")
        self.assertEqual(payload["rows"][0]["citations"], "1")
        self.assertNotIn("detail_url", payload["rows"][0])
        self.assertNotIn("pdf_url", payload["rows"][0])
        self.assertEqual(stored_results[0]["pdf_url"], "https://example.invalid/download/pdf")
        search.assert_called_once()

    def test_search_action_reuses_same_signature_run_until_refresh(self):
        first_payload = {
            "transport": "http",
            "page_count": 1,
            "total": "1",
            "pages": [{"page_no": 1, "page": "1/1", "results": [{"title": "旧标题", "authors": "张三", "downloads": "2", "citations": "1"}]}],
            "warnings": [],
        }
        refreshed_payload = {
            "transport": "http",
            "page_count": 1,
            "total": "1",
            "pages": [{"page_no": 1, "page": "1/1", "results": [{"title": "新标题", "authors": "李四", "downloads": "3", "citations": "2"}]}],
            "warnings": [],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkspaceStore(tmpdir)
            with mock.patch.object(workflow_core, "run_http_advsearch_workflow", return_value=first_payload) as search:
                first = workflow_core.search_action("机器学习", workspace_id="cws-test", label="医学影像", store=store)
                hit = workflow_core.search_action("机器学习", workspace_id="cws-test", label="医学影像", store=store)
            with mock.patch.object(workflow_core, "run_http_advsearch_workflow", return_value=refreshed_payload) as refresh_search:
                refreshed = workflow_core.search_action("机器学习", workspace_id="cws-test", label="医学影像", refresh=True, store=store)
            stored_results = store.load_results("cws-test", first["run_id"])

        self.assertEqual(first["run_id"], hit["run_id"])
        self.assertEqual(first["run_id"], refreshed["run_id"])
        self.assertEqual(hit["cache_status"], "hit")
        self.assertEqual(refreshed["cache_status"], "refresh")
        self.assertEqual(hit["rows"][0]["title"], "旧标题")
        self.assertEqual(refreshed["rows"][0]["title"], "新标题")
        self.assertEqual(stored_results[0]["title"], "新标题")
        self.assertEqual(search.call_count, 1)
        refresh_search.assert_called_once()

    def test_strict_http_failure_returns_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkspaceStore(tmpdir)
            with mock.patch.object(
                workflow_core,
                "run_http_advsearch_workflow",
                side_effect=http_search.HttpSearchUnavailable("http_captcha", "captcha"),
            ):
                payload = workflow_core.search_action("机器学习", workspace_id="cws-test", store=store)

        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["error"], "http_captcha")


class HttpSearchRuntimeTests(StrictHttpOnlyTestCase):
    def test_sorted_pagination_uses_initial_sort_then_page_requests(self):
        calls = []

        def fake_request(method, url, cookie, form, timeout):
            calls.append(dict(form))
            page = form["pageNum"]
            title = "第一页" if len(calls) < 3 else "第二页"
            turnpage = "TURN1" if len(calls) == 1 else "TURN2"
            html = f"""
            <span class="pagerTitleCell">找到 40 条结果</span><span class="countPageMark">{page}/2</span>
            <input id="hidTurnPage" type="hidden" value="{turnpage}">
            <table class="result-table-list"><tbody>
              <tr>
                <td>1</td>
                <td class="name"><a class="fz14" href="/kcms2/article/abstract?v={page}">{title}</a></td>
                <td class="author">作者</td><td class="source">来源</td><td class="date">2024</td>
                <td class="data">CJFD</td><td class="quote">1</td><td class="download">2</td>
              </tr>
            </tbody></table>
            """
            return http_search.HttpResponse(url=url, status=200, headers={}, body=html, elapsed_ms=1)

        spec = http_search.SearchSpec(query="机器学习", doc_type="journal", sort="downloads", page_limit=2, page_size=1)
        with mock.patch.object(http_search, "_load_http_cookie", return_value=("COOKIE", "test")):
            with mock.patch.object(http_search, "_request", side_effect=fake_request):
                payload = http_search.run_http_advsearch_workflow(spec)

        self.assertEqual(payload["transport"], "http")
        self.assertEqual(payload["page_count"], 2)
        self.assertEqual(len(calls), 3)

        initial_query = json.loads(calls[0]["QueryJson"])
        sort_query = json.loads(calls[1]["QueryJson"])
        page_query = json.loads(calls[2]["QueryJson"])

        self.assertEqual(calls[0]["boolSearch"], "true")
        self.assertNotIn("turnpage", calls[0])
        self.assertEqual(initial_query["SearchFrom"], 1)
        self.assertEqual(calls[1]["boolSearch"], "false")
        self.assertEqual(calls[1]["boolSortSearch"], "true")
        self.assertEqual(calls[1]["turnpage"], "TURN1")
        self.assertEqual(calls[1]["sortField"], "DFR")
        self.assertEqual(sort_query["SearchFrom"], 5)
        self.assertEqual(calls[2]["boolSearch"], "false")
        self.assertEqual(calls[2]["boolSortSearch"], "false")
        self.assertEqual(calls[2]["turnpage"], "TURN2")
        self.assertEqual(calls[2]["sortField"], "DFR")
        self.assertNotIn("CurPage", calls[2])
        self.assertEqual(page_query["SearchFrom"], 4)

    def test_later_no_results_keeps_previous_pages(self):
        calls = []

        def fake_request(method, url, cookie, form, timeout):
            calls.append(dict(form))
            if form["pageNum"] == "2":
                html = "<div>抱歉，暂无数据，请稍后重试。</div>"
            else:
                html = """
                <span class="pagerTitleCell">找到 20 条结果</span><span class="countPageMark">1/1</span>
                <input id="hidTurnPage" type="hidden" value="TURN1">
                <table class="result-table-list"><tbody>
                  <tr>
                    <td>1</td><td class="name"><a class="fz14" href="/kcms2/article/abstract?v=1">第一页</a></td>
                    <td class="author">作者</td><td class="source">来源</td><td class="date">2024</td>
                  </tr>
                </tbody></table>
                """
            return http_search.HttpResponse(url=url, status=200, headers={}, body=html, elapsed_ms=1)

        spec = http_search.SearchSpec(query="机器学习", page_limit=2, page_size=1)
        with mock.patch.object(http_search, "_load_http_cookie", return_value=("COOKIE", "test")):
            with mock.patch.object(http_search, "_request", side_effect=fake_request):
                payload = http_search.run_http_advsearch_workflow(spec)

        self.assertEqual(payload["status"] if "status" in payload else "ok", "ok")
        self.assertEqual(payload["page_count"], 1)
        self.assertEqual(payload["pages"][0]["results"][0]["title"], "第一页")


if __name__ == "__main__":
    unittest.main()
