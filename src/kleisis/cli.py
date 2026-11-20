"""Command-line interface for kleisis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from hodegos import DiscoveryError, Tool, discover

from kleisis import __version__
from kleisis.application.service import SchemaClosureService
from kleisis.errors import DriftError, SchemaClosureError
from kleisis.findings import Finding
from kleisis.sarif import sarif_log

_REPORTING_COMMANDS = ("check", "validate")

TOOL = Tool("kleisis", ("yml",))
"""Convention of Kleisis for Hodegos: the file `kleisis.yml`, and the variable `KLEISIS_ROOT`.

The places of a folder are `kleisis.yml`, `.config/kleisis.yml` and `config/kleisis.yml`, as the
specification of Hodegos states:
https://github.com/esther-poniatowski/hodegos/blob/main/docs/specification.md#the-places-of-a-folder
The table of `pyproject.toml` is no place of the configuration, since a consumer of Kleisis is a
repository of schemas, rarely a Python project. A configuration in another folder is named by
`--config`.
"""


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
    parser.add_argument(
        "-v", "--version", action="version", version=__version__, help="print the version and exit"
    )
    parser.add_argument(
        "-r",
        "--root",
        type=Path,
        help=f"consumer repository root; by default ${TOOL.variable}, else the nearest folder "
        "holding the configuration, else the current folder",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        help="configuration file, read from the current folder and lying below the root; by "
        "default kleisis.yml at the root, in .config/, or in config/",
    )
    parser.add_argument(
        "--format",
        choices=("text", "sarif"),
        default="text",
        help="report of check and validate: text writes a line on standard error on failure; "
        "sarif also writes a SARIF 2.1.0 log on standard output or at --output",
    )
    parser.add_argument(
        "-o",
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


def _locate(
    parser: argparse.ArgumentParser, root: Path | None, config: Path | None
) -> tuple[Path, PurePosixPath]:
    """Return the root and the path of the configuration relative to it, found through Hodegos.

    The root comes from ``--root``, from ``KLEISIS_ROOT``, from the nearest folder holding the
    configuration, or from the current folder. The configuration is the file of ``--config``, or
    the single place of the root holding `kleisis.yml`. A root that is not a folder, a missing
    configuration, and a configuration outside the root, which names the outputs of the root, are
    usage errors.
    """
    try:
        found = discover(TOOL, root=root, config=config)
    except DiscoveryError as exc:
        parser.error(str(exc))
    if not found.root.is_dir():
        parser.error(f"the root {found.root} is not a folder")
    resolved_root = found.root.resolve()
    if found.configuration is None:
        parser.error(
            f"no configuration below {resolved_root}: write kleisis.yml at the root, in .config/, "
            "or in config/, or name it with --config"
        )
    resolved = found.configuration.path.resolve()
    if not resolved.is_relative_to(resolved_root):
        parser.error(f"--config {config} lies outside the root {resolved_root}")
    return resolved_root, PurePosixPath(resolved.relative_to(resolved_root).as_posix())


def main(arguments: list[str] | None = None) -> None:
    """Run the selected operation and report a concise failure."""
    parser = _parser()
    options = parser.parse_args(arguments)
    if options.format == "sarif" and options.command not in _REPORTING_COMMANDS:
        parser.error(f"--format sarif applies to {' and '.join(_REPORTING_COMMANDS)} only")
    if options.output is not None and options.format != "sarif":
        parser.error("--output requires --format sarif")
    root, configuration = _locate(parser, options.root, options.config)
    service = SchemaClosureService(configuration)
    findings: tuple[Finding, ...] = ()
    failure: Exception | None = None
    stopped: str | None = None
    try:
        if options.command == "materialize":
            service.materialize(root)
        elif options.command == "check":
            service.check(root)
        else:
            service.validate(root)
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
        status = failure.exit_status if isinstance(failure, SchemaClosureError) else 1
        raise SystemExit(status) from failure
