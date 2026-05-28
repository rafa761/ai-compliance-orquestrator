from hashlib import sha256


def inbound_event_idempotency_key(*, source: str, external_id: str) -> str:
    """Derive the internal replay key from source event identity.

    Hashing avoids ambiguous separators and keeps long upstream IDs out of the
    unique index while preserving the `source` + `external_id` business boundary.
    """

    digest = sha256(f"{source}\x1f{external_id}".encode()).hexdigest()
    return f"inbound_event:{digest}"
