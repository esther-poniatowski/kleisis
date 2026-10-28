"""Errors reported by schema-closure."""


class SchemaClosureError(Exception):
    """Base class for an invalid record, source, schema, or generated state."""


class RecordError(SchemaClosureError):
    """A resource record violates its declared structure."""


class SourceError(SchemaClosureError):
    """An exact dependency source cannot be obtained or verified."""


class RegistryError(SchemaClosureError):
    """A registry or schema declaration violates the registry contract."""


class ReferenceError(SchemaClosureError):
    """A schema reference cannot be materialized safely."""


class DriftError(SchemaClosureError):
    """Generated files differ from a fresh materialization."""
