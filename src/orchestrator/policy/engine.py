from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orchestrator.models import AccountStatus, OutreachChannel, PolicyDecisionOutcome

OUTREACH_WINDOW_START = time(hour=9)
OUTREACH_WINDOW_END = time(hour=20)


class PolicyInput(BaseModel):
    """Pure data required to evaluate outreach policy."""

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
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_at must be timezone-aware")
        return value


class PolicyResult(BaseModel):
    """Deterministic outreach policy decision."""

    model_config = ConfigDict(use_enum_values=False)

    decision: PolicyDecisionOutcome
    channel: OutreachChannel
    reasons: list[str]
    defer_until: datetime | None = None


def evaluate_policy(policy_input: PolicyInput) -> PolicyResult:
    """Evaluate deterministic outreach policy rules without side effects."""

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
    try:
        return ZoneInfo(customer_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid customer_timezone: {customer_timezone}") from exc
