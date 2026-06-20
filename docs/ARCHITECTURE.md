# Multicoders Architecture

## Overview

Multicoders orchestrates multiple coding agents through a deterministic pipeline.
The project keeps two execution surfaces:

- A deterministic local flow used by CLI dry-run, tests, and offline validation.
- A Parrot-compatible wrapper used when real `ai-parrot` agents are available.

The local flow is the operational baseline because it can run without provider
keys and without making LLM calls.

## Execution Flow

1. Research
   - `ResearchNode` receives the raw prompt.
   - It scans local repository context.
   - It uses `ParrotLens` to extract real Parrot API signatures from local
     `_refs/ai-parrot` sources when available.
   - It stores a research checkpoint.

2. Dispatch
   - `Dispatcher` sends the enriched prompt to coder agents.
   - In dry-run, `MockCoder` instances return deterministic Python snippets.
   - In real mode, `ParrotCoder` wraps `parrot.bots.agent.BasicAgent`.
   - Candidate artifacts are persisted in SQLite.
   - Optional worktree isolation writes each candidate to `candidate.py`.

3. Arena
   - `Arena` first runs an objective syntax filter with `ast.parse`.
   - It then asks judges to vote on each candidate.
   - Judges can be Parrot judges, mocks, or simple callables.
   - Votes are normalized from `approve`/`approved` and `reject`/`rejected`.
   - Consensus is reached by majority approval among active judges.
   - Verdicts and checkpoints are persisted.

4. QA
   - `QANode` checks the selected winner.
   - Security scanner rejects dangerous calls such as `eval`, `exec`,
     `compile`, `os.system`, and unsafe `subprocess.Popen(shell=True)`.
   - Static analysis reports missing return type hints and placeholder bodies.
   - Code must compile.
   - Embedded doctests run when present.
   - Candidate worktrees with tests are validated through pytest.
   - QA verdicts are persisted.

5. Completion
   - Successful tasks are marked `completed`.
   - Failed consensus or QA exhaustion marks the task `needs_human`.
   - Winner metadata and output are printed by the CLI.
   - Losing worktrees are removed after a successful winner is selected.

## Runtime Surfaces

### CLI

`python -m multicoders` is a single dispatcher (`multicoders/__main__.py`) in
front of two engines:

- `arena ...` -> `multicoders.cli.main` — the deterministic Parrot arena engine.
- everything else (`run`, `service`, `brainstorming`, `send-test-messages`,
  `discover-telegram-chat`) -> `multicoders.app.main` — the council / Telegram
  service engine. `app.parse_args` auto-prefixes `run` when no known subcommand
  leads the arguments.

This wiring is why the systemd unit's `python -m multicoders service ...`
resolves correctly; before unification `__main__` routed only to the arena
engine, which rejected the `service` subcommand.

Arena engine modes:

- `arena --dry-run`: deterministic mocks, no LLM calls.
- `arena` (default real mode): requires provider keys.
- `arena --resume <task_id>`: loads an existing task payload and reruns from a
  stored task context.

### Deterministic Flow

`multicoders.flow.MulticodersFlow` is synchronous at its public boundary and can
work with both synchronous and async components. It is used by tests and dry-run.

### Parrot-Compatible Flow

`multicoders.parrot_flow.ParrotMulticodersFlow` keeps the Parrot stack entry
point available. The installed `ai-parrot` version exposes a different
`FlowNode` API than the local implementation originally expected, so the wrapper
now executes its node handlers sequentially instead of constructing obsolete
`FlowNode(name=..., handler=...)` instances.

## Provider-Agnostic Backends (`multicoders/backends/`)

A single `AgentBackend` protocol (`name`, `capabilities()`, `health()`,
`generate(TaskSpec, AgentContext) -> Artifact`) lets both engines consume agents
uniformly. Variants:

- `ParrotBackend` — in-process `parrot.bots` agents.
- `CliBackend` — the official provider CLI in the user's own authenticated
  session (BYO-auth). Strips provider API-key env vars before invoking so a
  subscription session is never silently billed as API usage; never proxies or
  pools tokens. Wraps the same `providers.run_provider` the council uses.
- `ApiBackend` — provider API keys (pay-per-token); opt-in only, never an
  automatic fallback.
- `LocalBackend` — local models via an Ollama-compatible endpoint.
- `MockBackend` — deterministic, for dry-run/tests.

`BackendCoder`/`BackendJudge` adapters bridge any backend onto the existing
`Dispatcher`/`Arena` interfaces. The arena CLI exposes `--backend {parrot,cli,mock}`.
Resolution (`resolver.py`) honours an explicit order + capability + health and
never auto-inserts a billed backend.

## Layered Memory (`multicoders/memory/`)

`MemoryService` turns "single storage" into a memory stack. The SQLite ledger
stays transactional; context and learning live in layers:

- working — `InProcessWorkingMemory`, per-run phase staging (Fase 4a).
- decisions — `JsonDecisionMemory`, durable cross-run episodic recall by token
  overlap (Fase 4a); upgraded to `GraphIndexEpisodicMemory` when GraphIndex is
  present (Fase 4b).
- documents — `PageIndexDocumentMemory`, grounds research via `retrieve()`
  instead of the `os.walk` file dump (Fase 4b).

The PageIndex/GraphIndex layers are feature-detected: they report
`available() == False` on the current ai-parrot pin and activate automatically
once the submodule is bumped (the pin lacks `parrot.knowledge.pageindex/graphindex`).

## Domain Profiles (`multicoders/domains/`)

A `DomainProfile` (objective filter + validators + artifact kind) makes the
pipeline domain-agnostic; only the profile swaps:

- `CodeProfile` — `ast.parse` filter; security/compile/doctest validators.
- `ProcessProfile` — non-code proof: JSON administrative/ops workflows; schema +
  business-rule validators with an HITL `needs_human` flag.

`Arena.objective_filter` and `MulticodersFlow`'s winner gate delegate to the
profile when set (default `None` keeps the code behavior). The arena CLI exposes
`--domain`.

## Persistence Model

SQLite schema lives in `multicoders/schema.sql`.

Tables:

- `tasks`: task id, status, payload, timestamps.
- `artifacts`: generated candidates, authors, content, optional workdir.
- `verdicts`: judge and QA votes with reasoning.
- `checkpoints`: per-node JSON snapshots for audit and resume support.

The `Storage` class initializes the schema and performs compatibility migration
for the `artifacts.workdir` column.

## Local Dependencies

`pyproject.toml` uses normal package names in `[project.dependencies]` and maps
local source paths through `[tool.uv.sources]`:

- `ai-parrot`
- `ai-parrot-tools[git,docker,sandbox,codeinterpreter,db]`

This avoids invalid wheel metadata from relative `file:` dependency references
and lets `uv` install the project in editable mode.

## Local Environment Requirements

`ai-parrot` imports `navconfig`, which requires an `env/` structure. Minimal
local files:

- `env/.env`
- `env/dev/.env`

Required local values:

```dotenv
ENV=dev
ENVIRONMENT=development
DEBUG=true
PRODUCTION=false
VAULT_ENABLED=false
CACHE_BACKEND=
TEMPLATE_DIR=_refs/ai-parrot/templates
```

`TEMPLATE_DIR` points to the templates bundled in `_refs/ai-parrot`, avoiding
the `Notify: template directory .../templates does not exist` startup failure.

## Implemented Fixes

- Created a project-local `.venv` using `uv`.
- Installed local Parrot packages and project dependencies in editable mode.
- Replaced invalid relative `file:` dependencies with `tool.uv.sources`.
- Added git ignores for `.venv/`, `.uv-cache/`, and `env/`.
- Added `etc/config.ini` as a minimal NavConfig placeholder.
- Routed `python -m multicoders` to the supported CLI.
- Restored `create_multicoders_stack` as a compatibility factory alias.
- Restored deterministic `MulticodersFlow` behavior for injected components.
- Added synchronous `Dispatcher.dispatch` with async `dispatch_async`.
- Disabled default Parrot tools for coder and judge agents to avoid loading
  unrelated heavy tools during stack construction.
- Made `Arena` support Parrot judges, mock judges, and callable judges.
- Normalized vote values and persisted judge verdicts consistently.
- Adapted `ParrotMulticodersFlow` away from the obsolete `FlowNode` constructor.

## Current Validation Status

Validated successfully:

```bash
.venv/bin/python -m multicoders --dry-run "Crear un endpoint de salud"
```

Focused passing tests:

```bash
.venv/bin/python -m pytest \
  tests/test_dry_run.py \
  tests/test_factory.py \
  tests/test_qa.py \
  tests/test_worktree.py \
  tests/test_flow_e2e.py \
  -q
```

Known test issue:

- `tests/test_parrot_flow.py::TestParrotFlow::test_flow_success` contains an
  assertion with a newly-created `MagicMock()` as the expected task id. That
  assertion does not match any real generated task id and should be updated to
  use `unittest.mock.ANY` or assert the status result instead.

## Known Constraints

- First import of `ai-parrot` is slow because it loads optional scientific and
  plotting packages.
- Real provider mode still depends on external credentials and provider quotas.
- `ParrotMulticodersFlow` is currently Parrot-compatible but not using native
  `AgentsFlow` topology construction because the local implementation targeted
  an older API.
- `env/` is intentionally local-only and ignored, so new machines must create
  the minimal NavConfig files during setup.
