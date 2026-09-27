"""Turn a resolved work plus a pinpoint into legislation.gov.uk URLs.

Two URL shapes matter:

``url``
    A browsable page, e.g. ``.../ukpga/1998/42/section/6``. Subsection depth is
    expressed as a fragment because the section page is the addressable page.
``id_uri``
    The abstract identifier under ``/id/``, which does address subsection
    depth as path segments -- the right thing to store in a triple or a
    database key.

Only schedules genuinely nest in legislation.gov.uk paths; a section is
addressed directly even when the text cited it via its Part. :func:`targets`
encodes that.
"""

from __future__ import annotations

from urllib.parse import quote_plus

from .models import BASE, CONTAINER_KINDS, CanonicalWork, Provision, Version
from .patterns import KIND_RANK


def targets(chain: tuple[Provision, ...]) -> list[tuple[list[str], Provision]]:
    """Path segments for each thing the pinpoint actually addresses.

    A list such as "ss 45 and 46" yields one target per sibling; a nested
    reference such as "paragraph 4 of Schedule 2" yields a single, deeper one.

    Only Schedules contribute a path prefix. A Part or Chapter is dropped when
    something more specific is cited, because legislation.gov.uk addresses
    sections directly and redirects ``/schedule/1/part/I/paragraph/1`` to
    ``/schedule/1/paragraph/1``.
    """
    if not chain:
        return []

    schedules = [p for p in chain if p.kind == "schedule"]
    leaves = [p for p in chain if p.kind not in CONTAINER_KINDS]

    if leaves:
        # Keep only the most specific kind: "section 6 of Part 2" is /section/6.
        best = max(KIND_RANK.get(p.kind, 9) for p in leaves)
        leaves = [p for p in leaves if KIND_RANK.get(p.kind, 9) == best]
    elif schedules:
        # "Part 2 of Schedule 1" is addressable only down to the Schedule.
        leaves, schedules = schedules[-1:], schedules[:-1]
    else:
        # Only Parts and/or Chapters: address the innermost one.
        best = max(KIND_RANK.get(p.kind, 9) for p in chain)
        leaves = [p for p in chain if KIND_RANK.get(p.kind, 9) == best]

    prefix = [seg for p in schedules for seg in p.path]
    return [(prefix + list(leaf.path), leaf) for leaf in leaves]


def search_url(title: str | None, year: int | None = None) -> str:
    """Fallback link into legislation.gov.uk search for an unresolved work."""
    params = []
    if title:
        params.append(f"title={quote_plus(title)}")
    if year:
        params.append(f"year={year}")
    return f"{BASE}/all" + ("?" + "&".join(params) if params else "")


def build(
    work: CanonicalWork | None,
    chain: tuple[Provision, ...] = (),
    title: str | None = None,
    year: int | None = None,
    version: Version | None = None,
) -> tuple[str | None, str | None, list[str]]:
    """Return ``(url, id_uri, all_target_urls)`` for a citation.

    With no resolved work, falls back to a search URL and no id URI -- a link
    the user can follow, honestly distinguished from a resolved one.
    """
    if work is None:
        return search_url(title, year), None, []

    version = version or Version()
    suffix = version.suffix

    paths = targets(chain)
    if not paths:
        return work.url + suffix, work.id_uri, [work.url + suffix]

    urls: list[str] = []
    id_uris: list[str] = []
    for segments, leaf in paths:
        url = work.url + "/" + "/".join(segments) + suffix
        if leaf.subdivisions:
            url += "#" + "-".join(segments + list(leaf.subdivisions))
        urls.append(url)
        id_uris.append(work.id_uri + "/" + "/".join(segments + list(leaf.subdivisions)))

    return urls[0], id_uris[0], urls
