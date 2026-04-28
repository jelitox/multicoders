from __future__ import annotations

import unittest

from multicoders.prompts import build_chat_prompt, render_prior_payloads


class PromptTests(unittest.TestCase):
    def test_build_chat_prompt_includes_sarcastic_humor_guidance(self) -> None:
        prompt = build_chat_prompt(
            provider_name="codex",
            sender_name="Jelitox",
            user_message="que es el sol?",
            prior_messages=[],
        )
        self.assertIn("sarcastic, witty voice", prompt)
        self.assertIn("dark humor", prompt)
        self.assertIn("not cruel toward protected classes", prompt)

    def test_render_prior_payloads_truncates_large_context(self) -> None:
        rendered = render_prior_payloads([{"summary": "x" * 100}], max_chars=80)
        self.assertLessEqual(len(rendered), 112)
        self.assertIn("prior payloads truncated", rendered)


if __name__ == "__main__":
    unittest.main()
