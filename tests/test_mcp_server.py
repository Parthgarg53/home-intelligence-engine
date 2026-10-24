import asyncio
import json

from home_intelligence_engine.application import IntentApplicationService
from home_intelligence_engine.devices import SimulatedLight
from home_intelligence_engine.mcp_server import create_mcp_server
from home_intelligence_engine.persistence import SQLiteIntentRepository


def test_mcp_tools_create_and_execute_using_application_service(tmp_path):
    repository = SQLiteIntentRepository(tmp_path / "home.sqlite3")
    light = SimulatedLight()
    service = IntentApplicationService(repository, {light.device_id: light})
    server = create_mcp_server(service)

    async def call_tools():
        tools = await server.list_tools()
        created = await server.call_tool(
            "create_one_time_intent",
            {
                "description": "Turn off the living room light",
                "device_id": "living-room-light",
                "action": "turn_off",
            },
        )
        intent = json.loads(created.content[0].text)
        executed = await server.call_tool(
            "execute_intent", {"intent_id": intent["id"]}
        )
        return tools, intent, executed

    tools, intent, result = asyncio.run(call_tools())

    assert {tool.name for tool in tools} >= {
        "create_one_time_intent",
        "execute_intent",
        "list_active_intents",
        "cancel_intent",
    }
    assert intent["kind"] == "one_time"
    assert result.is_error is False
    assert json.loads(result.content[0].text)["status"] == "succeeded"
    assert repository.get(intent["id"]).status.value == "completed"
    assert light.is_on is False
