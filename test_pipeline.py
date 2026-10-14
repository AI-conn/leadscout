"""LeadScout test suite. Runs fully offline with an injected fake transport
and real TinyFish responses captured on 2026-10-13 (tests/fixtures/)."""

import csv
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import leadscout
from tinyfish_client import TinyFishClient, TinyFishError

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


class FakeTransport:
    """Returns the captured search payload for any search URL and the
    captured fetch payload for any fetch call, unless overridden."""

    def __init__(self, search_payload=None, fetch_payload=None):
        self.search_payload = search_payload if search_payload is not None else load_fixture("search_coffee_austin.json")
        self.fetch_payload = fetch_payload if fetch_payload is not None else load_fixture("fetch_pages.json")

    def __call__(self, method, url, payload=None, api_key=None):
        if "api.search" in url:
            return self.search_payload
        return self.fetch_payload


def quiet(_msg):
    pass


class PlanAndClassifyTests(unittest.TestCase):
    def test_plan_queries_cover_target_and_location(self):
        queries = leadscout.plan_queries("coffee shops", "Austin, TX")
        self.assertEqual(len(queries), 3)
        for query in queries:
            self.assertIn("coffee shops", query)
            self.assertIn("Austin, TX", query)

    def test_classify_sources(self):
        self.assertEqual(leadscout.classify_source("https://www.yelp.com/search?x=1"), "listing_or_social")
        self.assertEqual(leadscout.classify_source("https://www.bennucoffee.com/"), "business_site")
        self.assertEqual(leadscout.classify_source("https://austin.eater.com/maps/x"), "listing_or_social")

    def test_dedupe_collapses_variants_and_ranks_business_first(self):
        results = [
            {"url": "https://www.yelp.com/biz/a", "position": 1},
            {"url": "https://www.bennucoffee.com/", "position": 2},
            {"url": "https://www.bennucoffee.com", "position": 3},
            {"url": "https://bennucoffee.com/", "position": 4},
        ]
        candidates = leadscout.dedupe_candidates(results)
        self.assertEqual(len(candidates), 3)
        self.assertEqual(candidates[0]["source_type"], "business_site")


class ExtractionTests(unittest.TestCase):
    def test_real_page_without_contact_info_yields_nulls_not_inventions(self):
        page = load_fixture("fetch_pages.json")["results"][0]
        lead = leadscout.extract_lead(page, page["url"])
        self.assertTrue(lead["name"].startswith("Bennu Coffee"))
        self.assertIsNone(lead["email"])
        self.assertIsNone(lead["phone"])
        self.assertIsNone(lead["address"])
        self.assertLessEqual(lead["completeness"], 0.25)

    def test_extraction_finds_present_contact_details(self):
        page = {
            "title": "Sunrise Bakery | Portland",
            "url": "https://sunrisebakery.example/",
            "final_url": "https://sunrisebakery.example/",
            "text": "Visit us at 4210 SE Division Street, Portland. Call (503) 555-0142 or email hello@sunrisebakery.example for catering.",
        }
        lead = leadscout.extract_lead(page, page["url"])
        self.assertEqual(lead["name"], "Sunrise Bakery")
        self.assertEqual(lead["email"], "hello@sunrisebakery.example")
        self.assertEqual(lead["phone"], "(503) 555-0142")
        self.assertIn("Division Street", lead["address"])
        self.assertEqual(lead["completeness"], 1.0)

    def test_malformed_page_does_not_crash(self):
        lead = leadscout.extract_lead({}, "https://example.com/")
        self.assertIsNone(lead["email"])
        self.assertEqual(lead["website"], "https://example.com/")


class SweepTests(unittest.TestCase):
    def test_sweep_with_real_fixtures_produces_leads(self):
        client = TinyFishClient(api_key="test", transport=FakeTransport())
        report = leadscout.run_sweep(client, "coffee shops", "Austin, TX", limit=5, log=quiet)
        self.assertGreater(report["candidates_seen"], 0)
        self.assertGreater(len(report["leads"]), 0)
        self.assertLessEqual(len(report["leads"]), 5)
        for lead in report["leads"]:
            self.assertIn("source_url", lead)
            self.assertTrue(0 <= lead["completeness"] <= 1)

    def test_empty_search_returns_empty_leads_without_crashing(self):
        transport = FakeTransport(search_payload={"results": []}, fetch_payload={"results": [], "errors": []})
        client = TinyFishClient(api_key="test", transport=transport)
        report = leadscout.run_sweep(client, "nothing", "nowhere", limit=5, log=quiet)
        self.assertEqual(report["leads"], [])
        self.assertEqual(report["candidates_seen"], 0)

    def test_error_payload_raises_tinyfish_error(self):
        transport = FakeTransport(search_payload={"error": "quota exhausted"})
        client = TinyFishClient(api_key="test", transport=transport)
        with self.assertRaises(TinyFishError):
            leadscout.run_sweep(client, "x", "y", log=quiet)

    def test_fetch_errors_are_tolerated(self):
        transport = FakeTransport(fetch_payload={"results": [], "errors": [{"url": "x", "error": "timeout"}]})
        client = TinyFishClient(api_key="test", transport=transport)
        report = leadscout.run_sweep(client, "coffee shops", "Austin, TX", limit=5, log=quiet)
        self.assertEqual(report["leads"], [])


class RegressionTests(unittest.TestCase):
    """Regression coverage for Stress Gate findings F1, F2, F3."""

    def test_reddit_is_listing_or_social(self):
        self.assertEqual(leadscout.classify_source("https://www.reddit.com/r/austin/comments/x"), "listing_or_social")

    def test_business_sites_rank_ahead_of_listings_regardless_of_completeness(self):
        search_payload = {"results": [
            {"url": "https://www.reddit.com/r/austin/comments/x", "position": 1, "snippet": "s"},
            {"url": "https://www.bennucoffee.com/", "position": 2, "snippet": "s"},
        ]}
        fetch_payload = {"results": [
            {"url": "https://www.reddit.com/r/austin/comments/x", "final_url": "https://www.reddit.com/r/austin/comments/x",
             "title": "Coffee thread", "text": "Meet at 4210 SE Division Street or call (503) 555-0142, mail a@b.example"},
            {"url": "https://www.bennucoffee.com/", "final_url": "https://www.bennucoffee.com/",
             "title": "Bennu Coffee", "text": "A coffee shop."},
        ], "errors": []}
        client = TinyFishClient(api_key="test", transport=FakeTransport(search_payload, fetch_payload))
        report = leadscout.run_sweep(client, "coffee shops", "Austin, TX", limit=5, log=quiet)
        self.assertEqual(report["leads"][0]["source_type"], "business_site")

    def test_website_is_normalized_without_tracking_params(self):
        page = {"title": "Cuvee Coffee", "url": "https://cuveecoffee.com/?srsltid=XYZ",
                "final_url": "https://cuveecoffee.com/?srsltid=XYZ", "text": "Coffee."}
        lead = leadscout.extract_lead(page, page["url"])
        self.assertEqual(lead["website"], "https://cuveecoffee.com/")

    def test_listicle_titles_are_demoted_to_listing(self):
        results = [
            {"url": "https://fltrmagazine.com/best-coffee", "position": 1, "title": "13 Best Specialty Coffee Shops in Austin"},
            {"url": "https://www.bennucoffee.com/", "position": 2, "title": "Bennu Coffee"},
        ]
        candidates = leadscout.dedupe_candidates(results)
        self.assertEqual(candidates[0]["url"], "https://www.bennucoffee.com/")
        self.assertEqual(candidates[1]["source_type"], "listing_or_social")

    def test_year_fragment_is_not_an_address(self):
        page = {"title": "Roundup", "url": "https://blog.example/x", "final_url": "https://blog.example/x",
                "text": "The shop relocated in 2016 on Webberville Road. Patrick now runs four locations across town."}
        lead = leadscout.extract_lead(page, page["url"])
        self.assertIsNone(lead["address"])

    def test_colon_tagline_is_trimmed_from_name(self):
        page = {"title": "Bennu Coffee: Austin's Premier 24-Hour Coffee Shop",
                "url": "https://www.bennucoffee.com/", "final_url": "https://www.bennucoffee.com/", "text": "Coffee."}
        lead = leadscout.extract_lead(page, page["url"])
        self.assertEqual(lead["name"], "Bennu Coffee")


class OutputTests(unittest.TestCase):
    def test_csv_escapes_commas_and_round_trips(self):
        report = {
            "target": "bakeries", "location": "Portland, OR", "queries": [], "candidates_seen": 1,
            "leads": [{"name": "Doe, John Bakery", "website": "https://x.example", "email": None,
                       "phone": None, "address": None, "source_url": "https://x.example", "completeness": 0.25}],
        }
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            paths = leadscout.write_outputs(report, os.path.join(tmp, "out"))
            with open(paths[1], encoding="utf-8") as handle:
                rows = list(csv.reader(io.StringIO(handle.read())))
            self.assertEqual(rows[0][0], "name")
            self.assertEqual(rows[1][0], "Doe, John Bakery")
            with open(paths[0], encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["leads"][0]["name"], "Doe, John Bakery")


if __name__ == "__main__":
    unittest.main(verbosity=2)
