# Home Intelligence Engine

Home Intelligence Engine is a small Python project for representing and managing smart-home instructions over time. Its focus is **intent lifecycle management**: distinguishing a one-time request from an instruction that remains active, expires, or is cancelled.

The project is being built in small, testable milestones. The current foundation contains an intent model and SQLite persistence. Device execution, condition evaluation, MCP tools, and natural-language planning are future work.

## Current capabilities

- Models four intent kinds: one-time action, conditional one-shot, persistent automation, and temporary override.
- Tracks active, completed, cancelled, and expired statuses.
- Requires timezone-aware creation, expiry, and supplied clock timestamps.
- Applies an intent's expiry before completing or cancelling it, so an expired intent cannot make either transition.
- Persists intent records in SQLite and can load active records for runtime recovery.
- Records status transitions in a separate history table and rejects stale updates when the stored status has changed.

An intent status describes the lifecycle of a request. It does not prove that a physical device changed state; device adapters and execution outcome records have not been implemented yet.

## Project layout

```text
home_intelligence_engine/
├── intent.py       # Intent kinds, statuses, validation, and lifecycle methods
└── persistence.py  # SQLite repository and transition history
tests/
├── test_intent.py
└── test_persistence.py
```

## Requirements

- Python 3.11 or newer
- Dependencies listed in `pyproject.toml`

SQLite uses Python's standard library, so it does not require a separate database server.

## Set up locally

From the repository root on macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

If the virtual environment already exists, activate it with `source .venv/bin/activate` before using `python` or `pytest`.

## Run the tests

```bash
python -m pytest -q
```

## Persistence notes

`SQLiteIntentRepository` initializes a versioned SQLite schema when it is constructed. It stores intent data in `intents` and lifecycle history in `intent_transitions`. Pass a database path to the repository; for example:

```python
from home_intelligence_engine.persistence import SQLiteIntentRepository

repository = SQLiteIntentRepository("home_intelligence.sqlite3")
```

The repository exposes `add`, `get`, `list_active`, `save_transition`, and `list_transitions`. Callers currently apply lifecycle methods on an `Intent` and then persist a status change with `save_transition`, providing the prior status and a timezone-aware transition time. Active records can be loaded with `list_active()` after an application restart. Expiry is refreshed by the lifecycle methods or `refresh_expiry()`; there is not yet a background scheduler.

## Development direction

The next milestone is a vertical slice through a simulated device: accept a structured one-time intent, validate eligibility, execute a simulated action, and record its outcome. Conditional automations, temporary overrides, MCP integration, and natural-language planning will follow after the execution foundation is in place.
