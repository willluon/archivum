"""parse_value type-system tests — pure unit, no database."""

import datetime
from decimal import Decimal

import pytest

from archivum.domain import InvalidMetadataValue
from archivum.metadata import parse_value


def test_text():
    assert parse_value("text", "10 Example Street") == "10 Example Street"
    with pytest.raises(InvalidMetadataValue):
        parse_value("text", "   ")


def test_integer():
    assert parse_value("integer", "42") == 42
    assert parse_value("integer", -7) == -7
    for bad in ("3.5", "abc", "", True):
        with pytest.raises(InvalidMetadataValue):
            parse_value("integer", bad)


def test_decimal():
    assert parse_value("decimal", "25000.00") == Decimal("25000.00")
    for bad in ("banana", "", "NaN", "Infinity", True):
        with pytest.raises(InvalidMetadataValue):
            parse_value("decimal", bad)


def test_boolean():
    assert parse_value("boolean", "true") is True
    assert parse_value("boolean", "FALSE") is False
    for bad in ("yes", "1", "on", ""):
        with pytest.raises(InvalidMetadataValue):
            parse_value("boolean", bad)


def test_date():
    assert parse_value("date", "2026-08-12") == datetime.date(2026, 8, 12)
    for bad in ("2026-13-40", "08/12/2026", "banana"):
        with pytest.raises(InvalidMetadataValue):
            parse_value("date", bad)


def test_datetime_requires_timezone():
    parsed = parse_value("datetime", "2026-08-12T14:30:00+00:00")
    assert parsed.tzinfo is not None
    with pytest.raises(InvalidMetadataValue):
        parse_value("datetime", "2026-08-12T14:30:00")  # naive


def test_none_and_unknown_type_rejected():
    with pytest.raises(InvalidMetadataValue):
        parse_value("text", None)
    with pytest.raises(InvalidMetadataValue):
        parse_value("choice", "x")
