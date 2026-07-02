import io
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skill" / "cnki-search"
sys.path.insert(0, str(SKILL_DIR))
sys.path.insert(0, str(SKILL_DIR.parent))

import run as cnki_search_run


class CnkiSearchRunCliTests(unittest.TestCase):
    def test_download_dispatches_selected_rows(self):
        payload = {
            "status": "ok",
            "workspace_id": "cws-test",
            "run_id": "run-test",
            "action": "download",
            "count": 0,
            "summary": {},
            "rows": [],
            "returned_fields": [],
            "warnings": [],
        }
        argv = ["run.py", "download", "--workspace", "cws-test", "--run", "run-test", "--rows", "1"]
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.object(cnki_search_run, "download_action", return_value=payload) as action:
                with mock.patch("sys.stdout", new=io.StringIO()):
                    rc = cnki_search_run.main()

        self.assertEqual(rc, 0)
        action.assert_called_once()
        self.assertEqual(action.call_args.kwargs["workspace_id"], "cws-test")
        self.assertEqual(action.call_args.kwargs["run_id"], "run-test")
        self.assertEqual(action.call_args.kwargs["rows"], [1])

    def test_download_concurrency_dispatches_to_action(self):
        payload = {
            "status": "ok",
            "workspace_id": "cws-test",
            "run_id": "run-test",
            "action": "download",
            "count": 0,
            "summary": {},
            "rows": [],
            "returned_fields": [],
            "warnings": [],
        }
        argv = ["run.py", "download", "--workspace", "cws-test", "--run", "run-test", "--rows", "1", "--concurrency", "4"]
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.object(cnki_search_run, "download_action", return_value=payload) as action:
                with mock.patch("sys.stdout", new=io.StringIO()):
                    rc = cnki_search_run.main()

        self.assertEqual(rc, 0)
        action.assert_called_once()
        self.assertEqual(action.call_args.kwargs["direct_concurrency"], 4)


if __name__ == "__main__":
    unittest.main()
