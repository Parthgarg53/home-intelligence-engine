from datetime import datetime, timezone

import pytest

from home_intelligence_engine.devices import SimulatedLight
from home_intelligence_engine.execution import (
    AutomationService,
    ExecutionService,
    OverrideService,
)
from home_intelligence_engine.intent import (
    Condition,
    ConditionEvaluationError,
    ConditionOperator,
    Intent,
    IntentKind,
    IntentStatus,
)
from home_intelligence_engine.persistence import SQLiteIntentRepository


NOW = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)


def create_repository(tmp_path, **intent_values):
    repository = SQLiteIntentRepository(tmp_path / "home.sqlite3")
    values = {
        "id": "intent-1",
        "description": "Turn off the living room light",
        "kind": IntentKind.ONE_TIME,
        "created_at": NOW,
    }
    values.update(intent_values)
    intent = Intent(**values)
    repository.add(intent)
    return repository


def test_one_time_intent_executes_and_persists_verified_outcome(tmp_path):
    repository = create_repository(tmp_path)
    light = SimulatedLight(is_on=True)
    service = ExecutionService(repository, clock=lambda: NOW)

    outcome = service.execute("intent-1", light, "turn_off")

    assert outcome.status == "succeeded"
    assert outcome.explanation == "living-room-light is off"
    assert light.is_on is False
    assert repository.get("intent-1").status == IntentStatus.COMPLETED
    assert repository.list_transitions("intent-1")[0]["to_status"] == "completed"
    assert repository.list_executions("intent-1")[0]["status"] == "succeeded"


def test_failed_device_action_is_recorded_and_intent_stays_active(tmp_path):
    repository = create_repository(tmp_path)
    light = SimulatedLight()
    service = ExecutionService(repository, clock=lambda: NOW)

    outcome = service.execute("intent-1", light, "dim")

    assert outcome.status == "failed"
    assert "Unsupported light action: dim" in outcome.explanation
    assert repository.get("intent-1").status == IntentStatus.ACTIVE
    assert repository.list_transitions("intent-1") == []
    assert repository.list_executions("intent-1")[0]["status"] == "failed"


def test_expired_intent_is_marked_expired_without_device_action(tmp_path):
    repository = create_repository(
        tmp_path, expires_at=datetime(2030, 1, 1, 11, tzinfo=timezone.utc)
    )
    light = SimulatedLight()
    service = ExecutionService(repository, clock=lambda: NOW)

    with pytest.raises(ValueError, match="expired before execution"):
        service.execute("intent-1", light, "turn_off")

    assert light.is_on is True
    assert repository.get("intent-1").status == IntentStatus.EXPIRED
    assert repository.list_executions("intent-1") == []


def test_non_one_time_intent_is_not_executable_in_first_slice(tmp_path):
    repository = create_repository(tmp_path, kind=IntentKind.PERSISTENT_AUTOMATION)
    light = SimulatedLight()
    service = ExecutionService(repository, clock=lambda: NOW)

    with pytest.raises(ValueError, match="Only one-time and conditional"):
        service.execute("intent-1", light, "turn_off")

    assert light.is_on is True


def test_conditional_once_waits_until_condition_is_true(tmp_path):
    repository = create_repository(
        tmp_path,
        kind=IntentKind.CONDITIONAL_ONCE,
        condition=Condition(fact="weather.sunny", operator=ConditionOperator.EQ, value=True),
    )
    light = SimulatedLight()
    service = ExecutionService(repository, clock=lambda: NOW)

    waiting = service.execute("intent-1", light, "turn_off", {"weather.sunny": False})
    executed = service.execute("intent-1", light, "turn_off", {"weather.sunny": True})

    assert waiting.status == "waiting"
    assert waiting.execution_id is None
    assert repository.get("intent-1").status == IntentStatus.COMPLETED
    assert executed.status == "succeeded"
    assert len(repository.list_executions("intent-1")) == 1


def test_condition_evaluation_error_does_not_complete_intent(tmp_path):
    repository = create_repository(
        tmp_path,
        kind=IntentKind.CONDITIONAL_ONCE,
        condition=Condition(fact="weather.sunny", operator=ConditionOperator.EQ, value=True),
    )
    service = ExecutionService(repository, clock=lambda: NOW)

    with pytest.raises(ConditionEvaluationError, match="No observation available"):
        service.execute("intent-1", SimulatedLight(), "turn_off", {})

    assert repository.get("intent-1").status == IntentStatus.ACTIVE
    assert repository.list_executions("intent-1") == []


def test_temporary_override_suppresses_action_until_expiry(tmp_path):
    repository = SQLiteIntentRepository(tmp_path / "home.sqlite3")
    override = Intent(
        id="override-1",
        description="Keep the light on temporarily",
        kind=IntentKind.TEMPORARY_OVERRIDE,
        created_at=NOW,
        expires_at=datetime(2030, 1, 1, 14, tzinfo=timezone.utc),
        target_device_id="living-room-light",
        action="turn_on",
    )
    action = Intent(
        id="action-1",
        description="Turn off the light",
        kind=IntentKind.ONE_TIME,
        created_at=NOW,
    )
    repository.add(override)
    repository.add(action)
    light = SimulatedLight(is_on=False)
    override_result = OverrideService(repository, clock=lambda: NOW).activate(
        override.id, light
    )
    suppressed = ExecutionService(repository, clock=lambda: NOW).execute(
        action.id, light, "turn_off"
    )

    assert override_result.status == "succeeded"
    assert suppressed.status == "suppressed"
    assert "Suppressed by active override override-1" in suppressed.explanation
    assert light.is_on is True


def test_expired_override_is_persisted_and_no_longer_suppresses(tmp_path):
    repository = SQLiteIntentRepository(tmp_path / "home.sqlite3")
    override = Intent(
        id="override-1",
        description="Keep the light on temporarily",
        kind=IntentKind.TEMPORARY_OVERRIDE,
        created_at=NOW,
        expires_at=datetime(2030, 1, 1, 13, tzinfo=timezone.utc),
        target_device_id="living-room-light",
        action="turn_on",
    )
    action = Intent(
        id="action-1",
        description="Turn off the light",
        kind=IntentKind.ONE_TIME,
        created_at=NOW,
    )
    repository.add(override)
    repository.add(action)
    light = SimulatedLight(is_on=True)
    OverrideService(repository, clock=lambda: NOW).activate(override.id, light)
    after_expiry = datetime(2030, 1, 1, 13, tzinfo=timezone.utc)

    result = ExecutionService(repository, clock=lambda: after_expiry).execute(
        action.id, light, "turn_off"
    )

    assert result.status == "succeeded"
    assert repository.get(override.id).status == IntentStatus.EXPIRED
    assert light.is_on is False


def test_persistent_automation_runs_once_per_false_to_true_edge(tmp_path):
    repository = create_repository(
        tmp_path,
        kind=IntentKind.PERSISTENT_AUTOMATION,
        condition=Condition(fact="weather.sunny", operator=ConditionOperator.EQ, value=True),
        target_device_id="living-room-light",
        action="turn_off",
    )
    service = AutomationService(repository, clock=lambda: NOW)
    light = SimulatedLight()

    waiting = service.evaluate("intent-1", light, {"weather.sunny": False})
    first = service.evaluate("intent-1", light, {"weather.sunny": True})
    still_true = service.evaluate("intent-1", light, {"weather.sunny": True})
    service.evaluate("intent-1", light, {"weather.sunny": False})
    second = service.evaluate("intent-1", light, {"weather.sunny": True})

    assert waiting.status == "waiting"
    assert first.status == "succeeded"
    assert still_true.status == "condition_still_true"
    assert second.status == "succeeded"
    assert repository.get("intent-1").status == IntentStatus.ACTIVE
    assert len(repository.list_executions("intent-1")) == 2


def test_automation_failure_is_not_retried_until_condition_resets(tmp_path):
    repository = create_repository(
        tmp_path,
        kind=IntentKind.PERSISTENT_AUTOMATION,
        condition=Condition(fact="weather.sunny", operator=ConditionOperator.EQ, value=True),
        target_device_id="living-room-light",
        action="invalid_action",
    )
    service = AutomationService(repository, clock=lambda: NOW)
    light = SimulatedLight()

    failed = service.evaluate("intent-1", light, {"weather.sunny": True})
    no_retry = service.evaluate("intent-1", light, {"weather.sunny": True})

    assert failed.status == "failed"
    assert no_retry.status == "condition_still_true"
    assert len(repository.list_executions("intent-1")) == 1


def test_automation_restores_policy_after_override_expires(tmp_path):
    repository = SQLiteIntentRepository(tmp_path / "home.sqlite3")
    automation = Intent(
        id="automation-1",
        description="Turn light off when sunny",
        kind=IntentKind.PERSISTENT_AUTOMATION,
        created_at=NOW,
        condition=Condition(fact="weather.sunny", operator=ConditionOperator.EQ, value=True),
        target_device_id="living-room-light",
        action="turn_off",
    )
    override = Intent(
        id="override-1",
        description="Keep light on temporarily",
        kind=IntentKind.TEMPORARY_OVERRIDE,
        created_at=datetime(2030, 1, 1, 12, 10, tzinfo=timezone.utc),
        expires_at=datetime(2030, 1, 1, 13, tzinfo=timezone.utc),
        target_device_id="living-room-light",
        action="turn_on",
    )
    repository.add(automation)
    repository.add(override)
    light = SimulatedLight()
    AutomationService(repository, clock=lambda: NOW).evaluate(
        automation.id, light, {"weather.sunny": True}
    )
    OverrideService(
        repository, clock=lambda: datetime(2030, 1, 1, 12, 10, tzinfo=timezone.utc)
    ).activate(override.id, light)
    service = AutomationService(
        repository, clock=lambda: datetime(2030, 1, 1, 12, 30, tzinfo=timezone.utc)
    )
    during_override = service.evaluate(
        automation.id, light, {"weather.sunny": True}
    )
    assert light.is_on is True
    assert during_override.status == "suppressed"

    after_expiry = AutomationService(
        repository, clock=lambda: datetime(2030, 1, 1, 13, tzinfo=timezone.utc)
    ).evaluate(automation.id, light, {"weather.sunny": True})

    assert after_expiry.status == "succeeded"
    assert light.is_on is False
    assert repository.get(override.id).status == IntentStatus.EXPIRED
