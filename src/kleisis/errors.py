"""Errors reported by kleisis."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import PurePath
from typing import ClassVar

from kleisis.findings import (
    DRIFT_RESOURCES,
    RECORD_INVALID,
    REFERENCE_UNRESOLVED,
    REGISTRY_INVALID,
    SOURCE_UNAVAILABLE,
    Finding,
    Rule,
)


class SchemaClosureError(Exception):
    """Base class for an invalid record, source, schema, or generated state.

    The message reports the failure to a reader. The findings report the same failure, as one
    finding or as one finding per listed item, each with its rule and its subject. A raise site
    that names no rule takes the default rule of its class.
    """

    default_rule: ClassVar[Rule] = REGISTRY_INVALID
    exit_status: ClassVar[int] = 1
    """The exit status of a command that this failure stops."""

    def __init__(
        self,
        message: str,
        *,
        rule: Rule | None = None,
        subject: PurePath | None = None,
        locus: str = "",
        discriminator: str = "",
        folder: bool = False,
        findings: Sequence[Finding] = (),
    ) -> None:
        super().__init__(message)
        self.findings: tuple[Finding, ...] = tuple(findings) or (
            Finding(
                rule=rule or self.default_rule,
                message=message,
                subject=subject,
                locus=locus,
                discriminator=discriminator,
                folder=folder,
            ),
        )

    def locate(self, subject: PurePath, locus: str = "", discriminator: str = "") -> None:
        """Assign a subject, a locus and a discriminator to each finding that names no subject."""
        self.findings = tuple(
            replace(
                finding,
                subject=subject,
                locus=finding.locus or locus,
                discriminator=finding.discriminator or discriminator,
            )
            if finding.subject is None
            else finding
            for finding in self.findings
        )

    def map_findings(self, function: Callable[[Finding], Finding]) -> None:
        """Replace each finding by its image under ``function``."""
        self.findings = tuple(function(finding) for finding in self.findings)


class RecordError(SchemaClosureError):
    """A resource record violates its declared structure."""

    default_rule = RECORD_INVALID


class RecordFormatError(RecordError):
    """A resource record declares no format, or a format other than the one Kleisis reads.

    The record is the configuration of the command, so the command stops with status 2.
    """

    exit_status = 2


class SourceError(SchemaClosureError):
    """An exact dependency source cannot be obtained or verified."""

    default_rule = SOURCE_UNAVAILABLE


class RegistryError(SchemaClosureError):
    """A registry or schema declaration violates the registry contract."""


class ReferenceError(SchemaClosureError):
    """A schema reference cannot be materialized safely."""

    default_rule = REFERENCE_UNRESOLVED


class DriftError(SchemaClosureError):
    """Generated files differ from a fresh materialization.

    The check raises it only after comparing every output, so it ends a complete analysis.
    """

    default_rule = DRIFT_RESOURCES
