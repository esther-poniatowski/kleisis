"""Command-line interface for kleisis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath

from kleisis import __version__
from kleisis.application.service import SchemaClosureService
from kleisis.errors import SchemaClosureError
from kleisis.findings import FILESYSTEM_FAILED, Finding, complete
from kleisis.sarif import sarif_log

_REPORTING_COMMANDS = ("check", "validate")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kleisis",
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
    parser.add_argument(
        "--format",
        choices=("text", "sarif"),
        default="text",
        help="report of check and validate: text writes a line on standard error on failure; "
        "sarif also writes a SARIF 2.1.0 log on standard output",
    )
    return parser


def main(arguments: list[str] | None = None) -> None:
    """Run the selected operation and report a concise failure."""
    parser = _parser()
    options = parser.parse_args(arguments)
    if options.format == "sarif" and options.command not in _REPORTING_COMMANDS:
        parser.error(f"--format sarif applies to {' and '.join(_REPORTING_COMMANDS)} only")
    service = SchemaClosureService(options.record)
    findings: tuple[Finding, ...] = ()
    failure: Exception | None = None
    try:
        if options.command == "materialize":
            service.materialize(options.root)
        elif options.command == "check":
            service.check(options.root)
        else:
            service.validate(options.root)
    except SchemaClosureError as exc:
        print(f"kleisis: {exc}", file=sys.stderr)
        findings, failure = exc.findings, exc
    except OSError as exc:
        message = f"filesystem operation failed: {exc}"
        print(f"kleisis: {message}", file=sys.stderr)
        # An interrupted operation names no subject of its own, so it concerns the record.
        finding = Finding(rule=FILESYSTEM_FAILED, message=message)
        findings, failure = (complete(finding, options.record),), exc
    if options.format == "sarif":
        json.dump(sarif_log(findings, __version__), sys.stdout, indent=2)
        sys.stdout.write("\n")
    if failure is not None:
        raise SystemExit(1) from failure
