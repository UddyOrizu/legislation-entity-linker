#!/usr/bin/env python3
"""Check every registry entry against live legislation.gov.uk.

The registry maps titles to official numbers, and a wrong number produces a
confidently wrong link -- the worst failure this tool can have. This script is
the ground truth for that mapping.

    python scripts/verify_registry.py                 # check the seed registry
    python scripts/verify_registry.py extra.json      # check your own file
    python scripts/verify_registry.py --concurrency 4 # be gentler

Exits non-zero if any entry fails, so it can gate a release.
"""

from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from legislink.normalize import normalize_title, similarity  # noqa: E402
from legislink.registry import Registry  # noqa: E402

TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)


def check(client, work, title_cutoff: float) -> tuple[str, str]:
    """Return (status, detail) for one work: OK, TITLE, HTTP or ERROR."""
    try:
        response = client.get(work.url)
    except Exception as exc:
        return "ERROR", f"{type(exc).__name__}"

    if response.status_code != 200:
        return "HTTP", f"{response.status_code} {work.url}"

    match = TITLE_RE.search(response.text)
    if not match:
        return "OK", ""

    page_title = re.sub(r"\s+", " ", match.group(1)).strip()
    score = similarity(normalize_title(work.title), normalize_title(page_title))
    if score < title_cutoff:
        return "TITLE", f"registry={work.title!r} site={page_title!r} ({score:.2f})"
    return "OK", ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="registry JSON files (default: the seed)")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=40.0)
    parser.add_argument("--title-cutoff", type=float, default=0.75)
    args = parser.parse_args()

    try:
        import httpx
    except ImportError:
        print("this script needs httpx: pip install legislink[remote]", file=sys.stderr)
        return 2

    registry = Registry() if args.files else Registry.default()
    for path in args.files:
        registry.load_file(path)

    works = sorted(registry, key=lambda w: (w.year, w.title))
    print(f"checking {len(works)} works against legislation.gov.uk...\n")

    failures: list[tuple[str, str, str]] = []
    with httpx.Client(
        timeout=args.timeout,
        follow_redirects=True,
        headers={"User-Agent": "legislink registry verification"},
    ) as client:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = pool.map(lambda w: (w, *check(client, w, args.title_cutoff)), works)
            for work, status, detail in results:
                if status != "OK":
                    failures.append((work.id, status, detail))
                    print(f"  {status:<6} {work.id:<18} {detail}")

    checked = len(works)
    print(f"\n{checked - len(failures)}/{checked} verified", end="")
    print(f", {len(failures)} failed" if failures else "")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
