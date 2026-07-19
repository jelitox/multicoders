# Contributing to Multicoders

This project uses a DAG-based orchestration powered by **Parrot**.

## Development Setup

1. Clone with submodules: `git clone --recursive <url>`
2. Install in editable mode: `pip install -e .`
3. Run tests: `make test`

## Architecture

- **ResearchNode**: Enriches prompts using local context.
- **Dispatcher**: Generates candidate artifacts using Parrot BasicAgents.
- **Arena**: Evaluates candidates using consensus-based judging.
- **QANode**: Performs static analysis and security scanning.
- **CommitNode**: Integrates with GitToolkit for final output.
