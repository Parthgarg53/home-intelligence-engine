"""Application service for executing eligible one-time intents."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from home_intelligence_engine.conflicts import ActionRequest, ConflictResolver
from home_intelligence_engine.devices import Device
from home_intelligence_engine.intent import IntentKind, IntentStatus
from home_intelligence_engine.persistence import SQLiteIntentRepository


@dataclass(frozen=True)
class ExecutionOutcome:
    intent_id: str
    execution_id: int | None
    status: str
    explanation: str


class ExecutionService:
    """Validate, record, and run one-time actions through a device adapter.

    Failed attempts are recorded and leave the intent active for a deliberate
    later retry. The service does not automatically retry physical actions.
    """

    def __init__(
        self,
        repository: SQLiteIntentRepository,
        clock: Callable[[], datetime] | None = None,
    ):
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.conflict_resolver = ConflictResolver()

    def execute(
        self,
        intent_id: str,
        device: Device,
        action: str,
        observations: Mapping[str, Any] | None = None,
    ) -> ExecutionOutcome:
        requested_at = self.clock()
        self.repository.expire_due(requested_at)
        intent = self.repository.get(intent_id)
        if intent is None:
            raise ValueError(f"Intent {intent_id} was not found")
        if intent.kind not in {IntentKind.ONE_TIME, IntentKind.CONDITIONAL_ONCE}:
            raise ValueError("Only one-time and conditional one-shot intents can be executed")
        if intent.status != IntentStatus.ACTIVE:
            if intent.status == IntentStatus.EXPIRED:
                raise ValueError(f"Intent {intent_id} expired before execution")
            raise ValueError(f"Intent must be active; current status is {intent.status.value}")

        previous_status = intent.status
        intent.refresh_expiry(requested_at)
        if intent.status == IntentStatus.EXPIRED:
            self.repository.save_transition(intent, previous_status, requested_at)
            raise ValueError(f"Intent {intent_id} expired before execution")

        if intent.kind == IntentKind.CONDITIONAL_ONCE:
            if intent.condition is None:
                raise ValueError("Conditional one-shot intent requires a condition")
            if observations is None:
                raise ValueError("Observations are required to evaluate this condition")
            if not intent.condition.evaluate(observations):
                return ExecutionOutcome(
                    intent_id, None, "waiting", "Condition is not met; intent remains active"
                )

        request = ActionRequest(
            intent_id=intent.id,
            device_id=device.device_id,
            action=action,
            kind=intent.kind,
            created_at=intent.created_at,
        )
        active_overrides = [
            candidate
            for candidate in self.repository.list_active()
            if candidate.kind == IntentKind.TEMPORARY_OVERRIDE
            and candidate.target_device_id == device.device_id
            and (candidate.expires_at is None or requested_at < candidate.expires_at)
            and any(
                record["status"] == "succeeded"
                for record in self.repository.list_executions(candidate.id)
            )
        ]
        decision = self.conflict_resolver.resolve(request, active_overrides, requested_at)
        if not decision.allowed:
            return ExecutionOutcome(intent_id, None, "suppressed", decision.explanation)

        execution_id = self.repository.start_execution(
            intent_id, device.device_id, action, requested_at
        )
        try:
            explanation = device.execute(action)
        except Exception as error:
            finished_at = self.clock()
            self.repository.finish_execution(
                execution_id, "failed", str(error), finished_at
            )
            return ExecutionOutcome(
                intent_id=intent_id,
                execution_id=execution_id,
                status="failed",
                explanation=f"Execution failed: {error}",
            )

        finished_at = self.clock()
        self.repository.finish_execution(
            execution_id, "succeeded", explanation, finished_at
        )
        # Eligibility is checked at the start of the attempt. If a successful
        # action crosses its deadline while executing, record it as completed.
        intent.complete(now=requested_at)
        self.repository.save_transition(intent, previous_status, finished_at)
        return ExecutionOutcome(
            intent_id=intent_id,
            execution_id=execution_id,
            status="succeeded",
            explanation=explanation,
        )


class OverrideService:
    """Activate a temporary device override and persist its expiry window."""

    def __init__(
        self,
        repository: SQLiteIntentRepository,
        clock: Callable[[], datetime] | None = None,
    ):
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.conflict_resolver = ConflictResolver()

    def activate(self, intent_id: str, device: Device) -> ExecutionOutcome:
        intent = self.repository.get(intent_id)
        if intent is None:
            raise ValueError(f"Intent {intent_id} was not found")
        if intent.kind != IntentKind.TEMPORARY_OVERRIDE:
            raise ValueError("Intent is not a temporary override")
        if intent.status != IntentStatus.ACTIVE:
            raise ValueError(f"Override must be active; current status is {intent.status.value}")
        if intent.target_device_id != device.device_id or not intent.action:
            raise ValueError("Override must specify this device and an action")

        now = self.clock()
        self.repository.expire_due(now)
        intent = self.repository.get(intent_id)
        if intent is None:
            raise ValueError(f"Intent {intent_id} was not found")
        if intent.status != IntentStatus.ACTIVE:
            raise ValueError(f"Override must be active; current status is {intent.status.value}")
        if intent.expires_at is None or now >= intent.expires_at:
            if intent.expires_at is not None:
                previous_status = intent.status
                intent.refresh_expiry(now)
                self.repository.save_transition(intent, previous_status, now)
            raise ValueError("Temporary override requires a future expiry")
        if any(
            record["status"] == "succeeded"
            for record in self.repository.list_executions(intent_id)
        ):
            return ExecutionOutcome(
                intent_id,
                None,
                "succeeded",
                f"Override is already active until {intent.expires_at.isoformat()}",
            )

        active_overrides = [
            candidate
            for candidate in self.repository.list_active()
            if candidate.id != intent_id
            and candidate.kind == IntentKind.TEMPORARY_OVERRIDE
            and candidate.target_device_id == device.device_id
            and (candidate.expires_at is None or now < candidate.expires_at)
            and any(
                record["status"] == "succeeded"
                for record in self.repository.list_executions(candidate.id)
            )
        ]
        request = ActionRequest(
            intent.id, device.device_id, intent.action, intent.kind, intent.created_at
        )
        decision = self.conflict_resolver.resolve(request, active_overrides, now)
        if not decision.allowed:
            return ExecutionOutcome(intent_id, None, "suppressed", decision.explanation)

        execution_id = self.repository.start_execution(
            intent_id, device.device_id, intent.action, now
        )
        try:
            result = device.execute(intent.action)
        except Exception as error:
            self.repository.finish_execution(
                execution_id, "failed", str(error), self.clock()
            )
            return ExecutionOutcome(
                intent_id,
                execution_id,
                "failed",
                f"Override activation failed: {error}",
            )
        self.repository.finish_execution(execution_id, "succeeded", result, self.clock())
        return ExecutionOutcome(
            intent_id,
            execution_id,
            "succeeded",
            f"{result}; override remains active until {intent.expires_at.isoformat()}",
        )


class AutomationService:
    """Evaluate persistent automations once on each false-to-true edge.

    Each rising edge creates one recorded attempt. Failed or interrupted attempts
    are not retried automatically; the condition must become false and rise again.
    """

    def __init__(
        self,
        repository: SQLiteIntentRepository,
        clock: Callable[[], datetime] | None = None,
    ):
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.conflict_resolver = ConflictResolver()

    def evaluate(
        self,
        intent_id: str,
        device: Device,
        observations: Mapping[str, Any],
    ) -> ExecutionOutcome:
        now = self.clock()
        self.repository.expire_due(now)
        intent = self.repository.get(intent_id)
        if intent is None:
            raise ValueError(f"Intent {intent_id} was not found")
        if intent.kind != IntentKind.PERSISTENT_AUTOMATION:
            raise ValueError("Intent is not a persistent automation")
        if intent.status != IntentStatus.ACTIVE:
            raise ValueError(f"Automation must be active; current status is {intent.status.value}")
        if intent.condition is None:
            raise ValueError("Persistent automation requires a condition")
        if intent.target_device_id != device.device_id or not intent.action:
            raise ValueError("Automation must specify this device and an action")

        matched = intent.condition.evaluate(observations)
        previous_match = self.repository.get_automation_condition_state(intent_id)
        if not matched:
            self.repository.set_automation_condition_state(intent_id, False, now)
            return ExecutionOutcome(
                intent_id, None, "waiting", "Condition is false; automation is armed"
            )

        active_overrides = [
            candidate
            for candidate in self.repository.list_active()
            if candidate.kind == IntentKind.TEMPORARY_OVERRIDE
            and candidate.target_device_id == device.device_id
            and (candidate.expires_at is None or now < candidate.expires_at)
            and any(
                record["status"] == "succeeded"
                for record in self.repository.list_executions(candidate.id)
            )
        ]
        request = ActionRequest(
            intent.id, device.device_id, intent.action, intent.kind, intent.created_at
        )
        decision = self.conflict_resolver.resolve(request, active_overrides, now)
        if not decision.allowed:
            return ExecutionOutcome(intent_id, None, "suppressed", decision.explanation)
        if previous_match:
            return ExecutionOutcome(
                intent_id, None, "condition_still_true", "No new condition edge; action not repeated"
            )

        execution_id = self.repository.start_automation_execution(
            intent_id, device.device_id, intent.action, now
        )
        try:
            result = device.execute(intent.action)
        except Exception as error:
            self.repository.finish_execution(execution_id, "failed", str(error), self.clock())
            return ExecutionOutcome(
                intent_id, execution_id, "failed", f"Automation action failed: {error}"
            )
        self.repository.finish_execution(execution_id, "succeeded", result, self.clock())
        return ExecutionOutcome(intent_id, execution_id, "succeeded", result)
