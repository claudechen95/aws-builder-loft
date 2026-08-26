import unittest

from research import ResearchSession, _public_url, _unwrap_ddg_url
from agent import _depth_gaps, _valid_citations


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
        self.assertEqual(session.manifest()[0]["source_role"], "context")

    def test_distinct_searches_use_normalized_focus_labels(self) -> None:
        session = ResearchSession()
        session._search_focuses.add(session._normalize_focus(" Historical record "))
        session._search_focuses.add(session._normalize_focus("historical-record"))
        session._search_focuses.add(session._normalize_focus("Sound changes"))
        self.assertEqual(session.distinct_search_count, 2)

    def test_depth_gaps_require_domains_and_authoritative_sources(self) -> None:
        session = ResearchSession()
        session._search_focuses.update({"official data", "history", "criticism"})
        roles_and_urls = (
            ("primary", "https://agency.example/a"),
            ("authoritative_reference", "https://dictionary.example/b"),
            ("independent_secondary", "https://news.example/c"),
            ("context", "https://news.example/d"),
        )
        for role, url in roles_and_urls:
            source = session._register(url, url)
            source.fetched = True
            source.source_role = role
            session.read_count += 1
        self.assertEqual(_depth_gaps(session), [])

    def test_depth_gaps_report_repeated_domain_and_weak_sources(self) -> None:
        session = ResearchSession()
        session._search_focuses.update({"official", "history", "criticism"})
        for suffix in "abcd":
            source = session._register(f"https://wiki.example/{suffix}", suffix)
            source.fetched = True
            source.source_role = "context"
            session.read_count += 1
        gaps = _depth_gaps(session)
        self.assertIn("source domains 1/3", gaps)
        self.assertIn("primary or authoritative sources 0/2", gaps)

    def test_citation_validation_rejects_missing_and_invented_ids(self) -> None:
        session = ResearchSession()
        source = session._register("https://example.com/a", "A")
        source.fetched = True
        self.assertFalse(_valid_citations("Unsupported prose.", session)[0])
        self.assertFalse(_valid_citations("Invented [S2].", session)[0])
        self.assertTrue(_valid_citations("Supported [S1].", session)[0])


if __name__ == "__main__":
    unittest.main()
