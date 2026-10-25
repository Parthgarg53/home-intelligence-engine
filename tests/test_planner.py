from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from home_intelligence_engine.application import IntentApplicationService
from home_intelligence_engine.devices import SimulatedLight
from home_intelligence_engine.intent import (
    Condition,
    ConditionOperator,
    IntentKind,
    IntentStatus,
)
from home_intelligence_engine.planner import (
    IntentDraft,
    IntentPlanningService,
    OpenAIIntentPlanner,
    PlannerResponse,
    PlanningDecision,
)
from home_intelligence_engine.persistence import SQLiteIntentRepository


NOW = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)


def proposal(**overrides):
    values = {
        "kind": IntentKind.ONE_TIME,
        "description": "Turn off the living room light",
        "device_id": "living-room-light",
        "action": "turn_off",
        "condition": None,
        "expires_at": None,
    }
    values.update(overrides)
    return PlannerResponse(
        decision=PlanningDecision.PROPOSAL,
        message="I can prepare that action.",
        questions=[],
        proposal=IntentDraft(**values),
    )


class FixedPlanner:
    def __init__(self, response):
        self.response = response
        self.received = None

    def propose(self, request, device_catalog, current_time, timezone_name):
        self.received = (request, device_catalog, current_time, timezone_name)
        return self.response


def make_service(tmp_path, planner):
    repository = SQLiteIntentRepository(tmp_path / "home.sqlite3")
    light = SimulatedLight()
    app = IntentApplicationService(repository, {light.device_id: light})
    return repository, light, IntentPlanningService(
        planner, app, timezone_name="Asia/Kolkata", clock=lambda: NOW
    )


def test_planner_returns_proposal_without_persisting_until_accept(tmp_path):
    model = FixedPlanner(proposal())
    repository, light, service = make_service(tmp_path, model)

    result = service.propose("Turn off the living room light")

    assert result.decision == PlanningDecision.PROPOSAL
    assert model.received[1] == {"living-room-light": ["turn_off", "turn_on"]}
    assert model.received[2] == NOW
    assert model.received[3] == "Asia/Kolkata"
    assert repository.list_active() == []

    intent = service.accept(result)

    assert intent.kind == IntentKind.ONE_TIME
    assert intent.status == IntentStatus.ACTIVE
    assert repository.get(intent.id) == intent
    assert light.is_on is True  # Accepting a draft never executes it.


def test_planner_clarification_cannot_be_accepted_or_persisted(tmp_path):
    response = PlannerResponse(
        decision=PlanningDecision.CLARIFICATION,
        message="I need one detail before preparing this request.",
        questions=["Which room should I use?"],
        proposal=None,
    )
    _, _, service = make_service(tmp_path, FixedPlanner(response))

    with pytest.raises(ValueError, match="Only a proposal can be accepted"):
        service.accept(service.propose("Turn off the light"))


@pytest.mark.parametrize(
    "draft, message",
    [
        (proposal(device_id="unregistered-device").proposal, "not registered"),
        (proposal(action="unlock_door").proposal, "not supported"),
        (
            proposal(kind=IntentKind.CONDITIONAL_ONCE).proposal,
            "requires a condition",
        ),
    ],
)
def test_invalid_model_proposals_are_rejected(tmp_path, draft, message):
    repository, _, service = make_service(tmp_path, FixedPlanner(None))
    result = PlannerResponse(
        decision=PlanningDecision.PROPOSAL,
        message="Proposed action",
        questions=[],
        proposal=draft,
    )

    with pytest.raises(ValueError, match=message):
        service.accept(result)

    assert repository.list_active() == []


def test_conditional_proposal_requires_an_expiry_and_condition(tmp_path):
    draft = proposal(
        kind=IntentKind.CONDITIONAL_ONCE,
        condition=Condition(
            fact="weather.sunny", operator=ConditionOperator.EQ, value=True
        ),
        expires_at=datetime(2030, 1, 2, tzinfo=timezone.utc),
    )
    repository, _, service = make_service(tmp_path, FixedPlanner(None))

    intent = service.accept(draft)

    assert intent.kind == IntentKind.CONDITIONAL_ONCE
    assert repository.get(intent.id).condition == draft.proposal.condition


def test_openai_adapter_uses_structured_schema_and_never_executes_tools():
    expected = proposal()
    captured = {}

    class Responses:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_parsed=expected)

    client = SimpleNamespace(responses=Responses())
    planner = OpenAIIntentPlanner("configured-model", client=client)

    result = planner.propose(
        "Turn off the living room light",
        {"living-room-light": ["turn_off", "turn_on"]},
        NOW,
        "UTC",
    )

    assert result == expected
    assert captured["text_format"] is PlannerResponse
    assert captured["model"] == "configured-model"
    prompt = captured["input"][0]["content"]
    assert "Never execute an action" in prompt
