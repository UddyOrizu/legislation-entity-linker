"""Command line interface.

    legislink resolve "section 6 of the Human Rights Act 1998"
    legislink resolve --file judgment.txt --format table
    legislink annotate judgment.txt --html > judgment.html
    legislink index corpus/ -o index.json
    legislink works --search "data protection"
    legislink serve --port 8000
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .models import DocumentResult, Version
from .normalize import FUZZY_BACKEND
from .pipeline import annotate, build_index, resolve_text
from .registry import Registry
from .resolve import Resolver
from . import __version__


def _build_resolver(args: argparse.Namespace) -> Resolver:
    registry = Registry.default()
    for path in getattr(args, "registry", None) or []:
        registry.load_file(path)

    version = Version()
    if getattr(args, "original", False):
        version = Version(kind="original")
    elif getattr(args, "as_at", None):
        version = Version(kind="point-in-time", date=args.as_at)
    elif getattr(args, "prospective", False):
        version = Version(kind="latest")

    remote = None
    if getattr(args, "remote", False):
        from .remote import LegislationGovUk

        remote = LegislationGovUk()

    return Resolver(
        registry,
        accept=getattr(args, "accept", 0.86),
        suggest=getattr(args, "suggest", 0.60),
        remote=remote,
        default_version=version,
    )


def _read_input(args: argparse.Namespace) -> str:
    """Resolve the text to process from --file, positionals, or stdin.

    A positional argument that names an existing file is read as one, so
    ``legislink annotate judgment.txt`` does the obvious thing rather than
    annotating the literal string "judgment.txt".
    """
    if args.file:
        return Path(args.file).read_text(encoding="utf-8", errors="replace")
    if args.text:
        paths = [Path(t) for t in args.text]
        if all(p.is_file() for p in paths):
            return "\n\n".join(
                p.read_text(encoding="utf-8", errors="replace") for p in paths
            )
        return " ".join(args.text)
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("nothing to read: pass text, a file path, --file, or pipe stdin")


def _print_table(result: DocumentResult) -> None:
    rows = [("CONF", "METHOD", "CITATION", "RESOLVED AS", "URL")]
    for c in result.citations:
        rows.append(
            (
                f"{c.confidence:.2f}",
                c.method,
                " ".join(c.surface.split())[:44],
                (c.work.citation if c.work else "—")[:46],
                c.url or "—",
            )
        )
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    for i, row in enumerate(rows):
        line = "  ".join(row[j].ljust(widths[j]) for j in range(4)) + "  " + row[4]
        print(line)
        if i == 0:
            print("  ".join("-" * w for w in widths) + "  " + "-" * 3)
    s = result.to_dict()["stats"]
    print(
        f"\n{s['citations']} citations, {s['resolved']} resolved, "
        f"{s['distinct_works']} distinct works"
    )


def cmd_resolve(args: argparse.Namespace) -> int:
    result = resolve_text(_read_input(args), _build_resolver(args))
    if args.format == "table":
        _print_table(result)
    else:
        json.dump(result.to_dict(include_text=args.include_text), sys.stdout, indent=2)
        sys.stdout.write("\n")
    return 0


def cmd_annotate(args: argparse.Namespace) -> int:
    result = resolve_text(_read_input(args), _build_resolver(args))
    sys.stdout.write(
        annotate(
            result,
            fmt="html" if args.html else "markdown",
            min_confidence=args.min_confidence,
        )
    )
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    index = build_index(args.paths, _build_resolver(args))
    payload = json.dumps(index, indent=2)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
        s = index["stats"]
        print(
            f"indexed {s['documents']} documents, {s['resolved_citations']}/"
            f"{s['total_citations']} citations resolved across "
            f"{s['distinct_works']} works -> {args.output}"
        )
    else:
        print(payload)
    return 0


def cmd_works(args: argparse.Namespace) -> int:
    registry = Registry.default()
    for path in args.registry or []:
        registry.load_file(path)

    if args.search:
        hits = registry.search(args.search, args.year, limit=args.limit, cutoff=0.3)
        for work, score in hits:
            print(f"{score:.2f}  {work.id:<18}  {work.citation}")
            print(f"{'':6}{'':18}  {work.url}")
    else:
        works = sorted(registry, key=lambda w: (w.year, w.title))
        if args.year:
            works = [w for w in works if w.year == args.year]
        for work in works[: args.limit]:
            print(f"{work.id:<18}  {work.citation}")
        print(f"\n{len(registry)} works in registry (fuzzy backend: {FUZZY_BACKEND})")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        raise SystemExit("serving needs the 'api' extra: pip install legislink[api]")
    from .api import create_app

    uvicorn.run(create_app(_build_resolver(args)), host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="legislink",
        description="Extract, resolve and link UK legislation citations.",
    )
    parser.add_argument("--version", action="version", version=f"legislink {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_resolver_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("-r", "--registry", action="append", metavar="JSON",
                       help="extra registry file (repeatable)")
        p.add_argument("--as-at", metavar="YYYY-MM-DD",
                       help="link to the point-in-time version at this date")
        p.add_argument("--original", action="store_true",
                       help="link to the original enacted version")
        p.add_argument("--prospective", action="store_true",
                       help="request the latest available view")
        p.add_argument("--accept", type=float, default=0.86,
                       help="fuzzy score at which a match counts as resolved")
        p.add_argument("--suggest", type=float, default=0.60,
                       help="fuzzy score at which a near-miss is reported")
        p.add_argument("--remote", action="store_true",
                       help="fall back to legislation.gov.uk for unknown titles")

    def add_input_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("text", nargs="*", help="text to process, or a file path")
        p.add_argument("-f", "--file", help="read the text from a file")

    p_resolve = sub.add_parser("resolve", help="resolve citations in text")
    add_input_flags(p_resolve)
    add_resolver_flags(p_resolve)
    p_resolve.add_argument("--format", choices=("json", "table"), default="table")
    p_resolve.add_argument("--include-text", action="store_true",
                           help="echo the source text in the JSON output")
    p_resolve.set_defaults(func=cmd_resolve)

    p_annotate = sub.add_parser("annotate", help="rewrite text with citation links")
    add_input_flags(p_annotate)
    add_resolver_flags(p_annotate)
    p_annotate.add_argument("--html", action="store_true", help="emit HTML instead of markdown")
    p_annotate.add_argument("--min-confidence", type=float, default=0.0,
                            help="leave citations below this confidence unlinked")
    p_annotate.set_defaults(func=cmd_annotate)

    p_index = sub.add_parser("index", help="build a citation index over a corpus")
    p_index.add_argument("paths", nargs="+", help="files or directories")
    p_index.add_argument("-o", "--output", help="write JSON here instead of stdout")
    add_resolver_flags(p_index)
    p_index.set_defaults(func=cmd_index)

    p_works = sub.add_parser("works", help="inspect the registry")
    p_works.add_argument("--search", metavar="TITLE", help="fuzzy-search titles")
    p_works.add_argument("--year", type=int)
    p_works.add_argument("--limit", type=int, default=20)
    p_works.add_argument("-r", "--registry", action="append", metavar="JSON")
    p_works.set_defaults(func=cmd_works)

    p_serve = sub.add_parser("serve", help="run the REST API")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    add_resolver_flags(p_serve)
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
