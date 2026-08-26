import unittest

from research import ResearchSession, _public_url, _unwrap_ddg_url


class ResearchToolsTest(unittest.TestCase):
    def test_blocks_local_network_targets(self) -> None:
        for url in ("http://127.0.0.1/", "http://localhost/", "http://[::1]/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _public_url(url)

    def test_unwraps_duckduckgo_redirect(self) -> None:
        wrapped = "/l/?uddg=https%3A%2F%2Fexample.com%2Freport%3Fa%3D1"
        self.assertEqual(_unwrap_ddg_url(wrapped), "https://example.com/report?a=1")

    def test_source_registry_deduplicates_and_manifests_reads(self) -> None:
        session = ResearchSession()
        first = session._register("https://example.com/a", "A")
        second = session._register("https://example.com/a", snippet="Lead")
        self.assertEqual(first.source_id, second.source_id)
        self.assertEqual(second.snippet, "Lead")
        self.assertEqual(session.manifest(), [])
        second.fetched = True
        self.assertEqual(session.manifest()[0]["source_id"], "S1")


if __name__ == "__main__":
    unittest.main()
