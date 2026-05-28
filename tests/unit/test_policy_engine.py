from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from orchestrator.models import AccountStatus, OutreachChannel, PolicyDecisionOutcome
from orchestrator.policy.engine import PolicyInput, evaluate_policy


def make_policy_input(**overrides: object) -> PolicyInput:
    data = {
        "channel": OutreachChannel.SMS,
        "scheduled_at": datetime(2026, 5, 28, 14, 0, tzinfo=ZoneInfo("UTC")),
        "customer_timezone": "America/New_York",
        "sms_consent": True,
        "call_consent": True,
        "email_consent": True,
        "opted_out": False,
        "account_status": AccountStatus.CURRENT,
        "recent_outbound_attempt_count": 0,
        "frequency_cap": 3,
    }
    data.update(overrides)
    return PolicyInput(**data)


@pytest.mark.parametrize(
    "channel",
    [OutreachChannel.CALL, OutreachChannel.SMS, OutreachChannel.EMAIL],
)
def test_opted_out_blocks_all_channels(channel: OutreachChannel) -> None:
    result = evaluate_policy(make_policy_input(channel=channel, opted_out=True))

    assert result.decision == PolicyDecisionOutcome.BLOCK
    assert result.channel == channel
    assert result.reasons == ["customer_opted_out"]
    assert result.defer_until is None


@pytest.mark.parametrize(
    ("channel", "consent_field", "expected_reason"),
    [
        (OutreachChannel.SMS, "sms_consent", "missing_sms_consent"),
        (OutreachChannel.CALL, "call_consent", "missing_call_consent"),
        (OutreachChannel.EMAIL, "email_consent", "missing_email_consent"),
    ],
)
def test_each_channel_requires_its_own_consent(
    channel: OutreachChannel, consent_field: str, expected_reason: str
) -> None:
    result = evaluate_policy(
        make_policy_input(channel=channel, **{consent_field: False})
    )

    assert result.decision == PolicyDecisionOutcome.BLOCK
    assert result.reasons == [expected_reason]


@pytest.mark.parametrize(
    ("account_status", "expected_reason"),
    [
        (AccountStatus.PAUSED, "account_paused"),
        (AccountStatus.RESOLVED, "account_resolved"),
    ],
)
def test_paused_and_resolved_accounts_block(
    account_status: AccountStatus, expected_reason: str
) -> None:
    result = evaluate_policy(make_policy_input(account_status=account_status))

    assert result.decision == PolicyDecisionOutcome.BLOCK
    assert result.reasons == [expected_reason]


@pytest.mark.parametrize("channel", [OutreachChannel.CALL, OutreachChannel.SMS])
def test_quiet_hours_defer_call_and_sms_before_0900_local(
    channel: OutreachChannel,
) -> None:
    scheduled_at = datetime(2026, 5, 28, 12, 30, tzinfo=ZoneInfo("UTC"))  # 08:30 NY

    result = evaluate_policy(
        make_policy_input(channel=channel, scheduled_at=scheduled_at)
    )

    assert result.decision == PolicyDecisionOutcome.DEFER
    assert result.reasons == ["quiet_hours"]
    assert result.defer_until == datetime(
        2026, 5, 28, 9, 0, tzinfo=ZoneInfo("America/New_York")
    )


@pytest.mark.parametrize("channel", [OutreachChannel.CALL, OutreachChannel.SMS])
def test_quiet_hours_defer_call_and_sms_at_or_after_2000_local(
    channel: OutreachChannel,
) -> None:
    scheduled_at = datetime(2026, 5, 29, 0, 0, tzinfo=ZoneInfo("UTC"))  # 20:00 NY

    result = evaluate_policy(
        make_policy_input(channel=channel, scheduled_at=scheduled_at)
    )

    assert result.decision == PolicyDecisionOutcome.DEFER
    assert result.reasons == ["quiet_hours"]
    assert result.defer_until == datetime(
        2026, 5, 29, 9, 0, tzinfo=ZoneInfo("America/New_York")
    )


def test_quiet_hours_do_not_apply_to_email() -> None:
    scheduled_at = datetime(2026, 5, 29, 3, 0, tzinfo=ZoneInfo("UTC"))  # 23:00 NY

    result = evaluate_policy(
        make_policy_input(channel=OutreachChannel.EMAIL, scheduled_at=scheduled_at)
    )

    assert result.decision == PolicyDecisionOutcome.ALLOW
    assert result.reasons == ["policy_allowed"]
    assert result.defer_until is None


@pytest.mark.parametrize("channel", [OutreachChannel.CALL, OutreachChannel.SMS])
def test_inside_outreach_window_allows_when_other_rules_pass(
    channel: OutreachChannel,
) -> None:
    scheduled_at = datetime(2026, 5, 28, 13, 0, tzinfo=ZoneInfo("UTC"))  # 09:00 NY

    result = evaluate_policy(
        make_policy_input(channel=channel, scheduled_at=scheduled_at)
    )

    assert result.decision == PolicyDecisionOutcome.ALLOW
    assert result.reasons == ["policy_allowed"]
    assert result.defer_until is None


def test_frequency_cap_defers() -> None:
    result = evaluate_policy(
        make_policy_input(recent_outbound_attempt_count=3, frequency_cap=3)
    )

    assert result.decision == PolicyDecisionOutcome.DEFER
    assert result.reasons == ["frequency_cap_exceeded"]
    assert result.defer_until is None


def test_combined_block_decision_includes_multiple_block_reasons() -> None:
    result = evaluate_policy(
        make_policy_input(
            channel=OutreachChannel.SMS,
            opted_out=True,
            sms_consent=False,
            account_status=AccountStatus.PAUSED,
        )
    )

    assert result.decision == PolicyDecisionOutcome.BLOCK
    assert result.reasons == [
        "customer_opted_out",
        "missing_sms_consent",
        "account_paused",
    ]


def test_block_precedence_over_defer() -> None:
    scheduled_at = datetime(2026, 5, 29, 3, 0, tzinfo=ZoneInfo("UTC"))  # 23:00 NY

    result = evaluate_policy(
        make_policy_input(
            scheduled_at=scheduled_at,
            opted_out=True,
            recent_outbound_attempt_count=3,
            frequency_cap=3,
        )
    )

    assert result.decision == PolicyDecisionOutcome.BLOCK
    assert result.reasons == ["customer_opted_out"]
    assert result.defer_until is None


def test_block_precedence_skips_defer_validation() -> None:
    result = evaluate_policy(
        make_policy_input(opted_out=True, customer_timezone="Not/A_Zone")
    )

    assert result.decision == PolicyDecisionOutcome.BLOCK
    assert result.reasons == ["customer_opted_out"]


def test_invalid_timezone_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Invalid customer_timezone"):
        evaluate_policy(make_policy_input(customer_timezone="Not/A_Zone"))
