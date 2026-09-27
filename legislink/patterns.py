"""The UK citation grammar.

Everything regex-shaped lives here so the extraction logic in
:mod:`legislink.extract` stays readable. The grammar covers the forms that
actually turn up in UK judgments, statutes and legal drafting:

    Human Rights Act 1998                     full title
    Theft Act 1968 (c. 60)                    with chapter number
    Land Reform (Scotland) Act 2003 (asp 2)   devolved, parenthesised title
    section 6 of the Human Rights Act 1998    forward binding
    Companies Act 2006, ss 170-177            backward binding, range
    s 45(2)(a)                                pinpoint with subdivisions
    para 4 of Schedule 2                      nested provisions
    SI 2017/692                               bare instrument number
    the 1998 Act / the Act / the DPA          anaphora and short forms
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Work titles
# --------------------------------------------------------------------------

# Lowercase words permitted *inside* a title. A bare "the" is deliberately
# excluded: it almost never stands alone mid-title, and allowing it lets the
# title match run backwards across a sentence ("...v Jones the Human Rights
# Act 1998"). It is allowed only *after* another connective, which is how it
# actually occurs -- "Offences against the Person Act 1861".
_CONNECTIVE = r"(?:of|and|for|to|in|on|at|or|with|under|upon|by|from|against|into|over)"
_CONNECTOR = rf"(?:{_CONNECTIVE}\s+the|{_CONNECTIVE}|etc\.?)"

# A capitalised word, or a parenthesised qualifier such as "(Scotland)" or
# "(Consolidation)" or "(Rights of Third Parties)". A trailing comma is allowed
# because plenty of real titles contain one -- "Legal Aid, Sentencing and
# Punishment of Offenders Act 2012", "Copyright, Designs and Patents Act 1988".
_TITLE_TOKEN = r"(?:[A-Z][A-Za-z'’\-\.]*,?|\([^()]{1,70}\),?)"

_KIND_NOUN = r"(?:Acts?|Measure|Regulations|Rules|Order|Scheme|Code)"

_YEAR = r"(?:1[0-9]{3}|20[0-9]{2})"

# Non-greedy so the shortest title ending in a kind noun before the year wins;
# bounded repetition keeps a pathological line from running away.
WORK_RE = re.compile(
    rf"""
    \b(?P<title>
        {_TITLE_TOKEN}
        (?:\s+(?:{_TITLE_TOKEN}|{_CONNECTOR})){{0,14}}?
        \s+{_KIND_NOUN}
    )
    \s+(?P<year>{_YEAR})
    (?:                                   # optional official number
        \s*\(\s*
        (?:
            c\.?\s*(?P<chapter>\d+)
          | (?P<devolved_type>asp|anaw|asc|nia)\s*(?P<devolved_no>\d+)
          | S\.?\s*I\.?\s*(?P<si_year>{_YEAR})\s*/\s*(?P<si_no>\d+)
          | S\.?\s*S\.?\s*I\.?\s*(?P<ssi_year>{_YEAR})\s*/\s*(?P<ssi_no>\d+)
        )
        \s*\)
    )?
    """,
    re.VERBOSE,
)

# A bare instrument number: "SI 2017/692", "S.I. 2017/692", "SSI 2015/41".
BARE_SI_RE = re.compile(
    rf"\b(?P<code>S\.?\s?I\.?|S\.?\s?S\.?\s?I\.?|S\.?\s?R\.?)\s*(?P<year>{_YEAR})\s*/\s*(?P<number>\d+)\b"
)

# Short-form definition immediately following a work mention:
#   Data Protection Act 2018 ("the DPA 2018")
#   Human Rights Act 1998 ('the HRA')
SHORT_FORM_RE = re.compile(
    r"""^\s*\(\s*["'“‘]?\s*(?:the\s+)?
        (?P<short>[A-Za-z0-9][A-Za-z0-9\s'’\.\-]{0,48}?)
        \s*["'”’]?\s*\)""",
    re.VERBOSE,
)

# --------------------------------------------------------------------------
# Anaphora
# --------------------------------------------------------------------------

# "the 1998 Act", "that 2006 Act"
ANAPHOR_YEAR_RE = re.compile(
    rf"\b(?:the|that|this|said)\s+(?P<year>{_YEAR})\s+(?P<noun>Act|Regulations|Order|Rules|Measure)\b",
    re.IGNORECASE,
)

# "the Act", "the said Regulations". Sentence-initial "The Act" must match too.
ANAPHOR_BARE_RE = re.compile(
    r"\b(?:the|that|this)\s+(?:said\s+)?(?P<noun>Act|Regulations|Order|Rules|Measure)\b",
    re.IGNORECASE,
)

# "the DPA", "the HRA 1998", "the CPR", or a bare "TUPE" / "under COSHH".
# The determiner is optional and case-insensitive; the acronym itself is not,
# or every "the act" in the document would look like a short form. Callers
# filter matches against a set of known short forms, so a bare acronym is only
# ever accepted when it was defined in the document or lives in the registry.
ANAPHOR_SHORT_RE = re.compile(
    rf"\b(?:[Tt]h(?:e|at|is)\s+)?(?P<short>[A-Z][A-Z0-9]{{1,9}}(?:\s+{_YEAR})?)\b"
)

# --------------------------------------------------------------------------
# Provisions
# --------------------------------------------------------------------------

# Canonical kind for each abbreviation the grammar accepts.
_KIND_ALIASES: dict[str, str] = {
    "section": "section", "sections": "section", "sec": "section", "secs": "section",
    "s": "section", "ss": "section",
    "subsection": "section", "subsections": "section", "sub-section": "section",
    "schedule": "schedule", "schedules": "schedule", "sched": "schedule",
    "scheds": "schedule", "sch": "schedule", "schs": "schedule",
    "part": "part", "parts": "part", "pt": "part", "pts": "part",
    "chapter": "chapter", "chapters": "chapter", "ch": "chapter", "chs": "chapter",
    # A bare "r" for regulation is deliberately omitted: it fires constantly on
    # ordinary prose and the abbreviated forms below cover real usage.
    "regulation": "regulation", "regulations": "regulation", "reg": "regulation",
    "regs": "regulation",
    "article": "article", "articles": "article", "art": "article", "arts": "article",
    "rule": "rule", "rules": "rule",
    "paragraph": "paragraph", "paragraphs": "paragraph", "para": "paragraph",
    "paras": "paragraph", "sub-paragraph": "paragraph", "subparagraph": "paragraph",
}

# Longest-first so "sections" is not shadowed by "section", "ss" not by "s".
_KIND_ALT = "|".join(sorted((re.escape(k) for k in _KIND_ALIASES), key=len, reverse=True))

# A provision number: 45, 2A, 45ZA, 12B.
_NUM = r"\d+[A-Z]{0,3}"

# Bracketed levels below the number: (2)(a)(i).
_SUBS = r"(?:\s*\(\s*[0-9A-Za-z]{1,4}\s*\))*"

_ITEM = rf"{_NUM}{_SUBS}"

# kind + one or more items joined by list/range connectors.
PROVISION_RE = re.compile(
    rf"""
    \b(?P<kind>{_KIND_ALT})\s*\.?\s*
    (?P<blob>
        {_ITEM}
        (?:\s*(?:,\s*|\s+and\s+|\s+to\s+|\s*[-–—]\s*)\s*{_ITEM})*
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Splits the matched blob back into individual items, keeping the connector so
# we can tell "sections 45 to 48" (a range) from "sections 45 and 48" (a list).
ITEM_SPLIT_RE = re.compile(
    rf"(?P<connector>,\s*|\s+and\s+|\s+to\s+|\s*[-–—]\s*)?(?P<item>{_NUM}{_SUBS})",
    re.VERBOSE | re.IGNORECASE,
)

#: Connectors that make the preceding item the start of a range.
RANGE_CONNECTORS = ("to", "-", "–", "—")

SUB_RE = re.compile(r"\(\s*([0-9A-Za-z]{1,4})\s*\)")
NUM_HEAD_RE = re.compile(rf"^({_NUM})", re.IGNORECASE)

#: How specific each provision kind is. A smaller rank is an outer container,
#: so "paragraph 4 of Schedule 2" nests paragraph (4) inside schedule (0).
KIND_RANK = {
    "schedule": 0, "part": 1, "chapter": 2,
    "section": 3, "regulation": 3, "article": 3, "rule": 3,
    "paragraph": 4,
}

# Text allowed between a provision and the work it attaches to. "to" is here
# for "Schedule 2 to the Companies Act 2006", which is the standard UK form.
FORWARD_JOIN_RE = re.compile(r"^\s*(?:of|in|to|under)\s+(?:the\s+)?$", re.IGNORECASE)
BACKWARD_JOIN_RE = re.compile(r"^\s*(?:,|—|-)?\s*(?:at\s+)?$", re.IGNORECASE)
NESTING_JOIN_RE = re.compile(r"^\s*(?:of|to|in)\s+(?:the\s+)?$", re.IGNORECASE)


def canonical_kind(raw: str) -> str:
    """Map a matched kind token to its canonical name."""
    return _KIND_ALIASES[raw.lower().rstrip(".")]
