# LeadScout

An agentic lead collector built on TinyFish. Give it a business type and a location, and it plans its own searches, reads the live web, and hands you a structured lead list.

## What it does

1. Plans three searches for the target (official sites, independent roundups, contact pages).
2. Searches the live web with TinyFish Search.
3. Dedupes candidates and ranks each business's own site ahead of directories, forums, and roundup articles.
4. Fetches the real page content with TinyFish Fetch.
5. Extracts name, email, phone, and street address from each page, scores completeness, and writes the leads as JSON, CSV, and a Markdown summary.

Integrity rule: a field is filled only from text TinyFish actually returned. If a business does not publish a phone or email on its page, the field is null. LeadScout never invents contact details.

## Setup

- Python 3.10 or newer. No third-party packages.
- A TinyFish API key in the environment:

```bash
export TINYFISH_API_KEY=<your key>
```

## Run

```bash
python3 leadscout.py --target "coffee shops" --location "Austin, TX" --limit 10 --out leads
```

This writes leads.json, leads.csv, and leads.md. The console shows each step as it happens: planned queries, results per query, unique candidates, pages fetched, and the final lead count.

## Output

Each lead carries name, website, email, phone, address, the source URL it was extracted from, a source_type (business_site or listing_or_social), the search snippet that discovered it, and a completeness score from 0 to 1. A real sample from a live run is in examples/sample-output.json.

## Tests

The suite runs offline against real TinyFish responses captured on 2026-10-13, with the network replaced by an injected transport:

```bash
python3 -m unittest discover -s tests
```

17 tests cover query planning, source classification (including listicle demotion), dedupe, extraction with and without published contact details, address-pattern edge cases, empty and error responses, and output formatting.

## Verified live

On 2026-10-13, a live run for coffee shops in Austin, TX read 30 search results, deduped to 21 candidates, fetched 8 pages, and produced 5 leads, all from business sites, with one real street address extracted and no invented fields.

## Limits

- Extraction is pattern-based. A business that hides contact details behind a form or a script will show nulls, which is the honest answer.
- Classification is heuristic. An unusual business name can occasionally be demoted with the roundups, and an unusual roundup can occasionally rank as a business page. The source_type field is always shown so a human can re-sort.
