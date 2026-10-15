
from datetime import datetime, timezone
from enum import Enum

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


class Intent(BaseModel):
    id: str
    description: str
    kind: IntentKind
    status: IntentStatus = IntentStatus.ACTIVE
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_expiry(self):
        for field_name in ("created_at", "expires_at"):
            value = getattr(self, field_name)
            if value is not None and value.utcoffset() is None:
                raise ValueError(f"{field_name} must include a timezone")
        return self

    def complete(self) -> None:
        if self.status != IntentStatus.ACTIVE:
            raise ValueError("Only active intents can be completed")
        self.status = IntentStatus.COMPLETED

    def cancel(self) -> None:
        if self.status != IntentStatus.ACTIVE:
            raise ValueError("Only active intents can be cancelled")
        self.status = IntentStatus.CANCELLED

    def refresh_expiry(self, now: datetime | None = None) -> None:
        if self.status != IntentStatus.ACTIVE or self.expires_at is None:
            return

        current_time = now or datetime.now(timezone.utc)
        if current_time.utcoffset() is None:
            raise ValueError("now must include a timezone")

        if current_time >= self.expires_at:
            self.status = IntentStatus.EXPIRED
