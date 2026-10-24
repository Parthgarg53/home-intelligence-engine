"""Application services shared by MCP and future user interfaces."""

from datetime import datetime, timezone
from uuid import uuid4

from home_intelligence_engine.devices import Device
from home_intelligence_engine.execution import (
    AutomationService,
    ExecutionOutcome,
    ExecutionService,
    OverrideService,
)
from home_intelligence_engine.intent import Condition, Intent, IntentKind, IntentStatus
from home_intelligence_engine.persistence import SQLiteIntentRepository


class IntentApplicationService:
    def __init__(
        self,
        repository: SQLiteIntentRepository,
        devices: dict[str, Device],
    ):
        self.repository = repository
        self.devices = devices
        self.execution = ExecutionService(repository)
        self.automation = AutomationService(repository)
        self.overrides = OverrideService(repository)

    def create_one_time(self, description: str, device_id: str, action: str) -> Intent:
        self._require_device(device_id)
        intent = Intent(
            id=str(uuid4()),
            description=description,
            kind=IntentKind.ONE_TIME,
            target_device_id=device_id,
            action=action,
        )
        self.repository.add(intent)
        return intent

    def create_conditional_once(
        self,
        description: str,
        device_id: str,
        action: str,
        condition: Condition,
        expires_at: datetime | None,
    ) -> Intent:
        self._require_device(device_id)
        intent = Intent(
            id=str(uuid4()),
            description=description,
            kind=IntentKind.CONDITIONAL_ONCE,
            target_device_id=device_id,
            action=action,
            condition=condition,
            expires_at=expires_at,
        )
        self.repository.add(intent)
        return intent

    def create_automation(
        self,
        description: str,
        device_id: str,
        action: str,
        condition: Condition,
        expires_at: datetime | None = None,
    ) -> Intent:
        self._require_device(device_id)
        intent = Intent(
            id=str(uuid4()),
            description=description,
            kind=IntentKind.PERSISTENT_AUTOMATION,
            target_device_id=device_id,
            action=action,
            condition=condition,
            expires_at=expires_at,
        )
        self.repository.add(intent)
        return intent

    def create_override(
        self,
        description: str,
        device_id: str,
        action: str,
        expires_at: datetime,
    ) -> Intent:
        self._require_device(device_id)
        if expires_at <= datetime.now(timezone.utc):
            raise ValueError("Temporary override expiry must be in the future")
        intent = Intent(
            id=str(uuid4()),
            description=description,
            kind=IntentKind.TEMPORARY_OVERRIDE,
            target_device_id=device_id,
            action=action,
            expires_at=expires_at,
        )
        self.repository.add(intent)
        return intent

    def get(self, intent_id: str) -> Intent:
        intent = self.repository.get(intent_id)
        if intent is None:
            raise ValueError(f"Intent {intent_id} was not found")
        return intent

    def list_active(self) -> list[Intent]:
        self.repository.expire_due(datetime.now(timezone.utc))
        return self.repository.list_active()

    def cancel(self, intent_id: str) -> Intent:
        now = datetime.now(timezone.utc)
        self.repository.expire_due(now)
        intent = self.get(intent_id)
        previous_status = intent.status
        intent.cancel(now)
        self.repository.save_transition(intent, previous_status, now)
        return intent

    def execute(self, intent_id: str, observations: dict | None = None) -> ExecutionOutcome:
        intent = self.get(intent_id)
        device_id, action = self._action_details(intent)
        return self.execution.execute(
            intent_id, self._require_device(device_id), action, observations
        )

    def evaluate_automation(
        self, intent_id: str, observations: dict
    ) -> ExecutionOutcome:
        intent = self.get(intent_id)
        device_id, _ = self._action_details(intent)
        return self.automation.evaluate(
            intent_id, self._require_device(device_id), observations
        )

    def activate_override(self, intent_id: str) -> ExecutionOutcome:
        intent = self.get(intent_id)
        device_id, _ = self._action_details(intent)
        return self.overrides.activate(intent_id, self._require_device(device_id))

    def _require_device(self, device_id: str) -> Device:
        try:
            return self.devices[device_id]
        except KeyError as error:
            raise ValueError(f"Device {device_id} is not registered") from error

    @staticmethod
    def _action_details(intent: Intent) -> tuple[str, str]:
        if not intent.target_device_id or not intent.action:
            raise ValueError(f"Intent {intent.id} has no executable device action")
        return intent.target_device_id, intent.action


def intent_to_dict(intent: Intent) -> dict:
    return intent.model_dump(mode="json")


def outcome_to_dict(outcome: ExecutionOutcome) -> dict:
    return {
        "intent_id": outcome.intent_id,
        "execution_id": outcome.execution_id,
        "status": outcome.status,
        "explanation": outcome.explanation,
    }
