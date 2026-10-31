"""Command-line entry point: Phase 1 scan (legacy), relate, and query."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from datalineague import scanner
from datalineague.calls import extract_calls
from datalineague.query import COMMANDS, QueryError, brief_mermaid, render_markdown, run_query
from datalineague.relations import dump_registry, relate_registry
from datalineague.sql_lineage import extract_sql

SUBCOMMANDS = {"relate", "query"}


def _relate(args: argparse.Namespace) -> int:
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    result = relate_registry(args.paths, registry, [extract_calls, extract_sql])
    output = args.output or args.registry
    output.write_text(dump_registry(result), encoding="utf-8")
    return 0


def _query(args: argparse.Namespace) -> int:
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    try:
        if args.command == "brief":
            if args.target is None:
                raise QueryError("The brief command requires a target.")
            sys.stdout.write(brief_mermaid(registry, args.target))
            return 0
        rows = run_query(registry, args.command, args.target, args.transitive)
    except QueryError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.format == "markdown":
        sys.stdout.write(render_markdown(rows))
    else:
        sys.stdout.write(json.dumps(rows, indent=2, sort_keys=True) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in SUBCOMMANDS:
        return scanner.main(arguments)
    parser = argparse.ArgumentParser(prog="datalineague", description="PL/SQL lineage relationships and queries.")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    relate = subparsers.add_parser("relate", help="Add CALLS/READS/WRITES/DERIVES_FROM relations to a Phase 1 registry.")
    relate.add_argument("paths", nargs="+", type=Path, help="PL/SQL source files or directories used for the registry")
    relate.add_argument("--registry", type=Path, required=True, help="Phase 1 registry JSON")
    relate.add_argument("-o", "--output", type=Path, help="Output path (default: update the registry in place)")
    relate.set_defaults(handler=_relate)

    query = subparsers.add_parser("query", help="Query the relationship index.")
    query.add_argument("registry", type=Path, help="Registry JSON containing relations")
    query.add_argument("command", choices=COMMANDS)
    query.add_argument("target", nargs="?", help="Routine name or object id")
    query.add_argument("--transitive", action="store_true", help="Follow resolved CALLS edges")
    query.add_argument("--format", choices=("json", "markdown"), default="json")
    query.set_defaults(handler=_query)

    args = parser.parse_args(arguments)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
