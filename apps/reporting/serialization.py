"""af-json-v1 deterministic snapshot serialization (spec 18.2).

Only JSON-compatible values are accepted: dict (str keys), list, str, int,
bool and None. Dates, datetimes, UUIDs and decimals must already be strings
(use ``iso_utc``/``iso_date``). Floats are rejected so no non-finite or
platform-dependent number formatting can enter the hash.
"""
import hashlib
import json
from datetime import date, datetime, timezone

SERIALIZER_VERSION = "af-json-v1"
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class SnapshotTypeError(TypeError):
    pass


def iso_utc(value: datetime | None):
    if value is None:
        return None
    if value.tzinfo is None:
        raise SnapshotTypeError("Naive datetimes are not allowed in snapshots.")
    return value.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


def iso_date(value: date | None):
    return value.isoformat() if value is not None else None


def validate(value, path="$"):
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            validate(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise SnapshotTypeError(f"{path}: non-string key {key!r}")
            validate(item, f"{path}.{key}")
        return
    raise SnapshotTypeError(f"{path}: unsupported type {type(value).__name__}")


def snapshot_bytes(data: dict) -> bytes:
    """Serialize already-normalised af-json-v1 snapshot data."""
    validate(data)
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def snapshot_sha256(data: dict) -> str:
    return hashlib.sha256(snapshot_bytes(data)).hexdigest()
