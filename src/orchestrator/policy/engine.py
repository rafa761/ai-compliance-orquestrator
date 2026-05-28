from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orchestrator.models import AccountStatus, OutreachChannel, PolicyDecisionOutcome

OUTREACH_WINDOW_START = time(hour=9)
OUTREACH_WINDOW_END = time(hour=20)


class PolicyInput(BaseModel):
    """Pure policy input with all mutable state resolved before evaluation.

    The policy engine deliberately receives values, not ORM objects, so it stays
    deterministic and side-effect free. Callers must decide which snapshot of
    consent, opt-out state, account status, and recent-attempt count is current.
    """

    model_config = ConfigDict(use_enum_values=False)

    channel: OutreachChannel
    scheduled_at: datetime
    customer_timezone: str
    sms_consent: bool
    call_consent: bool
    email_consent: bool
    opted_out: bool
    account_status: AccountStatus
    recent_outbound_attempt_count: int = Field(default=0, ge=0)
    frequency_cap: int = Field(default=3, ge=0)

    @field_validator("scheduled_at")
    @classmethod
    def scheduled_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        """Require aware datetimes before conversion to the customer's local zone."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_at must be timezone-aware")
        return value


class PolicyResult(BaseModel):
    """Deterministic decision for one proposed outreach attempt.

    BLOCK means no task may be created. DEFER may schedule later only when
    `defer_until` is present. ALLOW means the caller can schedule at the
    proposed time.
    """

    model_config = ConfigDict(use_enum_values=False)

    decision: PolicyDecisionOutcome
    channel: OutreachChannel
    reasons: list[str]
    defer_until: datetime | None = None


def evaluate_policy(policy_input: PolicyInput) -> PolicyResult:
    """Evaluate rules in compliance-first order and return auditable reasons.

    Hard blocks win over deferrals, and deferrals win over allow. The reason
    strings are intentionally stable because they appear in policy decisions and
    audit rows used to explain the demo.
    """

    block_reasons: list[str] = []
    defer_reasons: list[str] = []
    defer_until: datetime | None = None

    block_reasons.extend(_opt_out_block_reasons(policy_input))
    block_reasons.extend(_channel_consent_block_reasons(policy_input))
    block_reasons.extend(_account_status_block_reasons(policy_input))

    if block_reasons:
        return PolicyResult(
            decision=PolicyDecisionOutcome.BLOCK,
            channel=policy_input.channel,
            reasons=block_reasons,
        )

    quiet_hours_defer_until = _quiet_hours_defer_until(policy_input)
    if quiet_hours_defer_until is not None:
        defer_reasons.append("quiet_hours")
        defer_until = quiet_hours_defer_until

    if policy_input.recent_outbound_attempt_count >= policy_input.frequency_cap:
        defer_reasons.append("frequency_cap_exceeded")

    if defer_reasons:
        return PolicyResult(
            decision=PolicyDecisionOutcome.DEFER,
            channel=policy_input.channel,
            reasons=defer_reasons,
            defer_until=defer_until,
        )

    return PolicyResult(
        decision=PolicyDecisionOutcome.ALLOW,
        channel=policy_input.channel,
        reasons=["policy_allowed"],
    )


def _opt_out_block_reasons(policy_input: PolicyInput) -> list[str]:
    if policy_input.opted_out:
        return ["customer_opted_out"]
    return []


def _channel_consent_block_reasons(policy_input: PolicyInput) -> list[str]:
    if policy_input.channel == OutreachChannel.SMS and not policy_input.sms_consent:
        return ["missing_sms_consent"]
    if policy_input.channel == OutreachChannel.CALL and not policy_input.call_consent:
        return ["missing_call_consent"]
    if policy_input.channel == OutreachChannel.EMAIL and not policy_input.email_consent:
        return ["missing_email_consent"]
    return []


def _account_status_block_reasons(policy_input: PolicyInput) -> list[str]:
    if policy_input.account_status == AccountStatus.PAUSED:
        return ["account_paused"]
    if policy_input.account_status == AccountStatus.RESOLVED:
        return ["account_resolved"]
    return []


def _quiet_hours_defer_until(policy_input: PolicyInput) -> datetime | None:
    """Return the next allowed local send time for call/SMS quiet-hour violations.

    Email is exempt in this demo. Calls and SMS are allowed from 09:00 inclusive
    to 20:00 exclusive in the customer's timezone; outside that window they move
    to the next local opening.
    """

    if policy_input.channel == OutreachChannel.EMAIL:
        return None

    customer_timezone = _get_customer_timezone(policy_input.customer_timezone)
    local_scheduled_at = policy_input.scheduled_at.astimezone(customer_timezone)
    local_scheduled_time = local_scheduled_at.time()

    if OUTREACH_WINDOW_START <= local_scheduled_time < OUTREACH_WINDOW_END:
        return None

    if local_scheduled_time < OUTREACH_WINDOW_START:
        return local_scheduled_at.replace(
            hour=OUTREACH_WINDOW_START.hour,
            minute=OUTREACH_WINDOW_START.minute,
            second=0,
            microsecond=0,
        )

    next_day = local_scheduled_at + timedelta(days=1)
    return next_day.replace(
        hour=OUTREACH_WINDOW_START.hour,
        minute=OUTREACH_WINDOW_START.minute,
        second=0,
        microsecond=0,
    )


def _get_customer_timezone(customer_timezone: str) -> ZoneInfo:
    """Resolve a customer timezone even when policy is called outside the API.

    The public event API validates this first, but tests and internal callers can
    still reach the engine directly, so invalid zones must fail here as well.
    """

    try:
        return ZoneInfo(customer_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid customer_timezone: {customer_timezone}") from exc
