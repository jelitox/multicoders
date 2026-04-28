from __future__ import annotations

import unittest

from multicoders.providers import PROVIDER_SPECS, ProviderResult, build_provider_command, extract_json_object, extract_text_output


class ProviderCommandTests(unittest.TestCase):
    def test_codex_command_skips_git_repo_check(self) -> None:
        command = build_provider_command(
            provider_name="codex",
            base_command=PROVIDER_SPECS["codex"].command,
            prompt="hello world",
            model=None,
            supports_model=True,
        )
        self.assertIn("--skip-git-repo-check", command)
        self.assertEqual(command[-1], "hello world")

    def test_gemini_command_places_prompt_after_flag(self) -> None:
        command = build_provider_command(
            provider_name="gemini",
            base_command=PROVIDER_SPECS["gemini"].command,
            prompt="hello world",
            model=None,
            supports_model=True,
        )
        self.assertEqual(
            command,
            ["gemini", "--output-format", "json", "--approval-mode", "auto_edit", "--prompt", "hello world"],
        )

    def test_claude_command_appends_prompt_positionally(self) -> None:
        command = build_provider_command(
            provider_name="claude",
            base_command=PROVIDER_SPECS["claude"].command,
            prompt="hello world",
            model=None,
            supports_model=True,
        )
        self.assertEqual(command[-1], "hello world")
        self.assertEqual(command[:-1], PROVIDER_SPECS["claude"].command)

    def test_extract_json_object_from_fenced_block(self) -> None:
        payload = extract_json_object("prefix\n```json\n{\"solution_id\":\"x\",\"summary\":\"y\"}\n```\nsuffix")
        self.assertEqual(payload["solution_id"], "x")

    def test_extract_json_object_unwraps_nested_response_json_string(self) -> None:
        raw = '{"session_id":"abc","response":"{\\"solution_id\\":\\"gemini-solution\\",\\"summary\\":\\"ok\\"}"}'
        payload = extract_json_object(raw)
        self.assertEqual(payload["solution_id"], "gemini-solution")

    def test_extract_json_object_ignores_braces_inside_strings(self) -> None:
        raw = 'prefix {"solution_id":"x","summary":"keeps } and { inside strings"} suffix'
        payload = extract_json_object(raw)
        self.assertEqual(payload["summary"], "keeps } and { inside strings")

    def test_extract_json_object_normalizes_fenced_nested_response(self) -> None:
        raw = '```json\n{"response":"{\\"solution_id\\":\\"codex-solution\\",\\"summary\\":\\"ok\\"}"}\n```'
        payload = extract_json_object(raw)
        self.assertEqual(payload["solution_id"], "codex-solution")

    def test_extract_text_output_unwraps_provider_json_response(self) -> None:
        raw = '{"session_id":"abc","response":"respuesta limpia"}'
        self.assertEqual(extract_text_output(raw), "respuesta limpia")

    def test_provider_result_text_output_unwraps_nested_result(self) -> None:
        result = ProviderResult(provider="gemini", stdout='{"result":{"content":{"parts":[{"text":"hola"}]}}}', stderr="")
        self.assertEqual(result.text_output(), "hola")


if __name__ == "__main__":
    unittest.main()
