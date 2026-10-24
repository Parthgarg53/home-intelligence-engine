
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, Field, model_validator


class IntentKind(str, Enum):
    ONE_TIME = "one_time"
    CONDITIONAL_ONCE = "conditional_once"
    PERSISTENT_AUTOMATION = "persistent_automation"
    TEMPORARY_OVERRIDE = "temporary_override"


class IntentStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ConditionOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"


class ConditionEvaluationError(ValueError):
    """Raised when a condition cannot be evaluated from available observations."""


class Condition(BaseModel):
    fact: str
    operator: ConditionOperator
    value: str | int | float | bool

    def evaluate(self, observations: Mapping[str, Any]) -> bool:
        if self.fact not in observations:
            raise ConditionEvaluationError(f"No observation available for {self.fact}")
        actual = observations[self.fact]
        if self.operator == ConditionOperator.EQ:
            return actual == self.value
        if self.operator == ConditionOperator.NE:
            return actual != self.value
        if (
            not isinstance(actual, (int, float))
            or isinstance(actual, bool)
            or not isinstance(self.value, (int, float))
            or isinstance(self.value, bool)
        ):
            raise ConditionEvaluationError(
                f"Condition {self.fact} with {self.operator.value} requires numeric values"
            )
        if self.operator == ConditionOperator.GT:
            return actual > self.value
        if self.operator == ConditionOperator.GTE:
            return actual >= self.value
        if self.operator == ConditionOperator.LT:
            return actual < self.value
        return actual <= self.value


class Intent(BaseModel):
    id: str
    description: str
    kind: IntentKind
    status: IntentStatus = IntentStatus.ACTIVE
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    expires_at: datetime | None = None
    condition: Condition | None = None
    target_device_id: str | None = None
    action: str | None = None

    @model_validator(mode="after")
    def validate_expiry(self):
        for field_name in ("created_at", "expires_at"):
            value = getattr(self, field_name)
            if value is not None and value.utcoffset() is None:
                raise ValueError(f"{field_name} must include a timezone")
        return self

    def complete(self, now: datetime | None = None) -> None:
        self.refresh_expiry(now)
        if self.status != IntentStatus.ACTIVE:
            raise ValueError(f"Only active intents can be completed; current status is {self.status.value}")
        self.status = IntentStatus.COMPLETED

    def cancel(self, now: datetime | None = None) -> None:
        self.refresh_expiry(now)
        if self.status != IntentStatus.ACTIVE:
            raise ValueError(f"Only active intents can be cancelled; current status is {self.status.value}")
        self.status = IntentStatus.CANCELLED

    def refresh_expiry(self, now: datetime | None = None) -> None:
        if self.status != IntentStatus.ACTIVE or self.expires_at is None:
            return

        current_time = now or datetime.now(timezone.utc)
        if current_time.utcoffset() is None:
            raise ValueError("now must include a timezone")

        if current_time >= self.expires_at:
            self.status = IntentStatus.EXPIRED
