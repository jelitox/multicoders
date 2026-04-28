# Multicoders

Multicoders is a deterministic agent orchestration system built on top of `ai-parrot`. It uses a Directed Acyclic Graph (DAG) approach to manage a multi-agent flow consisting of Research, Dispatching, Arena (Consensus), and QA phases.

## Core Mandates
- **Determinism**: The flow is controlled by a state machine, not just by LLM initiative.
- **Consensus**: Multiple agents (codex, claude, gemini) judge artifacts to ensure quality and reduce hallucinations.
- **QA Gates**: Every approved artifact must pass an automated QA check (syntax and doctests) before completion.
- **Auditability**: Every task, artifact, and verdict is stored in a local SQLite database for full traceability.

## Project Structure
- `multicoders/flow.py`: The main orchestration logic.
- `multicoders/research.py`: Context gathering node.
- `multicoders/dispatcher.py`: Code generation node.
- `multicoders/arena.py`: Multi-agent voting and consensus logic.
- `multicoders/qa.py`: Automated code validation node.
- `multicoders/storage.py`: SQLite persistence layer.

## Usage
To run a task through the Multicoders flow:
```bash
python3 -m multicoders "Your task here"
```

## Setup
Import `ai-parrot` as a local dependency:
```bash
pip install -e .
```
