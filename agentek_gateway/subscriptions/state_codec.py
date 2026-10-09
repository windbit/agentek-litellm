import json
from collections.abc import Mapping

from .model import (
    SignalSource,
    StateReason,
    StateRecord,
    SubscriptionState,
)


def encode_record(record: StateRecord) -> str:
    return json.dumps(
        {
            "state": record.state.value,
            "version": record.version,
            "entered_at": record.entered_at,
            "until": record.until,
            "reason": record.reason.value,
            "source": record.source.value,
            "overload_streak": record.overload_streak,
        }
    )


def decode_record(raw: str) -> StateRecord:
    fields = json.loads(raw)
    return record_from_fields(fields)


def record_from_fields(fields: Mapping[str, object]) -> StateRecord:
    until = fields.get("until")
    return StateRecord(
        state=SubscriptionState(str(fields["state"])),
        version=int(str(fields["version"])),
        entered_at=float(str(fields["entered_at"])),
        until=None if until is None else float(str(until)),
        reason=StateReason(str(fields["reason"])),
        source=SignalSource(str(fields["source"])),
        overload_streak=int(str(fields["overload_streak"])),
    )
