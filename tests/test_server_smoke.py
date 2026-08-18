import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from server import Handler


class ServerSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_health_and_search_page(self):
        with urllib.request.urlopen(self.base + "/api/health", timeout=3) as response:
            health = json.loads(response.read().decode("utf-8"))
        self.assertEqual(health["version"], "30.0")
        with urllib.request.urlopen(self.base + "/search.html", timeout=3) as response:
            html = response.read().decode("utf-8")
        self.assertIn("数据要素资料检索", html)
        self.assertIn("国内优先，国际补充", html)

    def test_search_contract_without_provider(self):
        payload = json.dumps({
            "query": "数据",
            "description": "关注数据要素治理对企业和个人的价值，优先国内政策和近期新闻",
            "types": ["news", "policy", "paper"],
            "regionPreference": "domestic-first",
            "timeRange": "latest",
            "maxResults": 20,
        }, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base + "/api/search", data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            result = json.loads(response.read().decode("utf-8"))
        self.assertEqual(result["understanding"]["regionPreference"], "domestic-first")
        self.assertEqual(result["meta"]["regionPreference"], "domestic-first")
        self.assertIn("国内", result["understanding"]["intentSummary"])


if __name__ == "__main__":
    unittest.main()
