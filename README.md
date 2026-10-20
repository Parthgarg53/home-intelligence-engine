# Home Intelligence Engine

**A lifecycle-aware intelligence layer for the smart home.**

Home Intelligence Engine is designed to understand what a person wants their home to do, how long that instruction should remain in effect, when it is allowed to run, and what happened when the system acted. It treats an instruction as managed intent—not simply a sentence to translate into a device command.

## The idea

Smart-home requests have different lifetimes. “Turn off the living room light” is a one-time action. “Turn it off if it gets sunny today” is a conditional request with a deadline. “Whenever it gets sunny” is an ongoing automation. “Keep it on for two hours” is a temporary override of normal behavior.

The engine is intended to represent these requests explicitly, manage their lifecycle, coordinate execution, and explain its decisions. Users should be able to understand whether an instruction is waiting, active, completed, cancelled, or expired—and why.

## Product principles

- **Intent has a lifecycle.** Every request has a clear type, status, validity window, and permitted transitions.
- **Actions are accountable.** Requested, planned, attempted, and verified outcomes remain distinct. A command being sent is not proof that a device changed state.
- **Failures stay visible.** Condition-evaluation and device failures must not be reported as successful completion. Retry and recovery behavior should be explicit.
- **Conflicts are explainable.** Temporary overrides and competing instructions should follow defined precedence rules, with a reason for any suppressed action.
- **Time is testable.** Time-dependent behavior uses an injectable clock so deadlines and recovery can be checked deterministically.
- **Interfaces share the same rules.** MCP tools, a future natural-language planner, and other clients should call the same application services rather than bypassing lifecycle and execution controls.
- **The foundation stays small.** The engine favors clear Python modules and well-defined interfaces over premature services, schedulers, or frameworks.

## Intended capabilities

1. **Intent lifecycle** — model one-time actions, conditional one-shots, persistent automations, and temporary overrides; validate transitions, deadlines, and cancellation.
2. **Durable state** — persist intent state and transition history in SQLite, and recover active work after restart.
3. **Reliable execution** — validate eligibility, execute through device adapters, capture outcomes, and apply explicit retry and idempotency policies.
4. **Conditions and conflicts** — evaluate time-bounded conditions, resolve competing instructions, restore normal behavior after overrides, and explain decisions.
5. **MCP access** — expose selected operations through thin, validated tools that use the same application services.
6. **Natural-language planning** — translate requests into structured proposals, ask clarifying questions when needed, and require validation before accepting or executing a proposal.

Longer-term goals—such as balancing comfort, preferences, and energy use across devices—can build on these foundations once lifecycle and execution behavior are reliable.

## Architecture

The project is organized around boundaries that keep core behavior independent of integrations:

| Layer | Responsibility |
| --- | --- |
| Domain | Intent kinds, states, conditions, deadlines, and execution records |
| Application | Lifecycle rules, validation, authorization, and orchestration |
| Persistence | Intent state, transition history, and recovery |
| Runtime | Eligibility checks, scheduling, and execution coordination |
| Device adapters | A consistent interface to simulated and real devices |
| MCP and planner | External tools and structured natural-language proposals |

The LLM planner must not mutate storage directly or control devices outside the application and execution layers.

## Technology

- Python 3.11+
- Pydantic 2 for validated domain models
- SQLite for initial persistence
- Official Python MCP SDK for the planned MCP interface
- pytest for automated tests

## Development

Create and activate a virtual environment, then install the project and development dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Run the test suite with:

```bash
python -m pytest -q
```

## Project status

The repository is under active development. The current foundation includes typed intent kinds and statuses, lifecycle validation, timezone-aware expiry handling, and a SQLite repository with active-intent recovery and transition history. Device execution, condition evaluation, conflict resolution, MCP tools, and natural-language planning remain planned capabilities.
