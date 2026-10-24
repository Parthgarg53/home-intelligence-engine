"""Official MCP SDK server exposing the application services."""

import json
import os
from datetime import datetime
from pathlib import Path

from mcp.server import MCPServer

from home_intelligence_engine.application import (
    IntentApplicationService,
    intent_to_dict,
    outcome_to_dict,
)
from home_intelligence_engine.devices import SimulatedLight
from home_intelligence_engine.intent import Condition, ConditionOperator
from home_intelligence_engine.persistence import SQLiteIntentRepository


def create_mcp_server(service: IntentApplicationService | None = None) -> MCPServer:
    if service is None:
        repository_path = Path(
            os.environ.get("HOME_INTELLIGENCE_DB", "home_intelligence.sqlite3")
        )
        repository = SQLiteIntentRepository(repository_path)
        light = SimulatedLight()
        service = IntentApplicationService(repository, {light.device_id: light})

    server = MCPServer(
        "Home Intelligence Engine",
        instructions=(
            "Manage lifecycle-aware home intents. Execution tools use registered "
            "devices and the validated action stored on each intent."
        ),
    )

    @server.tool()
    def create_one_time_intent(description: str, device_id: str, action: str) -> dict:
        """Create and persist a one-time device action without executing it."""
        return intent_to_dict(service.create_one_time(description, device_id, action))

    @server.tool()
    def create_conditional_once_intent(
        description: str,
        device_id: str,
        action: str,
        fact: str,
        operator: str,
        value_json: str,
        expires_at: str,
    ) -> dict:
        """Create a one-shot request that waits for one explicit condition."""
        condition = Condition(
            fact=fact,
            operator=ConditionOperator(operator),
            value=json.loads(value_json),
        )
        expiry = datetime.fromisoformat(expires_at)
        return intent_to_dict(
            service.create_conditional_once(
                description, device_id, action, condition, expiry
            )
        )

    @server.tool()
    def create_persistent_automation(
        description: str,
        device_id: str,
        action: str,
        fact: str,
        operator: str,
        value_json: str,
        expires_at: str | None = None,
    ) -> dict:
        """Create an automation evaluated once per false-to-true condition edge."""
        condition = Condition(
            fact=fact,
            operator=ConditionOperator(operator),
            value=json.loads(value_json),
        )
        expiry = datetime.fromisoformat(expires_at) if expires_at else None
        return intent_to_dict(
            service.create_automation(
                description, device_id, action, condition, expiry
            )
        )

    @server.tool()
    def create_temporary_override(
        description: str, device_id: str, action: str, expires_at: str
    ) -> dict:
        """Create a temporary device override ending at an ISO 8601 timestamp."""
        return intent_to_dict(
            service.create_override(
                description, device_id, action, datetime.fromisoformat(expires_at)
            )
        )

    @server.tool()
    def get_intent(intent_id: str) -> dict:
        """Return one intent and its lifecycle state."""
        return intent_to_dict(service.get(intent_id))

    @server.tool()
    def list_active_intents() -> list[dict]:
        """List currently active intents, persisting any due expirations."""
        return [intent_to_dict(intent) for intent in service.list_active()]

    @server.tool()
    def cancel_intent(intent_id: str) -> dict:
        """Cancel an active intent and persist its lifecycle transition."""
        return intent_to_dict(service.cancel(intent_id))

    @server.tool()
    def execute_intent(intent_id: str, observations: dict | None = None) -> dict:
        """Execute a one-time or eligible conditional intent using its saved action."""
        return outcome_to_dict(service.execute(intent_id, observations))

    @server.tool()
    def evaluate_automation(intent_id: str, observations: dict) -> dict:
        """Evaluate an automation against supplied current observations."""
        return outcome_to_dict(service.evaluate_automation(intent_id, observations))

    @server.tool()
    def activate_temporary_override(intent_id: str) -> dict:
        """Run an override's action; it remains active until its expiry."""
        return outcome_to_dict(service.activate_override(intent_id))

    return server


def main() -> None:
    create_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()
