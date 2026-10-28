"""Command-line interface for schema-closure."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path, PurePosixPath

from schema_closure.application.service import SchemaClosureService
from schema_closure.errors import SchemaClosureError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schema-closure",
        description="Materialize exact JSON Schema dependencies and deterministic registries.",
    )
    parser.add_argument(
        "command",
        choices=("materialize", "check", "validate"),
        help="operation to perform",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="consumer repository root")
    parser.add_argument(
        "--record",
        type=PurePosixPath,
        default=PurePosixPath("specs/schema-resources.yml"),
        help="resource record path relative to the repository root",
    )
    return parser


def main(arguments: list[str] | None = None) -> None:
    """Run the selected operation and report a concise failure."""
    options = _parser().parse_args(arguments)
    service = SchemaClosureService(options.record)
    try:
        if options.command == "materialize":
            service.materialize(options.root)
        elif options.command == "check":
            service.check(options.root)
        else:
            service.validate(options.root)
    except SchemaClosureError as exc:
        print(f"schema-closure: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except OSError as exc:
        print(f"schema-closure: filesystem operation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
