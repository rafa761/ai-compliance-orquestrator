from __future__ import annotations

# The demo script is intended to run directly from the repository root with
# `uv run python scripts/seed_demo.py`, so it adds `src/` before importing the app.
# ruff: noqa: E402, I001

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine

from orchestrator.models import (
    Account,
    AuditEvent,
    Customer,
    InboundEvent,
    OutreachTask,
    PolicyDecision,
)
from orchestrator.settings import get_settings

RESET_MODELS = [
    AuditEvent,
    OutreachTask,
    PolicyDecision,
    InboundEvent,
    Account,
    Customer,
]


class DemoScenario(NamedTuple):
    name: str
    description: str
    events: list[dict[str, Any]]


def api_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def demo_scenarios() -> list[DemoScenario]:
    start_at = _demo_start_at()
    payment_start_at = start_at + timedelta(hours=1)
    return [
        DemoScenario(
            name="happy_path_delinquent_account",
            description="Delinquent account with all channel consents schedules email, SMS, and call outreach.",
            events=[
                _account_delinquent_event(
                    external_id="evt_demo_happy_path_delinquent",
                    customer_external_id="cust_demo_jane_doe",
                    account_external_id="acct_demo_jane_doe",
                    full_name="Jane Doe",
                    email="jane.doe@example.com",
                    phone_number="+14155550100",
                    occurred_at=start_at,
                    balance_cents=12500,
                    days_past_due=14,
                )
            ],
        ),
        DemoScenario(
            name="opt_out_blocks_future_outreach",
            description="Opt-out event records durable opt-out state; a later delinquency event is blocked by policy.",
            events=[
                _opt_out_event(
                    external_id="evt_demo_opt_out_received",
                    customer_external_id="cust_demo_sam_taylor",
                    account_external_id="acct_demo_sam_taylor",
                    occurred_at=start_at + timedelta(minutes=10),
                ),
                _account_delinquent_event(
                    external_id="evt_demo_opt_out_followup_delinquent",
                    customer_external_id="cust_demo_sam_taylor",
                    account_external_id="acct_demo_sam_taylor",
                    full_name="Sam Taylor",
                    email="sam.taylor@example.com",
                    phone_number="+12125550100",
                    occurred_at=start_at + timedelta(minutes=20),
                    balance_cents=5400,
                    days_past_due=7,
                ),
            ],
        ),
        DemoScenario(
            name="payment_received_cancels_scheduled_outreach",
            description="A delinquency event schedules outreach; a payment event for the same account cancels the pending work.",
            events=[
                _account_delinquent_event(
                    external_id="evt_demo_payment_before_delinquent",
                    customer_external_id="cust_demo_maria_santos",
                    account_external_id="acct_demo_maria_santos",
                    full_name="Maria Santos",
                    email="maria.santos@example.com",
                    phone_number="+16465550100",
                    occurred_at=payment_start_at,
                    balance_cents=12500,
                    days_past_due=21,
                ),
                _payment_received_event(
                    external_id="evt_demo_payment_received",
                    customer_external_id="cust_demo_maria_santos",
                    account_external_id="acct_demo_maria_santos",
                    occurred_at=payment_start_at + timedelta(minutes=15),
                ),
            ],
        ),
    ]


def _demo_start_at() -> datetime:
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    return tomorrow.replace(hour=15, minute=0, second=0, microsecond=0)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _customer(
    *,
    external_id: str,
    full_name: str,
    email: str,
    phone_number: str,
    sms_consent: bool = True,
    call_consent: bool = True,
    email_consent: bool = True,
) -> dict[str, Any]:
    return {
        "external_id": external_id,
        "full_name": full_name,
        "timezone": "America/New_York",
        "phone_number": phone_number,
        "email": email,
        "sms_consent": sms_consent,
        "call_consent": call_consent,
        "email_consent": email_consent,
    }


def _account_delinquent_event(
    *,
    external_id: str,
    customer_external_id: str,
    account_external_id: str,
    full_name: str,
    email: str,
    phone_number: str,
    occurred_at: datetime,
    balance_cents: int,
    days_past_due: int,
) -> dict[str, Any]:
    return {
        "source": "demo_script",
        "external_id": external_id,
        "event_type": "account_delinquent",
        "customer": _customer(
            external_id=customer_external_id,
            full_name=full_name,
            email=email,
            phone_number=phone_number,
        ),
        "account": {
            "external_id": account_external_id,
            "status": "delinquent",
            "balance_cents": balance_cents,
            "days_past_due": days_past_due,
        },
        "occurred_at": _iso(occurred_at),
        "metadata": {"demo_scenario": True},
    }


def _opt_out_event(
    *,
    external_id: str,
    customer_external_id: str,
    account_external_id: str,
    occurred_at: datetime,
) -> dict[str, Any]:
    return {
        "source": "demo_script",
        "external_id": external_id,
        "event_type": "opt_out_received",
        "customer": _customer(
            external_id=customer_external_id,
            full_name="Sam Taylor",
            email="sam.taylor@example.com",
            phone_number="+12125550100",
            sms_consent=False,
            call_consent=False,
            email_consent=False,
        ),
        "account": {
            "external_id": account_external_id,
            "status": "delinquent",
            "balance_cents": 5400,
            "days_past_due": 7,
        },
        "occurred_at": _iso(occurred_at),
        "metadata": {
            "demo_scenario": True,
            "opt_out": {"channel": "sms", "message": "STOP"},
        },
    }


def _payment_received_event(
    *,
    external_id: str,
    customer_external_id: str,
    account_external_id: str,
    occurred_at: datetime,
) -> dict[str, Any]:
    return {
        "source": "demo_script",
        "external_id": external_id,
        "event_type": "payment_received",
        "customer": _customer(
            external_id=customer_external_id,
            full_name="Maria Santos",
            email="maria.santos@example.com",
            phone_number="+16465550100",
        ),
        "account": {
            "external_id": account_external_id,
            "status": "resolved",
            "balance_cents": 0,
            "days_past_due": 0,
        },
        "occurred_at": _iso(occurred_at),
        "metadata": {
            "demo_scenario": True,
            "payment": {"amount_cents": 12500, "provider_reference": "pay_demo_001"},
        },
    }


async def reset_database(database_url: str) -> None:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            for model in RESET_MODELS:
                await connection.execute(delete(model))
    finally:
        await engine.dispose()


class DemoApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def get_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        url = api_url(self.base_url, path)
        if params:
            url = f"{url}?{urlencode(params)}"
        return self._request("GET", url)

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", api_url(self.base_url, path), payload)

    def _request(
        self, method: str, url: str, payload: dict[str, Any] | None = None
    ) -> Any:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            url,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            # Demo-only local HTTP client; the base URL defaults to localhost.
            with urlopen(request, timeout=10) as response:  # noqa: S310 # nosec B310
                response_body = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8")
            raise RuntimeError(
                f"{method} {url} failed with {exc.code}: {detail}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(f"{method} {url} failed: {exc.reason}") from exc
        return json.loads(response_body) if response_body else None


def run_scenarios(client: DemoApiClient, scenario_names: set[str]) -> None:
    client.get_json("/healthz")
    for scenario in demo_scenarios():
        if scenario.name not in scenario_names:
            continue
        print(f"\n=== {scenario.name} ===")
        print(scenario.description)
        last_response: dict[str, Any] | None = None
        for event in scenario.events:
            last_response = client.post_json("/v1/events", event)
            _print_event_result(event, last_response)
        if last_response is None:
            continue
        account_external_id = scenario.events[-1]["account"]["external_id"]
        tasks = client.get_json(
            "/v1/tasks", {"account_external_id": account_external_id, "limit": "20"}
        )
        _print_tasks(account_external_id, tasks)
        last_correlation_id = last_response["correlation_id"]
        audit_rows = client.get_json(
            "/v1/audit", {"correlation_id": last_correlation_id}
        )
        _print_audit(last_correlation_id, audit_rows)


def _print_event_result(event: dict[str, Any], response: dict[str, Any]) -> None:
    print(
        "POST /v1/events "
        f"{event['event_type']} -> created={response['created_tasks']} "
        f"blocked={response['blocked_tasks']} deferred={response['deferred_tasks']} "
        f"cancelled={response['cancelled_tasks']} decisions={response['policy_decisions']} "
        f"correlation_id={response['correlation_id']}"
    )


def _print_tasks(account_external_id: str, tasks: list[dict[str, Any]]) -> None:
    print(
        f"GET /v1/tasks?account_external_id={account_external_id} -> {len(tasks)} task(s)"
    )
    for task in tasks:
        print(
            "  "
            f"{task['channel']} {task['status']} "
            f"scheduled_at={task['scheduled_at']} id={task['id']}"
        )


def _print_audit(correlation_id: str, audit_rows: list[dict[str, Any]]) -> None:
    print(f"GET /v1/audit?correlation_id={correlation_id} -> {len(audit_rows)} row(s)")
    for row in audit_rows:
        print(
            f"  {row['event_type']} actor={row['actor_type']} entity={row['entity_type']}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reset demo data and run compliant outreach demo scenarios."
    )
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument(
        "--database-url",
        default=None,
        help="Database URL to reset. Defaults to configured DATABASE_URL.",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        choices=[scenario.name for scenario in demo_scenarios()] + ["all"],
        default=None,
        help="Scenario to run. May be passed multiple times. Default: all.",
    )
    parser.add_argument(
        "--skip-reset",
        action="store_true",
        help="Do not delete existing demo tables before posting events.",
    )
    return parser.parse_args()


def selected_scenarios(values: list[str] | None) -> set[str]:
    names = {scenario.name for scenario in demo_scenarios()}
    if values is None or "all" in values:
        return names
    return set(values)


def main() -> None:
    args = parse_args()
    database_url = (
        args.database_url or os.getenv("DATABASE_URL") or get_settings().database_url
    )
    if not args.skip_reset:
        print(
            "Resetting demo database tables. This is destructive and intended for demo use only."
        )
        asyncio.run(reset_database(database_url))
    run_scenarios(DemoApiClient(args.api_url), selected_scenarios(args.scenario))
    print(
        "\nDemo scenarios complete. Inspect /v1/tasks and /v1/audit for the persisted story."
    )


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"Demo failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
