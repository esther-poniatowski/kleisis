"""A validator of producer logs and an identity builder, written from the findings profile.

The rules follow sections 3, 4 and 5 of the findings profile of Elenchos, version 1
(``docs/profile.md``). This module imports nothing from kleisis, so that it checks the output
of kleisis against the text of the profile and not against the serializer that wrote it. The
vectors in ``fixtures/elenchos`` are copies of ``specs/vectors`` of Elenchos at commit
``69fc2c1da09fd17c32121ab79c0fbb02ecd53fcb``.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any
from urllib.parse import unquote

SCHEMA = (
    "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
)
LEVELS = {"error", "warning", "note"}
COLUMN_KINDS = {"unicodeCodePoints", "utf16CodeUnits"}
TOOL_NAME = re.compile(r"[a-z][a-z0-9-]*")
RULE_ID = re.compile(r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*")
LOCUS_TYPES = {"link", "task", "slot", "term", "symbol"}
VERSION_COMPONENT = re.compile(r"v(?:0|[1-9][0-9]*)")
DISCRIMINATOR = "discriminator/v1"
GATE_ONLY = ("fingerprints", "suppressions")
ABSENT = ("kind", "baselineState")


def _is_versioned_key(key: object) -> bool:
    """Section 4.6: a versioned hierarchical string with non-empty components."""
    if not isinstance(key, str):
        return False
    components = key.split("/")
    return all(components) and VERSION_COMPONENT.fullmatch(components[-1]) is not None


def split_name(name: str) -> tuple[str, str]:
    """Section 4.5: split a logical name into its decoded resource and its locus.

    Raise ``ValueError`` for a name that the profile rejects.
    """
    resource, separator, locus = name.partition("#")
    if separator and not locus:
        raise ValueError(f"name ends with '#': {name!r}")
    if re.search(r"%(?!25|23)", resource):
        raise ValueError(f"resource holds a '%' other than %25 or %23: {name!r}")
    decoded = resource.replace("%23", "#").replace("%25", "%")
    return decoded, locus


def resource_violations(resource: str) -> list[str]:
    """Section 4.5: the forms ``id:<identifier>`` and ``path:<path>``."""
    if resource.startswith("id:"):
        return [] if len(resource) > 3 else [f"empty identifier: {resource!r}"]
    if not resource.startswith("path:"):
        return [f"resource has no prefix id: or path:: {resource!r}"]
    path = resource[len("path:") :]
    if path == "./":
        return []
    if not path or path.startswith("/") or path.startswith("./"):
        return [f"path is empty or opens on / or ./: {resource!r}"]
    segments = path.removesuffix("/").split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        return [f"path holds an empty, . or .. segment: {resource!r}"]
    return []


def locus_violations(locus: str) -> list[str]:
    """Section 4.5: the empty locus, a JSON Pointer, or a typed locus of the closed list."""
    if locus == "":
        return []
    if locus.startswith("/"):
        if re.search(r"~(?![01])", locus):
            return [f"pointer holds '~' not followed by 0 or 1: {locus!r}"]
        return []
    kind, separator, value = locus.partition(":")
    if not separator or kind not in LOCUS_TYPES or not value:
        return [f"locus is neither a pointer nor a typed locus: {locus!r}"]
    return []


def _result_violations(result: dict[str, Any], driver: str, rule_ids: set[str] | None) -> list[str]:
    problems: list[str] = []
    rule_id = result.get("ruleId")
    if not isinstance(rule_id, str) or not RULE_ID.fullmatch(rule_id):
        problems.append(f"ruleId is absent or malformed: {rule_id!r}")
    elif rule_ids is not None and rule_id not in rule_ids:
        problems.append(f"ruleId has no reporting descriptor: {rule_id}")
    for key in ABSENT:
        if key in result:
            problems.append(f"result holds {key}, absent from version 1")
    for key in GATE_ONLY:
        if key in result:
            problems.append(f"result holds {key}, written by the gate only")
    if result.get("level") not in LEVELS:
        problems.append(f"level is absent or invalid: {result.get('level')!r}")
    message = result.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("text"), str):
        problems.append("message.text is absent")
    elif not message["text"]:
        problems.append("message.text is empty")
    locations = result.get("locations")
    if not isinstance(locations, list) or len(locations) != 1:
        return [*problems, "locations does not hold exactly one location"]
    location = locations[0]
    logical = location.get("logicalLocations")
    if not isinstance(logical, list) or len(logical) != 1:
        return [*problems, "logicalLocations does not hold exactly one location"]
    name = logical[0].get("name")
    if not isinstance(name, str):
        return [*problems, "logical location has no name"]
    if "parentIndex" in logical[0]:
        problems.append("logical location is not top-level")
    if logical[0].get("fullyQualifiedName", name) != name:
        problems.append("fullyQualifiedName differs from name")
    try:
        resource, locus = split_name(name)
    except ValueError as exc:
        return [*problems, str(exc)]
    problems += resource_violations(resource) + locus_violations(locus)
    physical = location.get("physicalLocation")
    if resource.startswith("path:"):
        if not isinstance(physical, dict):
            problems.append("physicalLocation is absent for a path resource")
        else:
            problems += _physical_violations(physical, resource[len("path:") :])
    fingerprints = result.get("partialFingerprints", {})
    if not isinstance(fingerprints, dict):
        problems.append("partialFingerprints is not an object")
    else:
        for key, value in fingerprints.items():
            if not _is_versioned_key(key):
                problems.append(f"partialFingerprints key is not versioned: {key!r}")
            elif key != DISCRIMINATOR:
                problems.append(f"producer writes a partial fingerprint other than {DISCRIMINATOR}")
            if not isinstance(value, str) or not value:
                problems.append(f"partialFingerprints value is not a non-empty string: {value!r}")
    for key in result.get("properties", {}):
        if not key.startswith(f"{driver}/"):
            problems.append(f"property is not prefixed by the driver name: {key!r}")
    return problems


def _physical_violations(physical: dict[str, Any], path: str) -> list[str]:
    """Section 4.4: a relative URI under SRCROOT, percent-encoding the resource path."""
    artifact = physical.get("artifactLocation")
    if not isinstance(artifact, dict) or not isinstance(artifact.get("uri"), str):
        return ["physicalLocation has no artifactLocation.uri"]
    problems: list[str] = []
    uri = artifact["uri"]
    if re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", uri) or uri.startswith("/"):
        problems.append(f"artifact URI is not relative: {uri!r}")
    if artifact.get("uriBaseId") != "SRCROOT":
        problems.append("artifact URI has no uriBaseId SRCROOT")
    if unquote(uri) != path:
        problems.append(f"artifact URI {uri!r} does not encode the resource path {path!r}")
    region = physical.get("region")
    if region is not None:
        if path.endswith("/"):
            problems.append("a folder has a region")
        for key in ("startLine", "startColumn"):
            if key in region and (type(region[key]) is not int or region[key] < 1):
                problems.append(f"region.{key} is not a positive integer")
    return problems


def run_violations(run: dict[str, Any]) -> list[str]:
    """Sections 3 and 4: the violations of one run of a producer."""
    problems: list[str] = []
    driver = run.get("tool", {}).get("driver", {})
    name = driver.get("name")
    if not isinstance(name, str) or not TOOL_NAME.fullmatch(name):
        problems.append(f"tool.driver.name is absent or malformed: {name!r}")
        name = ""
    if not any(isinstance(driver.get(key), str) for key in ("version", "semanticVersion")):
        problems.append("tool.driver has neither version nor semanticVersion")
    rule_ids: set[str] | None = None
    if "rules" in driver:
        rule_ids = set()
        for rule in driver["rules"]:
            rule_ids.add(rule.get("id"))
            if not isinstance(rule.get("id"), str) or not RULE_ID.fullmatch(rule["id"]):
                problems.append(f"rule id is malformed: {rule.get('id')!r}")
            if not isinstance(rule.get("shortDescription", {}).get("text"), str):
                problems.append(f"rule {rule.get('id')} has no shortDescription")
            if rule.get("defaultConfiguration", {}).get("level") not in LEVELS:
                problems.append(f"rule {rule.get('id')} has no default level")
    results = run.get("results", [])
    if results and run.get("columnKind") not in COLUMN_KINDS:
        problems.append("columnKind is absent although results is non-empty")
    for index, result in enumerate(results):
        problems += [
            f"result {index}: {problem}" for problem in _result_violations(result, name, rule_ids)
        ]
    return problems


def log_violations(log: dict[str, Any]) -> list[str]:
    """Section 3: the violations of a log, each run checked as the run of a producer."""
    problems: list[str] = []
    if log.get("version") != "2.1.0":
        problems.append("version is not 2.1.0")
    if "$schema" in log and log["$schema"] != SCHEMA:
        problems.append("$schema is not the schema of errata 01")
    runs = log.get("runs")
    if not isinstance(runs, list) or not runs:
        return [*problems, "runs is absent or empty"]
    for index, run in enumerate(runs):
        problems += [f"run {index}: {problem}" for problem in run_violations(run)]
    return problems


def _escape(component: str) -> str:
    """Section 5.2: NFC, then escaping of %, | and control characters."""
    text = unicodedata.normalize("NFC", component)
    return "".join(
        f"%{ord(char):02X}" if char in "%|" or ord(char) < 0x20 or ord(char) == 0x7F else char
        for char in text
    )


def identities(log: dict[str, Any]) -> list[str]:
    """Section 5: the identity of each result, in the order of runs and results."""
    entries: list[tuple[list[str], tuple[int, int, str, int]]] = []
    for run in log["runs"]:
        tool = run["tool"]["driver"]["name"]
        for result in run.get("results", []):
            location = result["locations"][0]
            resource, locus = split_name(location["logicalLocations"][0]["name"])
            discriminator = result.get("partialFingerprints", {}).get(DISCRIMINATOR, "")
            components = [_escape(part) for part in (tool, result["ruleId"], resource, locus)]
            components.append(_escape(discriminator))
            while components[-1] == "":
                components.pop()
            region = location.get("physicalLocation", {}).get("region", {})
            order = (
                region.get("startLine", 0),
                region.get("startColumn", 0),
                result["message"]["text"],
                len(entries),
            )
            entries.append((components, order))
    groups: dict[str, list[int]] = defaultdict(list)
    for index, (components, _) in enumerate(entries):
        groups["|".join(components)].append(index)
    result_identities = ["" for _ in entries]
    for base, members in groups.items():
        for ordinal, index in enumerate(sorted(members, key=lambda item: entries[item][1]), 1):
            if ordinal == 1:
                result_identities[index] = base
            else:
                padded = entries[index][0] + [""] * (5 - len(entries[index][0]))
                result_identities[index] = "|".join([*padded, str(ordinal)])
    return result_identities
