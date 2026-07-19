# Multicoders

Multicoders is a deterministic multi-agent orchestration project built around
local `ai-parrot` sources. It coordinates research, candidate generation,
consensus review, QA validation, storage, and optional Parrot integration while
keeping a dry-run path available for local validation without LLM calls.

## What It Does

- Runs a deterministic flow: Research -> Dispatch -> Arena -> QA.
- Generates multiple candidate artifacts through coder agents or mocks.
- Evaluates candidates with judge agents or deterministic callable judges.
- Runs objective filters, security scanning, AST parsing, optional doctests,
  and pytest checks for candidate worktrees.
- Stores tasks, artifacts, verdicts, and checkpoints in SQLite.
- Supports a CLI dry-run mode that does not require provider API keys.
- Integrates local `ai-parrot` and `ai-parrot-tools` from `_refs/`.

## Local Setup

Use a project-local virtual environment. The global `python3` on this machine
does not include `pip`, so `uv` is the reliable setup path.

```bash
uv venv .venv
source .venv/bin/activate
uv --cache-dir .uv-cache pip install -e '.[dev]'
```

The project uses local Parrot sources through `tool.uv.sources` in
`pyproject.toml`, so the editable install resolves:

- `_refs/ai-parrot/packages/ai-parrot`
- `_refs/ai-parrot/packages/ai-parrot-tools`

`ai-parrot` also expects NavConfig environment assets. For local development,
create:

```bash
mkdir -p env/dev
cat > env/.env <<'EOF'
ENV=dev
ENVIRONMENT=development
DEBUG=true
PRODUCTION=false
VAULT_ENABLED=false
CACHE_BACKEND=
TEMPLATE_DIR=_refs/ai-parrot/templates
EOF
cp env/.env env/dev/.env
```

`env/`, `.venv/`, and `.uv-cache/` are ignored by git.

## CLI Usage

`python -m multicoders` is a single dispatcher in front of two engines:

- The **council / service engine** (`run`, `service`, `brainstorming`,
  `send-test-messages`, `discover-telegram-chat`) drives the Codex/Claude/Gemini
  CLIs over a real repository with Telegram mirroring.
- The **deterministic Parrot arena engine** (`arena`) runs the in-process
  Research -> Dispatch -> Arena -> QA flow.

Run `python -m multicoders --help` for the dispatcher overview, or
`python -m multicoders <command> --help` for per-engine options.

### Hornero engine

Multicoders exposes Hornero's complete lifecycle surface through a delegated
subcommand. Install the optional integration with `pip install
'multicoders[hornero]'`, then pass any Hornero command after `hornero`:

```bash
python -m multicoders hornero init --hooks
python -m multicoders hornero ingest requirements.md
python -m multicoders hornero reconcile --dry-run
python -m multicoders hornero reindex
python -m multicoders hornero wiki parrot build
python -m multicoders hornero wiki codex install
python -m multicoders hornero wiki claude install
python -m multicoders hornero wiki gemini install
python -m multicoders hornero report
python -m multicoders hornero status
```

This adapter deliberately delegates to Hornero rather than reimplementing its
SDD, evidence gates, knowledge graph, LLM-wiki, assurance, runtime, reports,
and release workflows. `multicoders run` and `multicoders arena` remain the
multi-agent execution engines; `multicoders hornero` is the project lifecycle
engine they can share.

### Arena engine

Dry-run without LLM providers:

```bash
.venv/bin/python -m multicoders arena --dry-run "Crear un endpoint de salud"
```

Real provider mode requires at least one configured provider key:

```bash
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
export GOOGLE_API_KEY=...
.venv/bin/python -m multicoders arena "Implement a thread-safe singleton in Python"
```

Resume a stored task:

```bash
.venv/bin/python -m multicoders arena --resume <task_id>
```

### Council engine

```bash
.venv/bin/python -m multicoders run --repo /path/to/repo --task "fix the bug" --task-type bugfix
.venv/bin/python -m multicoders service --db-file service.db --telegram-state-file state.json
```

## Main Modules

- `multicoders/cli.py`: supported CLI implementation.
- `multicoders/__main__.py`: `python -m multicoders` entry point.
- `multicoders/flow.py`: deterministic orchestration used by dry-run and tests.
- `multicoders/factory.py`: constructors for Parrot-backed stacks.
- `multicoders/parrot_flow.py`: Parrot-compatible orchestration wrapper.
- `multicoders/dispatcher.py`: candidate generation and worktree writing.
- `multicoders/arena.py`: objective filtering and consensus review.
- `multicoders/qa.py`: security, syntax, doctest, and pytest validation.
- `multicoders/research.py`: local context enrichment and Parrot API lens.
- `multicoders/storage.py`: SQLite persistence and checkpointing.

## Validation

Smoke command:

```bash
.venv/bin/python -m multicoders arena --dry-run "Crear un endpoint de salud"
```

Focused tests:

```bash
.venv/bin/python -m pytest \
  tests/test_dry_run.py \
  tests/test_factory.py \
  tests/test_qa.py \
  tests/test_worktree.py \
  tests/test_flow_e2e.py \
  -q
```

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the execution flow,
runtime dependencies, persistence model, validation gates, known constraints,
and the changes applied while making the project boot locally.
