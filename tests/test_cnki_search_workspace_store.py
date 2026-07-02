import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skill" / "cnki-search"
sys.path.insert(0, str(SKILL_DIR / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from actions import _workflow_impl as workflow_core
from core.state_store import WorkspaceLockTimeout, WorkspaceStore, parse_utc
from cnki_search_test_helpers import create_workspace_run


class WorkspaceStoreTests(unittest.TestCase):
    def test_create_workspace_sets_12_hour_expiry_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkspaceStore(tmpdir)
            before = datetime.now(timezone.utc)
            workspace = store.create_workspace("cws-test")
            expires_at = parse_utc(workspace["expires_at"])

        self.assertEqual(workspace["workspace_id"], "cws-test")
        self.assertIsNotNone(expires_at)
        self.assertGreaterEqual(expires_at, before + timedelta(hours=11, minutes=59))
        self.assertLessEqual(expires_at, before + timedelta(hours=12, minutes=1))

    def test_cleanup_expired_only_removes_valid_expired_workspaces(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkspaceStore(tmpdir)
            active = store.create_workspace("cws-active")
            expired = store.create_workspace("cws-expired")
            expired["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
            store.save_workspace(expired)
            non_workspace = Path(tmpdir) / "plain-dir"
            non_workspace.mkdir()
            (non_workspace / "note.txt").write_text("keep", encoding="utf-8")

            removed = store.cleanup_expired()

            self.assertEqual(active["workspace_id"], "cws-active")
            self.assertEqual(removed, ["cws-expired"])
            self.assertTrue((Path(tmpdir) / "cws-active" / "workspace.json").exists())
            self.assertFalse((Path(tmpdir) / "cws-expired").exists())
            self.assertTrue(non_workspace.exists())

    def test_non_search_command_without_workspace_returns_structured_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkspaceStore(tmpdir)
            payload = workflow_core.fetch_details_action(workspace_id="missing", rows=[1], store=store)

        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["error"], "workspace_not_found")

    def test_merge_results_updates_rows_without_replacing_unrelated_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkspaceStore(tmpdir)
            create_workspace_run(
                store,
                results=[
                    {"row_id": "row-0001", "global_rank": 1, "page_no": 1, "page_row_no": 1, "title": "A"},
                    {"row_id": "row-0002", "global_rank": 2, "page_no": 1, "page_row_no": 2, "title": "B"},
                ],
            )
            merged = store.merge_results("cws-test", "run-test", [{"global_rank": 2, "download_status": "downloaded"}])

        self.assertEqual(merged[0]["title"], "A")
        self.assertEqual(merged[1]["title"], "B")
        self.assertEqual(merged[1]["download_status"], "downloaded")

    def test_run_lock_timeout_is_structured(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkspaceStore(tmpdir)
            store.create_workspace("cws-test")
            with store.with_run_lock("cws-test", "run-test"):
                with mock.patch.dict(os.environ, {"CNKI_WORKSPACE_LOCK_TIMEOUT": "0.01"}):
                    with self.assertRaises(WorkspaceLockTimeout) as raised:
                        with store.with_run_lock("cws-test", "run-test"):
                            pass

        self.assertEqual(raised.exception.code, "workspace_lock_timeout")

    def test_workspace_layout_uses_runs_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkspaceStore(tmpdir)
            create_workspace_run(store, results=[{"row_id": "row-0001", "global_rank": 1, "page_no": 1, "page_row_no": 1, "title": "A"}])
            root = Path(tmpdir) / "cws-test"
            run_root = root / "runs" / "run-test"
            workspace = json.loads((root / "workspace.json").read_text(encoding="utf-8"))
            run_json_exists = (run_root / "run.json").exists()
            results_json_exists = (run_root / "results.json").exists()
            details_json_exists = (run_root / "details.json").exists()
            artifacts_json_exists = (run_root / "artifacts.json").exists()

        self.assertEqual(workspace["active_run_id"], "run-test")
        self.assertTrue(run_json_exists)
        self.assertTrue(results_json_exists)
        self.assertTrue(details_json_exists)
        self.assertTrue(artifacts_json_exists)


if __name__ == "__main__":
    unittest.main()
