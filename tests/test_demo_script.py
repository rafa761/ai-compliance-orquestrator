from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "seed_demo.py"


def load_seed_demo_module():
    spec = importlib.util.spec_from_file_location("seed_demo", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reset_tables_are_ordered_from_dependent_records_to_parent_records():
    seed_demo = load_seed_demo_module()

    assert [model.__tablename__ for model in seed_demo.RESET_MODELS] == [
        "audit_events",
        "outreach_tasks",
        "policy_decisions",
        "inbound_events",
        "accounts",
        "customers",
    ]


def test_all_scenarios_run_in_reviewer_story_order():
    seed_demo = load_seed_demo_module()

    assert [scenario.name for scenario in seed_demo.demo_scenarios()] == [
        "happy_path_delinquent_account",
        "opt_out_blocks_future_outreach",
        "payment_received_cancels_scheduled_outreach",
    ]


def test_opt_out_scenario_posts_opt_out_before_new_delinquency_event():
    seed_demo = load_seed_demo_module()

    scenario = seed_demo.demo_scenarios()[1]

    assert [event["event_type"] for event in scenario.events] == [
        "opt_out_received",
        "account_delinquent",
    ]
    assert (
        scenario.events[0]["customer"]["external_id"]
        == scenario.events[1]["customer"]["external_id"]
    )
    assert (
        scenario.events[0]["account"]["external_id"]
        == scenario.events[1]["account"]["external_id"]
    )


def test_payment_cancellation_scenario_creates_work_before_payment_event():
    seed_demo = load_seed_demo_module()

    scenario = seed_demo.demo_scenarios()[2]

    assert [event["event_type"] for event in scenario.events] == [
        "account_delinquent",
        "payment_received",
    ]
    assert scenario.events[0]["account"]["status"] == "delinquent"
    assert scenario.events[1]["account"]["status"] == "resolved"
    assert (
        scenario.events[0]["account"]["external_id"]
        == scenario.events[1]["account"]["external_id"]
    )


def test_selected_scenarios_defaults_to_all_scenarios():
    seed_demo = load_seed_demo_module()

    assert seed_demo.selected_scenarios(None) == {
        "happy_path_delinquent_account",
        "opt_out_blocks_future_outreach",
        "payment_received_cancels_scheduled_outreach",
    }


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://localhost:8000", "http://localhost:8000/v1/events"),
        ("http://localhost:8000/", "http://localhost:8000/v1/events"),
    ],
)
def test_api_url_builder_handles_trailing_slashes(base_url: str, expected: str):
    seed_demo = load_seed_demo_module()

    assert seed_demo.api_url(base_url, "/v1/events") == expected
