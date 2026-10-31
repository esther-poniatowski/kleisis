"""Write findings as a log of the findings profile of Elenchos, a subset of SARIF 2.1.0.

The log holds one run of the driver ``kleisis``, whose driver lists every rule of the catalogue.
Each result names its rule, its level and its message, the file concerned as a physical location
relative to ``SRCROOT``, and one logical location ``<resource>#<locus>``. Kleisis knows no line
or column of a failure, so no result holds a region, and a physical location denotes the whole
file or folder. The gate builds the identity of each result and writes its suppressions; a
producer writes neither.

The run holds one invocation, which states whether the analysis ran to its end. A run that
stopped, at the first failure of the build or on a crash, declares a failed invocation with one
notification of level ``error`` stating why it stopped.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

from kleisis.findings import RULES, Finding

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = (
    "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
)
TOOL_NAME = "kleisis"
URI_BASE_ID = "SRCROOT"
DISCRIMINATOR_KEY = "discriminator/v1"
LEVEL = "error"
"""Every failure of kleisis blocks the command, so every rule and result has level error."""

# The characters of an RFC 3986 path segment kept as written, besides unreserved characters:
# the sub-delimiters and ``@``. A colon is encoded, so that a first segment never reads as a
# scheme.
_URI_SAFE = "/!$&'()*+,;=@"


def resource_path(finding: Finding) -> str:
    """Return the repository-relative POSIX path of the subject; a folder ends with ``/``."""
    if finding.subject is None:
        raise ValueError(f"finding has no subject: {finding.message}")
    path = finding.subject.as_posix()
    if finding.folder:
        return "./" if path == "." else f"{path}/"
    return path


def logical_name(finding: Finding) -> str:
    """Return ``path:<path>``, followed by ``#<locus>`` when the locus is not empty.

    In the resource, ``%`` is written ``%25`` and ``#`` is written ``%23``, so that the name
    splits at its first ``#``; the locus is written as is.
    """
    resource = "path:" + resource_path(finding).replace("%", "%25").replace("#", "%23")
    return f"{resource}#{finding.locus}" if finding.locus else resource


def artifact_uri(finding: Finding) -> str:
    """Return the subject path percent-encoded as a relative URI reference."""
    return quote(resource_path(finding), safe=_URI_SAFE)


def _result(finding: Finding) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ruleId": finding.rule.id,
        "level": LEVEL,
        "message": {"text": finding.message},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": artifact_uri(finding), "uriBaseId": URI_BASE_ID}
                },
                "logicalLocations": [{"name": logical_name(finding)}],
            }
        ],
    }
    if finding.discriminator:
        result["partialFingerprints"] = {DISCRIMINATOR_KEY: finding.discriminator}
    return result


def invocation(stopped: str | None) -> dict[str, Any]:
    """Return the invocation of a run, failed with the reason ``stopped`` when it is given."""
    if stopped is None:
        return {"executionSuccessful": True}
    return {
        "executionSuccessful": False,
        "toolExecutionNotifications": [{"level": "error", "message": {"text": stopped}}],
    }


def sarif_log(
    findings: Sequence[Finding], version: str, stopped: str | None = None
) -> dict[str, Any]:
    """Return the log of one run of kleisis of ``version``, with one result per finding.

    ``stopped`` states why the analysis stopped before its end, and is ``None`` for a complete
    run. The analysis of kleisis begins with the command, so ``results`` is always written,
    empty when the run detected nothing before it stopped.
    """
    rules = [
        {
            "id": rule.id,
            "shortDescription": {"text": rule.description},
            "defaultConfiguration": {"level": LEVEL},
        }
        for rule in RULES
    ]
    return {
        "version": SARIF_VERSION,
        "$schema": SARIF_SCHEMA,
        "runs": [
            {
                "tool": {"driver": {"name": TOOL_NAME, "version": version, "rules": rules}},
                "invocations": [invocation(stopped)],
                "columnKind": "unicodeCodePoints",
                "results": [_result(finding) for finding in findings],
            }
        ],
    }
