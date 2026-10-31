"""Command-line interface for kleisis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from kleisis import __version__
from kleisis.application.service import SchemaClosureService
from kleisis.errors import DriftError, SchemaClosureError
from kleisis.findings import Finding
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
        "sarif also writes a SARIF 2.1.0 log on standard output or at --output",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="with --format sarif, write the log to this path instead of standard output; "
        "missing parent directories are created",
    )
    return parser


def _stop_reason(failure: SchemaClosureError) -> str | None:
    """Return why the analysis stopped before its end, or ``None`` when it ran to its end.

    ``check`` raises a drift error only after comparing every output, so its run is complete.
    Every other failure stops the build at its first failure.
    """
    if isinstance(failure, DriftError):
        return None
    rules = ", ".join(sorted({finding.rule.id for finding in failure.findings}))
    return f"the build stopped at its first failure ({rules}); later stages were not evaluated"


def _write_log(log: dict[str, Any], output: Path | None) -> None:
    """Write ``log`` to ``output``, or to standard output when ``output`` is ``None``."""
    text = json.dumps(log, indent=2) + "\n"
    if output is None:
        sys.stdout.write(text)
        return
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"kleisis: cannot write the SARIF log: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def main(arguments: list[str] | None = None) -> None:
    """Run the selected operation and report a concise failure."""
    parser = _parser()
    options = parser.parse_args(arguments)
    if options.format == "sarif" and options.command not in _REPORTING_COMMANDS:
        parser.error(f"--format sarif applies to {' and '.join(_REPORTING_COMMANDS)} only")
    if options.output is not None and options.format != "sarif":
        parser.error("--output requires --format sarif")
    service = SchemaClosureService(options.record)
    findings: tuple[Finding, ...] = ()
    failure: Exception | None = None
    stopped: str | None = None
    try:
        if options.command == "materialize":
            service.materialize(options.root)
        elif options.command == "check":
            service.check(options.root)
        else:
            service.validate(options.root)
    except SchemaClosureError as exc:
        print(f"kleisis: {exc}", file=sys.stderr)
        findings, failure, stopped = exc.findings, exc, _stop_reason(exc)
    except OSError as exc:
        message = f"filesystem operation failed: {exc}"
        print(f"kleisis: {message}", file=sys.stderr)
        # A crash is no defect of the analysed files: the run reports it as its failure, and
        # holds the results detected before it, none under the first-failure rule.
        failure, stopped = exc, message
    if options.format == "sarif":
        _write_log(sarif_log(findings, __version__, stopped), options.output)
    if failure is not None:
        raise SystemExit(1) from failure
