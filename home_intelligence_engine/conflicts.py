"""Deterministic precedence rules for competing device actions."""

from dataclasses import dataclass
from datetime import datetime

from home_intelligence_engine.intent import Intent, IntentKind


@dataclass(frozen=True)
class ActionRequest:
    intent_id: str
    device_id: str
    action: str
    kind: IntentKind
    created_at: datetime


@dataclass(frozen=True)
class ConflictDecision:
    allowed: bool
    explanation: str
    winning_intent_id: str | None = None


class ConflictResolver:
    """Overrides suppress normal actions; the newest active override wins."""

    def resolve(
        self,
        request: ActionRequest,
        active_intents: list[Intent],
        now: datetime,
    ) -> ConflictDecision:
        overrides = [
            intent
            for intent in active_intents
            if intent.id != request.intent_id
            and intent.kind == IntentKind.TEMPORARY_OVERRIDE
            and intent.target_device_id == request.device_id
            and (intent.expires_at is None or now < intent.expires_at)
        ]
        if request.kind == IntentKind.TEMPORARY_OVERRIDE:
            overrides.append(
                Intent(
                    id=request.intent_id,
                    description="override candidate",
                    kind=IntentKind.TEMPORARY_OVERRIDE,
                    created_at=request.created_at,
                    target_device_id=request.device_id,
                    action=request.action,
                )
            )
        if not overrides:
            return ConflictDecision(True, "No active override conflicts with this action")

        winner = max(overrides, key=lambda intent: (intent.created_at, intent.id))
        if winner.id == request.intent_id:
            return ConflictDecision(
                True,
                "This is the newest override for the device",
                winner.id,
            )
        return ConflictDecision(
            False,
            f"Suppressed by active override {winner.id} for {request.device_id}",
            winner.id,
        )
