from hashlib import sha256


def inbound_event_idempotency_key(*, source: str, external_id: str) -> str:
    """Derive a stable internal idempotency key from source event identity."""
    digest = sha256(f"{source}\x1f{external_id}".encode()).hexdigest()
    return f"inbound_event:{digest}"
