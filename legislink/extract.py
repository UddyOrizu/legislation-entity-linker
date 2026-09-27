"""Pass 1: find citations in text.

Extraction is deliberately resolution-free -- it reports *what the text says*,
including references it cannot pin down ("the Act"), and leaves matching
against the registry to :mod:`legislink.resolve`. Character offsets always
refer to the input string exactly as given, so callers can highlight or
rewrite the original document.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .models import Provision, WorkRef
from .normalize import normalize_alias
from . import patterns as P


@dataclass
class ProvisionGroup:
    """One or more provisions that form a single pinpoint, plus their span."""

    chain: tuple[Provision, ...]
    start: int
    end: int


@dataclass
class Mention:
    """A reference to a work, before resolution."""

    ref: WorkRef
    start: int
    end: int
    short_form: str | None = None  # short form *defined* at this mention


@dataclass
class RawCitation:
    """A mention optionally bound to a pinpoint."""

    mention: Mention | None
    group: ProvisionGroup | None
    start: int
    end: int

    @property
    def provisions(self) -> tuple[Provision, ...]:
        return self.group.chain if self.group else ()


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < e and s < end for s, e in spans)


# --------------------------------------------------------------------------
# Works
# --------------------------------------------------------------------------

def _work_type_from_match(m, title: str, year: int) -> tuple[str | None, str | None]:
    """Infer (work_type, number) from an explicit number in the citation."""
    if m.group("chapter"):
        return None, m.group("chapter")  # ukpga vs ukla decided at resolution
    if m.group("devolved_type"):
        return m.group("devolved_type").lower(), m.group("devolved_no")
    if m.group("si_no"):
        return "uksi", m.group("si_no")
    if m.group("ssi_no"):
        return "ssi", m.group("ssi_no")
    return None, None


def find_works(text: str) -> list[Mention]:
    """Find explicitly titled works, e.g. ``Theft Act 1968 (c. 60)``."""
    mentions: list[Mention] = []
    for m in P.WORK_RE.finditer(text):
        title = m.group("title")
        # Reject a title that is nothing but an article plus a kind noun
        # ("The Act"), which WORK_RE can otherwise produce.
        if len(title.split()) < 2 or title.split()[0].lower() in ("the", "this", "that", "a", "an"):
            if len(title.split()) < 3:
                continue
            title = " ".join(title.split()[1:])
        year = int(m.group("year"))
        work_type, number = _work_type_from_match(m, title, year)
        ref = WorkRef(
            surface=m.group(0),
            title=title,
            year=year,
            number=number,
            work_type=work_type,
        )
        mention = Mention(ref=ref, start=m.start(), end=m.end())

        # A short form defined immediately after: ("the DPA 2018").
        tail = text[m.end(): m.end() + 80]
        sf = P.SHORT_FORM_RE.match(tail)
        if sf:
            candidate = sf.group("short").strip()
            if _is_short_form(candidate):
                mention.short_form = candidate
        mentions.append(mention)
    return mentions


def _is_short_form(candidate: str) -> bool:
    """Is a parenthetical a short-form definition rather than a number?"""
    if not candidate or len(candidate) > 50:
        return False
    stripped = candidate.replace(".", "").replace(" ", "")
    if stripped.lower().startswith("c") and stripped[1:].isdigit():
        return False  # "(c. 60)"
    if "/" in candidate:
        return False  # "(SI 2017/692)"
    letters = [ch for ch in candidate if ch.isalpha()]
    if not letters:
        return False
    # An acronym, or a phrase ending in a kind noun ("the 2018 Act").
    has_acronym = any(
        len(w) >= 2 and w.isupper() for w in candidate.replace(".", "").split()
    )
    ends_in_kind = candidate.split()[-1].lower().rstrip("s") in (
        "act", "regulation", "rule", "order", "measure", "code", "scheme"
    )
    return has_acronym or ends_in_kind


def find_bare_instruments(text: str, taken: list[tuple[int, int]]) -> list[Mention]:
    """Find standalone instrument numbers, e.g. ``SI 2017/692``."""
    out: list[Mention] = []
    for m in P.BARE_SI_RE.finditer(text):
        if _overlaps(m.start(), m.end(), taken):
            continue
        code = m.group("code").replace(".", "").replace(" ", "").upper()
        work_type = {"SI": "uksi", "SSI": "ssi", "SR": "nisr"}.get(code, "uksi")
        out.append(
            Mention(
                ref=WorkRef(
                    surface=m.group(0),
                    title=None,
                    year=int(m.group("year")),
                    number=m.group("number"),
                    work_type=work_type,
                ),
                start=m.start(),
                end=m.end(),
            )
        )
    return out


def find_anaphors(
    text: str, taken: list[tuple[int, int]], short_forms: set[str]
) -> list[Mention]:
    """Find back-references: ``the 1998 Act``, ``the Regulations``, ``the DPA``.

    Acronym anaphors are only accepted when the acronym is a known short form
    -- defined in this document or registered in the registry -- otherwise
    every "the EU" in the document would become a citation.
    """
    out: list[Mention] = []
    known = {normalize_alias(s) for s in short_forms}

    for m in P.ANAPHOR_YEAR_RE.finditer(text):
        if _overlaps(m.start(), m.end(), taken):
            continue
        out.append(
            Mention(
                ref=WorkRef(
                    surface=m.group(0), year=int(m.group("year")), anaphoric=True
                ),
                start=m.start(),
                end=m.end(),
            )
        )

    spans = taken + [(x.start, x.end) for x in out]
    for m in P.ANAPHOR_SHORT_RE.finditer(text):
        if _overlaps(m.start(), m.end(), spans):
            continue
        short = m.group("short").strip()
        if normalize_alias(short) not in known:
            continue
        out.append(
            Mention(
                ref=WorkRef(surface=m.group(0), title=short, anaphoric=True),
                start=m.start(),
                end=m.end(),
            )
        )

    spans = taken + [(x.start, x.end) for x in out]
    for m in P.ANAPHOR_BARE_RE.finditer(text):
        if _overlaps(m.start(), m.end(), spans):
            continue
        out.append(
            Mention(
                ref=WorkRef(surface=m.group(0), anaphoric=True),
                start=m.start(),
                end=m.end(),
            )
        )

    return sorted(out, key=lambda x: x.start)


# --------------------------------------------------------------------------
# Provisions
# --------------------------------------------------------------------------

def _parse_blob(kind: str, blob: str) -> list[Provision]:
    """Turn ``"45(2), 46 to 48"`` into concrete :class:`Provision` objects."""
    provs: list[Provision] = []
    for m in P.ITEM_SPLIT_RE.finditer(blob):
        item = m.group("item")
        head = P.NUM_HEAD_RE.match(item)
        if not head:
            continue
        number = head.group(1).upper()
        subs = tuple(P.SUB_RE.findall(item))
        connector = (m.group("connector") or "").strip()
        if connector in P.RANGE_CONNECTORS and provs:
            provs[-1] = replace(provs[-1], end_number=number)
        else:
            provs.append(Provision(kind=kind, number=number, subdivisions=subs))
    return provs


def find_provisions(text: str, taken: list[tuple[int, int]]) -> list[ProvisionGroup]:
    """Find pinpoints, nesting ``paragraph 4 of Schedule 2`` into one group."""
    raw: list[tuple[str, list[Provision], int, int]] = []
    for m in P.PROVISION_RE.finditer(text):
        if _overlaps(m.start(), m.end(), taken):
            continue  # e.g. "Regulations 1998" inside a work title
        kind = P.canonical_kind(m.group("kind"))
        provs = _parse_blob(kind, m.group("blob"))
        if provs:
            raw.append((kind, provs, m.start(), m.end()))

    groups: list[ProvisionGroup] = []
    i = 0
    while i < len(raw):
        chain_parts = [raw[i]]
        j = i
        while j + 1 < len(raw):
            gap = text[raw[j][3]: raw[j + 1][2]]
            inner_rank = P.KIND_RANK.get(chain_parts[-1][0], 9)
            outer_rank = P.KIND_RANK.get(raw[j + 1][0], 9)
            if P.NESTING_JOIN_RE.match(gap) and outer_rank < inner_rank:
                chain_parts.append(raw[j + 1])
                j += 1
            else:
                break
        # Outermost container first: "para 4 of Sch 2" -> (Sch 2, para 4).
        ordered = list(reversed(chain_parts))
        chain = tuple(p for part in ordered for p in part[1])
        groups.append(
            ProvisionGroup(
                chain=chain,
                start=min(part[2] for part in chain_parts),
                end=max(part[3] for part in chain_parts),
            )
        )
        i = j + 1
    return groups


# --------------------------------------------------------------------------
# Binding
# --------------------------------------------------------------------------

def bind(
    text: str, mentions: list[Mention], groups: list[ProvisionGroup]
) -> list[RawCitation]:
    """Attach each pinpoint to the work it modifies.

    Forward binding ("section 6 of the Human Rights Act 1998") is preferred
    over backward ("Companies Act 2006, s 172") because the connective is
    explicit and therefore less ambiguous.
    """
    mentions = sorted(mentions, key=lambda m: m.start)
    groups = sorted(groups, key=lambda g: g.start)
    used_mentions: set[int] = set()
    citations: list[RawCitation] = []

    for group in groups:
        bound: int | None = None

        for idx, mention in enumerate(mentions):
            if idx in used_mentions or mention.start < group.end:
                continue
            if P.FORWARD_JOIN_RE.match(text[group.end: mention.start]):
                bound = idx
            break  # only the immediately following mention can bind forward

        if bound is None:
            for idx in range(len(mentions) - 1, -1, -1):
                mention = mentions[idx]
                if idx in used_mentions or mention.end > group.start:
                    continue
                gap = text[mention.end: group.start]
                # A citation and its pinpoint may wrap a line, but never span a
                # paragraph break.
                if gap.count("\n") <= 1 and P.BACKWARD_JOIN_RE.match(gap):
                    bound = idx
                break  # only the immediately preceding mention can bind back

        if bound is None:
            citations.append(RawCitation(None, group, group.start, group.end))
        else:
            used_mentions.add(bound)
            m = mentions[bound]
            citations.append(
                RawCitation(m, group, min(m.start, group.start), max(m.end, group.end))
            )

    for idx, mention in enumerate(mentions):
        if idx not in used_mentions:
            citations.append(RawCitation(mention, None, mention.start, mention.end))

    return sorted(citations, key=lambda c: (c.start, c.end))


def extract(
    text: str, known_short_forms: set[str] | None = None
) -> tuple[list[RawCitation], dict[str, Mention]]:
    """Run the full extraction pass.

    ``known_short_forms`` lets a caller supply acronyms that should be treated
    as citations even when the document never defines them -- the resolver
    passes its registry's aliases, which is how "under TUPE" is recognised.

    Returns the raw citations in document order and the short-form definitions
    discovered along the way (``"DPA 2018"`` -> the mention that defined it).
    """
    works = find_works(text)
    taken = [(m.start, m.end) for m in works]

    instruments = find_bare_instruments(text, taken)
    taken += [(m.start, m.end) for m in instruments]

    definitions = {m.short_form: m for m in works if m.short_form}
    vocabulary = set(definitions) | set(known_short_forms or ())
    anaphors = find_anaphors(text, taken, vocabulary)
    taken += [(m.start, m.end) for m in anaphors]

    groups = find_provisions(text, taken)
    mentions = works + instruments + anaphors
    return bind(text, mentions, groups), definitions
