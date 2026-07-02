import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
CNKI_SEARCH_SKILL_DIR = REPO_ROOT / "skill" / "cnki-search"
CNKI_SEARCH_SRC_DIR = CNKI_SEARCH_SKILL_DIR / "src"

sys.path.insert(0, str(CNKI_SEARCH_SRC_DIR))

from core import http as cnki_search_http


class CookiePriorityTests(unittest.TestCase):
    def test_cnki_search_http_prefers_ip_login_over_env_and_file(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("FILE_COOKIE=1")
            cookie_file = handle.name
        try:
            with mock.patch.dict(os.environ, {"CNKI_COOKIE": "ENV_COOKIE=1", "CNKI_COOKIE_FILE": cookie_file}, clear=False):
                with mock.patch.object(cnki_search_http, "_ip_login_cookie_seed", return_value=("IP_COOKIE=1", "ip_login")):
                    cookie, source = cnki_search_http._load_cookie_seed()

            self.assertEqual(cookie, "IP_COOKIE=1")
            self.assertEqual(source, "ip_login")
        finally:
            Path(cookie_file).unlink(missing_ok=True)

    def test_cnki_search_http_falls_back_to_file_after_ip_login_failure(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("FILE_COOKIE=1")
            cookie_file = handle.name
        try:
            with mock.patch.dict(os.environ, {"CNKI_COOKIE_FILE": cookie_file}, clear=True):
                with mock.patch.object(cnki_search_http, "_ip_login_cookie_seed", return_value=("", "ip_login_failed")):
                    cookie, source = cnki_search_http._load_cookie_seed()

            self.assertEqual(cookie, "FILE_COOKIE=1")
            self.assertEqual(source, "file_fallback_after_ip_login_failed")
        finally:
            Path(cookie_file).unlink(missing_ok=True)

if __name__ == "__main__":
    unittest.main()
