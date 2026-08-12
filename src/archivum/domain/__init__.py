"""Domain primitives: identity generation, title rules, domain errors.

No IO, no framework imports — this module must stay importable anywhere.
"""

import re
import uuid

MAX_TITLE_LENGTH = 255

# Forbid path separators and control characters; titles are display identity,
# never storage paths, but keeping them path-safe avoids a class of client bugs.
_TITLE_FORBIDDEN = re.compile(r"[/\\\x00-\x1f\x7f]")


def new_id() -> uuid.UUID:
    """Mint a permanent identity: UUIDv7 (time-ordered for index locality)."""
    return uuid.uuid7()


class DomainError(Exception):
    """Base for all repository domain rule violations."""


class EntryNotFound(DomainError):
    pass


class NotAFolder(DomainError):
    pass


class TitleConflict(DomainError):
    pass


class CycleDetected(DomainError):
    pass


class InvalidTitle(DomainError):
    pass


class InvalidOperation(DomainError):
    pass


class NotADocument(DomainError):
    pass


class VersionNotFound(DomainError):
    pass


class SchemaNotFound(DomainError):
    pass


class DuplicateSchemaName(DomainError):
    pass


class SchemaStateError(DomainError):
    """Operation not allowed in the schema's current lifecycle state
    (draft/active/retired — ADR-0009)."""


class SchemaAssignmentError(DomainError):
    pass


class FieldNotFound(DomainError):
    pass


class DuplicateFieldKey(DomainError):
    pass


class InvalidFieldKey(DomainError):
    pass


class InvalidMetadataValue(DomainError):
    pass


class MetadataNotAssigned(DomainError):
    pass


class VersionConflict(DomainError):
    """Optimistic-concurrency failure: the document's current version is no
    longer the one the writer last saw (ADR-0007)."""

    def __init__(self, expected: int, actual: int):
        super().__init__(f"expected version {expected}, but current version is {actual}")
        self.expected = expected
        self.actual = actual


class RevisionConflict(DomainError):
    """Aggregate optimistic-concurrency failure: the entry's revision is no
    longer the one the writer last saw (ADR-0010; maps to HTTP 412)."""

    def __init__(self, expected: int | None, actual: int):
        super().__init__(f"expected revision {expected}, but current revision is {actual}")
        self.expected = expected
        self.actual = actual


def validate_title(title: str) -> str:
    if not title or not title.strip():
        raise InvalidTitle("title must not be empty")
    if len(title) > MAX_TITLE_LENGTH:
        raise InvalidTitle(f"title exceeds {MAX_TITLE_LENGTH} characters")
    if title in (".", ".."):
        raise InvalidTitle("title must not be '.' or '..'")
    if _TITLE_FORBIDDEN.search(title):
        raise InvalidTitle("title must not contain path separators or control characters")
    return title
