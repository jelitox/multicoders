from __future__ import annotations

import unittest

from multicoders.app import format_group_message


class FormattingTests(unittest.TestCase):
    def test_format_group_message_renders_lists_as_bullets(self) -> None:
        rendered = format_group_message(
            "multicoders_codexitox_bot",
            "spec",
            {
                "summary": "short summary",
                "acceptance_criteria": ["first item", "second item"],
                "risks": ["risk one"],
            },
        )
        self.assertIn("Pienso esto: short summary", rendered)
        self.assertIn("Para darlo por bueno:\n- first item\n- second item", rendered)
        self.assertIn("Riesgos que veo:\n- risk one", rendered)


if __name__ == "__main__":
    unittest.main()
