# Multicoders — Architecture Spec for Continued Iteration

> Hand this to Claude Code. It assumes you already have the repo context from Fase 1.
> Scope: continue the engine-unification roadmap and lay the foundation for a
> provider-agnostic, multi-backend orchestrator that generalizes beyond code.

## 1. North star

Multicoders is a **conductor for multi-agent work** that lets anyone get maximum
leverage from the LLM subscriptions they already pay for — not only dedicated
developers. Today it builds, tests, and documents **code**; the architecture must
generalize to **non-technical, real-world processes** (administrative workflows,
ops, document production).

Positioning vs. existing tools: harnesses like Hermes or OpenClaw either require
you to be a dedicated developer or carry their own trade-offs (token proxying,
account-pool fragility, ToS exposure). Multicoders aims to be a **simple, safe
conductor** that orchestrates the *official* provider CLIs people already have,
without ever touching their credentials.

## 2. Current state (end of Fase 2)

- **Unified dispatcher** in `__main__.py`: `arena` → deterministic Parrot arena
  engine (`cli.py`); `run` / `service` / `brainstorming` / ... → council engine
  (`app.py`). The broken `systemd` `service` routing is fixed; 65 focal tests green.
- **Arena pipeline**: Research → Dispatch → Arena (judges vote, `ast.parse`
  syntax filter, majority consensus) → QA (security scanner for `eval`/`exec`/
  `compile`/`os.system`/`subprocess(shell=True)`, static analysis, compile,
  doctests, pytest) → Completion. SQLite persistence (`tasks`, `artifacts`,
  `verdicts`, `checkpoints`).
- **Council engine**: Telegram service.
- **Weak memory today**: `ResearchNode.enrich` does `os.walk` (≤50 files) + an
  AST signature dump concatenated into the prompt; `build_repo_context` lists
  candidate files; `render_prior_payloads` truncates to ~12k chars; history lives
  in `TelegramState` JSON and loose specs in `docs/brainstorming/`. No semantic
  memory across runs.
- **Backends today**: `MockCoder` (deterministic, dry-run) and `ParrotCoder`
  (wraps `parrot.bots.agent.BasicAgent`).

### Fase 2 cleanup — completed

- Deleted `parrot-multicoders-v2/v3/v4` (untracked, unreferenced, ~2.2 MB).
- **Kept** `agents/` and `plugins/` on purpose — they are scaffolds that match
  the knowledge/Oddie pattern, not dead prototypes.
- `.gitignore` expanded (`logs/`, `outputs/`, `settings/`, `.agents/`);
  `multicoders.db` already covered by `*.db`.
- Verified: import OK, dispatcher OK, **28 focal tests green**, no regressions.

### Reality check on `ai-parrot` (do not skip)

The pinned `_refs/ai-parrot` (commit `271aba90`) **does not** include
`pageindex`, `graphindex`, or `skills` — the rich examples target a newer
version (FEAT-190/191/198/240). Available **today** in the pin:

- `knowledge/ontology` (graph_store, intent resolver, RAG mixin, merger)
- `stores/kb` (`AbstractKnowledgeBase`, `LocalKB`, `RedisKnowledgeBase` — with
  `search()` and `format_context()`)
- `tools/working_memory` (`WorkingMemoryToolkit`)

Anything depending on PageIndex / GraphIndex / Skills requires a **submodule bump**.

## 3. Target architecture

### 3.1 AgentBackend protocol (Fase 3)

A single abstraction so both engines (arena + council) consume agents uniformly.

```python
class AgentBackend(Protocol):
    name: str
    def capabilities(self) -> set[str]: ...        # e.g. {"code", "review", "doc"}
    async def health(self) -> bool: ...
    async def generate(self, task: TaskSpec, ctx: AgentContext) -> Artifact: ...
```

Variants:

- **ParrotBackend** — in-process `parrot.bots` agents (refactor of `ParrotCoder`).
- **CliBackend** — spawns an *official provider CLI* as a subprocess in its own
  authenticated session (`claude` / `codex` / `gemini`). Headless invocation;
  prompt via stdin or `-p`; capture stdout as the artifact.
- **ApiBackend** — provider API keys (pay-per-token), for CI / headless /
  contributors without a subscription.
- **LocalBackend** — Ollama / local models, zero-cost contributors.
- **MockBackend** — deterministic, dry-run/tests (refactor of `MockCoder`).

Backend resolution order is **configurable**; the core must never silently fall
back to API billing.

#### HARD CONSTRAINT (non-negotiable)

`CliBackend` MUST use each provider's **official login flow, one account per
user, bring-your-own-auth**. It must **NEVER** proxy, pool, or redistribute
subscription tokens; never call reverse-engineered/undocumented endpoints; never
share credentials. This is both a ToS-safety requirement (Anthropic actively
restricts third-party harness reuse of subscription tokens and has banned for it;
OpenAI treats account pooling as a grey/violating area) **and** the precondition
for shipping as open source without exposing users' accounts to bans.

#### CLI adapter notes

- **Claude Code**: `claude -p "<prompt>"` (print / non-interactive). Subscription
  via `claude login` (Pro/Max OAuth). The adapter MUST NOT set `ANTHROPIC_API_KEY`
  in its environment — that silently switches the session to API billing.
- **Codex CLI**: `codex` with ChatGPT login; set `CODEX_NON_INTERACTIVE=1`;
  device-code flow available for headless hosts.
- **Gemini CLI**: `gemini` headless mode, Google-account free tier (≈1000 req/day).
  NOTE: for the free / Google One tier, Gemini CLI is being replaced by
  **Antigravity CLI** (effective ~June 18, 2026) — the adapter should target the
  successor binary; verify the current command at build time.

### 3.2 Layered MemoryService (Fase 4)

"Single storage" is a **memory stack**, not just SQLite. Introduce a
`MemoryService` facade (layered) consumed via `AgentBackend`/`AgentContext`.

| Layer | Purpose | Backing | Availability |
|---|---|---|---|
| Operational ledger | tasks / status / audit | SQLite (keep) | available now |
| Documental / code | ground spec/review/vote in the real repo | PageIndex `retrieve()` instead of 12k truncation | needs bump |
| Episodic / semantic | "did we solve this before?", recurring patterns | GraphIndex (tasks↔solutions↔verdicts↔concepts; communities=themes, centrality=key patterns, relevance=related past decisions) | needs bump |
| Procedural | reusable how-tos the council learns | SkillRegistry | needs bump |
| Working / short-term | staging run phases | WorkingMemoryToolkit | available now |

The transactional ledger stays in SQLite; **context and learning** move to
PageIndex + GraphIndex + Skills. Skills/docs follow the Oddie layout
(`agents/<name>/skills/`, `agents/<name>/documentation/`) — keep those scaffolds.

### 3.3 Domain generalization (the evolution path)

Abstract the code-specific pipeline into **domain-pluggable** roles so the same
orchestrator can run non-code work:

- `Worker` (was Coder), `Judge`, `Validator` (was QA).
- A `DomainProfile` defines: artifact type, objective/syntax filter, validators,
  success criteria.
  - **code profile**: `ast.parse` filter; validators = security scanner + pytest
    + doctests.
  - **admin/process profile**: schema validation, business-rule checks, HITL
    approval.
- The orchestrator (Research → Dispatch → Arena → Validate → Complete) stays
  domain-agnostic; only the `DomainProfile` swaps.

## 4. Roadmap & acceptance criteria

- **Fase 2 — Cleanup (DONE)**: deleted dead `parrot-multicoders-v2/v3/v4`
  prototypes (~2.2 MB); **kept** `agents/`/`plugins/` scaffolds (they match the
  knowledge/Oddie pattern); expanded `.gitignore`. Test drift noted in Fase 1
  (`ResearchNode` rename; `test_research_parser` using removed
  `_extract_parrot_signatures`; `MagicMock` task_id in `test_parrot_flow`) — fold
  into Fase 3 if not yet resolved. **Verified**: import + dispatcher OK, 28 focal
  tests green, no regressions.
- **Fase 3 — AgentBackend**: define the protocol; refactor
  `MockCoder`/`ParrotCoder` → `MockBackend`/`ParrotBackend`; add `CliBackend`
  (claude/codex/gemini) with BYO-auth; add configurable backend resolution.
  **Acceptance**: arena + council both run through the same backend interface;
  dry-run unaffected; one CLI backend demonstrated end-to-end.
- **Fase 4 — MemoryService**: introduce the `MemoryService` facade.
  - *4a (no bump)*: `WorkingMemory` for phase staging + `stores/kb` `LocalKB` as
    the first cross-run "decision memory". Low risk.
  - *4b (after ai-parrot bump)*: PageIndex as the `ResearchNode`/
    `build_repo_context` engine; GraphIndex for episodic/semantic recall.
  **Acceptance**: `ResearchNode` grounds via `retrieve()` instead of an `os.walk`
  dump; cross-run recall demonstrable.
- **Fase 5 — Domain generalization**: extract `DomainProfile`; ship one non-code
  profile as proof.

## 5. Non-goals

- No token proxying, pooling, or credential sharing — ever.
- Don't couple the core to any single provider or auth mechanism.
- Don't replace the SQLite ledger; augment around it.

## 6. Immediate next step for Claude Code

Fases 1 and 2 are done. Optionally **consolidate with a commit of Fases 1+2**
before continuing. Then begin **Fase 3**: define the `AgentBackend` protocol and
refactor `MockBackend`/`ParrotBackend` *before* adding `CliBackend`. If the Fase 1
test drift (`ResearchNode` rename, removed `_extract_parrot_signatures`,
`MagicMock` task_id) is still open, fix it as the first task of Fase 3.
