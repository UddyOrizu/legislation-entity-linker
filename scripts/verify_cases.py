#!/usr/bin/env python3
"""Check every case registry entry against live Find Case Law.

A registry entry maps a case name and its report citations to a neutral
citation, and a wrong neutral citation produces a confidently wrong link. For
each case linked to Find Case Law this fetches the judgment page and checks
that it exists and that its title matches the registered name.

    python scripts/verify_cases.py                  # check the seed registry
    python scripts/verify_cases.py extra.json       # check your own file

Cases linked to BAILII are reported as SKIP: BAILII refuses automated
requests, so they cannot be checked this way. Report citations cannot be
checked either -- there is no free authoritative source -- which is why the
resolver cross-checks them against the cited case name.

Exits non-zero if any checkable entry fails, so it can gate a release.
"""

from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from legislink.cases import CaseRegistry  # noqa: E402
from legislink.courts import TNA  # noqa: E402
from legislink.normalize import normalize_case_name  # noqa: E402
from legislink.resolve import _name_agreement  # noqa: E402

TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
SITE_SUFFIX = re.compile(r"\s+-\s+Find Case Law.*$")


def check(client, case, name_cutoff: float) -> tuple[str, str]:
    """Return (status, detail) for one case: OK, SKIP, NAME, HTTP or ERROR."""
    url = case.url
    if url is None:
        return "SKIP", "no direct link"
    if not url.startswith(TNA):
        return "SKIP", f"not checkable: {url}"
    try:
        response = client.get(url)
    except Exception as exc:
        return "ERROR", f"{type(exc).__name__}"

    if response.status_code != 200:
        return "HTTP", f"{response.status_code} {url}"

    match = TITLE_RE.search(response.text)
    if not match:
        return "OK", ""
    page_title = SITE_SUFFIX.sub("", re.sub(r"\s+", " ", match.group(1)).strip())
    score = max(
        _name_agreement(normalize_case_name(n), normalize_case_name(page_title))
        for n in (case.name, *case.aliases)
    )
    if score < name_cutoff:
        return "NAME", f"registry={case.name!r} site={page_title!r} ({score:.2f})"
    return "OK", ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="case registry JSON files (default: the seed)")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=40.0)
    parser.add_argument("--name-cutoff", type=float, default=0.6)
    args = parser.parse_args()

    try:
        import httpx
    except ImportError:
        print("this script needs httpx: pip install legislink[remote]", file=sys.stderr)
        return 2

    registry = CaseRegistry() if args.files else CaseRegistry.default()
    for path in args.files:
        registry.load_file(path)

    cases = sorted(registry, key=lambda c: (c.year or 0, c.name))
    print(f"checking {len(cases)} cases against Find Case Law...\n")

    failures: list[tuple[str, str, str]] = []
    skipped = 0
    with httpx.Client(
        timeout=args.timeout,
        follow_redirects=True,
        headers={"User-Agent": "legislink case registry verification"},
    ) as client:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = pool.map(lambda c: (c, *check(client, c, args.name_cutoff)), cases)
            for case, status, detail in results:
                if status == "SKIP":
                    skipped += 1
                    print(f"  SKIP   {case.id:<22} {detail}")
                elif status != "OK":
                    failures.append((case.id, status, detail))
                    print(f"  {status:<6} {case.id:<22} {detail}")

    checked = len(cases) - skipped
    print(f"\n{checked - len(failures)}/{checked} verified", end="")
    print(f", {len(failures)} failed" if failures else "", end="")
    print(f", {skipped} skipped" if skipped else "")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
