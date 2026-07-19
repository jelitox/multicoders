"""Centralized prompts for Multicoders."""
from __future__ import annotations

import json
from pathlib import Path

from multicoders.stacks import StackProfile

MAX_PRIOR_PAYLOAD_CHARS = 12000


def render_prior_payloads(payloads: list[dict[str, object]], max_chars: int = MAX_PRIOR_PAYLOAD_CHARS) -> str:
    rendered = json.dumps(payloads, ensure_ascii=True, indent=2)
    if len(rendered) <= max_chars:
        return rendered
    return rendered[: max_chars - 32] + "\n...<prior payloads truncated>..."


def build_discussion_prompt(
    *,
    provider_name: str,
    phase: str,
    repo: Path,
    task_type: str,
    task: str,
    stack: StackProfile,
    branch: str,
    recent_commit: str,
    candidate_files: list[str],
    prior_payloads: list[dict[str, object]],
    winning_solution_id: str | None = None,
    candidate_solution_ids: list[str] | None = None,
) -> str:
    candidates = "\n".join(f"- {path}" for path in candidate_files[:20]) or "- No candidate files were preselected"
    previous = render_prior_payloads(prior_payloads)
    solution_clause = ""
    if winning_solution_id:
        solution_clause = f"\nWinning solution to implement: {winning_solution_id}\n"
    candidate_clause = ""
    if candidate_solution_ids:
        candidate_clause = "Candidate solution ids for selection:\n" + "\n".join(
            f"- {solution_id}" for solution_id in candidate_solution_ids
        )
        candidate_clause += "\n"

    return f"""You are the {provider_name} agent in a 3-agent software council with codex, claude, and gemini.

You are operating inside this git repository: {repo}

Task type: {task_type}
User request:
{task}

Repository context:
- Stack: {stack.label}
- Stack rationale: {stack.rationale}
- Branch: {branch}
- Recent commit: {recent_commit}

Stack rules:
{chr(10).join(f"- {rule}" for rule in stack.rules)}

Candidate files to inspect first:
{candidates}
{solution_clause}
{candidate_clause}
Discussion phase: {phase}

Prior discussion payloads:
{previous}

Output rules:
- Return valid JSON only.
- Do not wrap the JSON in markdown.
- Keep rationale concise and concrete.
- Do not modify unrelated files, generated caches, or dependency lockfiles unless required by the task.

Phase contract:
- If phase is "spec", return:
  {{
    "solution_id": "{provider_name}-solution",
    "summary": "one-line summary",
    "problem": "what is happening",
    "proposal": "specific fix or feature approach",
    "acceptance_criteria": ["..."],
    "risks": ["..."],
    "files_to_touch": ["relative/path"]
  }}
- If phase is "review", return:
  {{
    "solution_id": "{provider_name}-review",
    "preferred_solution_id": "solution-id",
    "summary": "one-line review summary",
    "strengths": ["..."],
    "concerns": ["..."],
    "recommended_changes": ["..."]
  }}
- If phase is "vote", return:
  {{
    "solution_id": "{provider_name}-vote",
    "vote_for": "solution-id",
    "summary": "one-line vote summary",
    "reasoning": ["..."],
    "must_have_checks": ["..."]
  }}
- If phase is "tie_break", return:
  {{
    "solution_id": "{provider_name}-tie-break",
    "vote_for": "solution-id",
    "summary": "one-line tie-break summary",
    "reasoning": ["..."],
    "must_have_checks": ["..."]
  }}
- If phase is "implement", first inspect the repository, then edit files directly in the working tree to implement the chosen solution. Do not commit.
  Run the most relevant local validation you can reasonably run.
  Return:
  {{
    "solution_id": "{provider_name}-implementation",
    "implemented_solution_id": "{winning_solution_id or ''}",
    "summary": "one-line implementation summary",
    "changed_files": ["relative/path"],
    "validation": ["commands run or checks performed"],
    "notes": ["remaining risks or follow-ups"]
  }}
"""


def build_chat_prompt(
    *,
    provider_name: str,
    sender_name: str,
    user_message: str,
    prior_messages: list[dict[str, str]],
    media_capabilities: str | None = None,
    catchup_note: str | None = None,
) -> str:
    history = "\n".join(
        f"- {item['speaker']}: {item['text']}"
        for item in prior_messages
        if item.get("speaker") and item.get("text")
    ) or "- No previous agent messages yet."

    catchup_block = ""
    if catchup_note:
        catchup_block = f"\nCatch-up briefing (you were offline):\n{catchup_note}\n"

    return f"""You are {provider_name}, one of three AI participants in a Telegram group chat with codex, claude, and gemini.

The human participant who wrote the latest message is: {sender_name}
Latest user message:
{user_message}

Conversation so far from the group:
{history}
{catchup_block}
{media_capabilities or "Rich media directives: no configured media catalog was provided. You may still use normal emoji/emoticons directly in text."}

Instructions:
- Reply like a person in a group chat, not like a JSON API.
- Use natural, direct language.
- Lean into a sarcastic, witty voice with a bit of dark humor when it fits.
- Use emoji/emoticons sparingly when they improve tone or make a joke land.
- Keep the humor playful and clever, not cruel toward protected classes or graphic about real harm.
- Keep the answer concise but useful.
- Add something new if another agent already answered.
- If the latest prior message is from another agent, answer that agent directly and keep the thread moving.
- Avoid repeating the same point unless you are sharpening or correcting it.
- If earlier answers were already good, explicitly build on them with one extra angle, example, caveat, or clarification.
- Answer in Spanish unless the user message clearly asks for another language.
- Return plain text only. No JSON. No markdown fences.
"""


def build_brainstorm_prompt(
    *,
    provider_name: str,
    phase: str,
    topic: str,
    repo: Path,
    stack: StackProfile,
    branch: str,
    recent_commit: str,
    candidate_files: list[str],
    prior_payloads: list[dict[str, object]],
    round_number: int,
    selected_solution_id: str | None = None,
    selected_improvement_id: str | None = None,
    media_capabilities: str | None = None,
) -> str:
    candidates = "\n".join(f"- {path}" for path in candidate_files[:20]) or "- No candidate files were preselected"
    previous = render_prior_payloads(prior_payloads)
    selection_clause = ""
    if selected_solution_id:
        selection_clause += f"\nSelected solution id: {selected_solution_id}\n"
    if selected_improvement_id:
        selection_clause += f"Selected improvement id: {selected_improvement_id}\n"

    return f"""You are {provider_name}, taking part in a brainstorming session about a design problem.

Repository: {repo}
Topic: {topic}
Round: {round_number}
Phase: {phase}

Repository context:
- Stack: {stack.label}
- Stack rationale: {stack.rationale}
- Branch: {branch}
- Recent commit: {recent_commit}

Stack rules:
{chr(10).join(f"- {rule}" for rule in stack.rules)}

Candidate files:
{candidates}

Prior discussion payloads:
{previous}

{selection_clause}
{media_capabilities or "Rich media directives: no configured media catalog was provided. You may still use normal emoji/emoticons directly in text."}

Output rules:
- Return valid JSON only.
- Do not wrap the JSON in markdown.
- Keep it compact, but include concrete numbers and identifiers.
- You must score yourself and the other bots where requested.

Phase contract:
- If phase is "proposal", return:
  {{
    "solution_id": "{provider_name}-brainstorm-r{round_number}-proposal",
    "summary": "one-line proposal summary",
    "approach": "concise approach",
    "self_score": 1,
    "risks": ["..."],
    "improvement_ideas": ["..."]
  }}
- If phase is "score", return:
  {{
    "solution_id": "{provider_name}-brainstorm-r{round_number}-score",
    "scores": {{"proposal-id": 1}},
    "best_proposal_id": "proposal-id",
    "summary": "one-line scoring summary",
    "reasons": ["..."]
  }}
- If phase is "improvement", return:
  {{
    "solution_id": "{provider_name}-brainstorm-r{round_number}-improvement",
    "target_solution_id": "{selected_solution_id or 'selected-solution-id'}",
    "improvement_id": "{provider_name}-brainstorm-r{round_number}-improvement-1",
    "summary": "one-line improvement summary",
    "improvement": "specific enhancement",
    "self_score": 1,
    "tradeoffs": ["..."]
  }}
- If phase is "improvement_vote", return:
  {{
    "solution_id": "{provider_name}-brainstorm-r{round_number}-improvement-vote",
    "vote_for": "improvement-id",
    "summary": "one-line vote summary",
    "reasoning": ["..."]
  }}
- If phase is "proposal_vote", return the same shape as "improvement_vote" but vote for a proposal id instead of an improvement id.
- If phase is "spec", return:
  {{
    "solution_id": "{provider_name}-brainstorm-r{round_number}-spec",
    "spec_title": "short title",
    "file_name": "filename.md",
    "spec_markdown": "# ...",
    "summary": "one-line spec summary"
  }}
"""


def get_coder_system_prompt(name: str, role: str) -> str:
    return (
        f"You are {name}, an expert {role}. "
        "Output ONLY clean Python code. "
        "Do NOT include markdown code blocks (```python). "
        "Do NOT include explanations or preamble. "
        "Your output must be valid Python code that can be executed directly."
    )


def get_judge_system_prompt(name: str) -> str:
    return (
        f"You are {name}, a senior software engineer and code reviewer. "
        "Review the provided Python code for correctness, safety, and best practices. "
        "You must respond in a structured format:\n"
        "VOTE: <APPROVE/REJECT>\n"
        "REASON: <Your brief reasoning here>\n\n"
        "Be strict but fair. If the code is broken, REJECT it."
    )


def get_research_system_prompt() -> str:
    return (
        "You are a research agent. Your task is to analyze the repository structure "
        "and provide relevant context for a coding task. "
        "Identify key files, classes, and functions that might be relevant."
    )
