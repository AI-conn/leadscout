#!/usr/bin/env python3
"""LeadScout: an agentic lead collector built on TinyFish Search and Fetch.

Give it a business type and a location. It plans search queries, searches
the live web, picks the most promising pages, fetches their real content,
extracts contact details, dedupes, scores completeness, and writes a lead
list as JSON, CSV, and a short Markdown summary.

Integrity rule: a field is only filled from text TinyFish actually
returned. Anything not found stays null. LeadScout never invents a phone
number, email, or address.
"""

import argparse
import csv
import io
import json
import re
import sys
from urllib.parse import urlparse

from tinyfish_client import TinyFishClient, TinyFishError

AGGREGATOR_DOMAINS = {
    "yelp.com", "tripadvisor.com", "facebook.com", "instagram.com",
    "eater.com", "google.com", "bing.com", "mapquest.com", "yellowpages.com",
    "thumbtack.com", "angi.com", "bbb.org", "chamberofcommerce.com",
    "reddit.com", "x.com", "twitter.com", "quora.com",
}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}")
ADDRESS_RE = re.compile(
    r"\d{1,6}\s+(?:[A-Z][A-Za-z']+\s+){1,3}(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|Hwy|Highway|Ct|Court|Plz|Plaza)\b[^,\n.]{0,30}",
)
LISTICLE_TITLE_RE = re.compile(r"(?i)^\s*\d+\s+(best|top)\b|\b(best|top)\s+\d+\b|\bbest\b[^.]{0,60}\bin\b")


def plan_queries(target, location):
    base = f"{target} in {location}".strip()
    return [
        f"{base} official website",
        f"best independent {base}",
        f"{base} contact",
    ]


def classify_source(url):
    host = (urlparse(url).hostname or "").lower()
    bare = host[4:] if host.startswith("www.") else host
    for domain in AGGREGATOR_DOMAINS:
        if bare == domain or bare.endswith("." + domain):
            return "listing_or_social"
    return "business_site"


def normalize_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme.lower()}://{host}{path}"


def dedupe_candidates(results):
    seen = {}
    for item in results:
        url = item.get("url")
        if not url:
            continue
        key = normalize_url(url)
        if key not in seen:
            entry = dict(item)
            entry["source_type"] = classify_source(url)
            if entry["source_type"] == "business_site" and LISTICLE_TITLE_RE.search(item.get("title") or ""):
                entry["source_type"] = "listing_or_social"
            seen[key] = entry
    candidates = list(seen.values())
    candidates.sort(key=lambda c: (c["source_type"] != "business_site", int(c.get("position") or 99)))
    return candidates


def clean_name(title, url):
    if title:
        name = re.split(r"\s[|\u2013\u2014-]\s", title)[0].strip()
        if ": " in name and len(name.split(": ")[0]) <= 40:
            name = name.split(": ")[0].strip()
        if 2 <= len(name) <= 80:
            return name
    host = (urlparse(url).hostname or "").replace("www.", "")
    return host.split(".")[0].replace("-", " ").title() if host else None


def extract_lead(page, source_url):
    text = page.get("text") or ""
    emails = sorted(set(EMAIL_RE.findall(text)))
    phones = sorted(set(p.strip() for p in PHONE_RE.findall(text)))
    address_match = ADDRESS_RE.search(text)
    source_type = classify_source(source_url)
    if source_type == "business_site" and LISTICLE_TITLE_RE.search(page.get("title") or ""):
        source_type = "listing_or_social"
    lead = {
        "name": clean_name(page.get("title"), source_url),
        "website": normalize_url(source_url),
        "source_type": source_type,
        "email": emails[0] if emails else None,
        "phone": phones[0] if phones else None,
        "address": address_match.group(0).strip() if address_match else None,
        "source_url": page.get("final_url") or source_url,
        "evidence": (page.get("title") or "")[:120],
    }
    present = sum(1 for key in ("name", "email", "phone", "address") if lead[key])
    lead["completeness"] = round(present / 4, 2)
    return lead


def run_sweep(client, target, location, limit=10, log=print):
    queries = plan_queries(target, location)
    log(f"[plan] {len(queries)} queries for '{target}' in '{location}'")
    raw_results = []
    for query in queries:
        found = client.search(query)
        log(f"[search] '{query}' -> {len(found)} results")
        raw_results.extend(found)
    candidates = dedupe_candidates(raw_results)
    log(f"[select] {len(candidates)} unique candidates after dedupe")
    pages_to_fetch = [c["url"] for c in candidates[: max(limit * 2, limit)]]
    leads = []
    if pages_to_fetch:
        pages, errors = client.fetch(pages_to_fetch)
        log(f"[fetch] {len(pages)} pages fetched, {len(errors)} errors")
        by_url = {normalize_url(c["url"]): c for c in candidates}
        for page in pages:
            source = page.get("url") or page.get("final_url")
            lead = extract_lead(page, source)
            origin = by_url.get(normalize_url(source or ""))
            lead["discovery_snippet"] = (origin or {}).get("snippet", "")[:200]
            leads.append(lead)
    leads.sort(key=lambda lead: (lead["source_type"] != "business_site", -lead["completeness"]))
    leads = leads[:limit]
    log(f"[done] {len(leads)} leads")
    return {
        "target": target,
        "location": location,
        "queries": queries,
        "candidates_seen": len(candidates),
        "leads": leads,
    }


def write_outputs(report, out_prefix):
    json_path = out_prefix + ".json"
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    csv_path = out_prefix + ".csv"
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["name", "website", "email", "phone", "address", "source_url", "source_type", "completeness"])
    for lead in report["leads"]:
        writer.writerow([lead["name"], lead["website"], lead["email"] or "", lead["phone"] or "",
                         lead["address"] or "", lead["source_url"], lead.get("source_type", ""), lead["completeness"]])
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        handle.write(buffer.getvalue())
    md_path = out_prefix + ".md"
    lines = [f"# Leads: {report['target']} in {report['location']}", ""]
    for lead in report["leads"]:
        lines.append(f"- **{lead['name']}** ({lead['website']})")
        lines.append(f"  Email: {lead['email'] or 'not found'} | Phone: {lead['phone'] or 'not found'} | Address: {lead['address'] or 'not found'}")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return json_path, csv_path, md_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Agentic lead collector on TinyFish Search and Fetch.")
    parser.add_argument("--target", required=True, help="Business type, e.g. 'coffee shops'")
    parser.add_argument("--location", required=True, help="Location, e.g. 'Austin, TX'")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--out", default="leads", help="Output file prefix")
    args = parser.parse_args(argv)
    client = TinyFishClient()
    try:
        report = run_sweep(client, args.target, args.location, limit=args.limit)
    except TinyFishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    paths = write_outputs(report, args.out)
    print("wrote: " + ", ".join(paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
