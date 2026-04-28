from __future__ import annotations

import json
import unittest
import urllib.parse
from unittest.mock import patch

from multicoders.telegram import TelegramBot, TelegramMessage, split_telegram_text, trim_telegram_caption


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps({"ok": True, "result": {"message_id": 1}}).encode("utf-8")


class TelegramBotTests(unittest.TestCase):
    def test_each_bot_sends_to_same_group(self) -> None:
        captured: list[tuple[str, dict[str, str]]] = []

        def fake_urlopen(request, timeout=0):
            payload = urllib.parse.parse_qs(request.data.decode("utf-8"))
            normalized = {key: values[0] for key, values in payload.items()}
            captured.append((request.full_url, normalized))
            return _FakeResponse()

        bots = [
            TelegramBot(name="codex", token="token-codex", chat_id="-100123"),
            TelegramBot(name="claude", token="token-claude", chat_id="-100123"),
            TelegramBot(name="gemini", token="token-gemini", chat_id="-100123"),
        ]

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            for bot in bots:
                bot.send_message(f"[run:test123] hello from {bot.name}")

        self.assertEqual(len(captured), 3)
        self.assertEqual(
            [url for url, _ in captured],
            [
                "https://api.telegram.org/bottoken-codex/sendMessage",
                "https://api.telegram.org/bottoken-claude/sendMessage",
                "https://api.telegram.org/bottoken-gemini/sendMessage",
            ],
        )
        for _, payload in captured:
            self.assertEqual(payload["chat_id"], "-100123")
            self.assertEqual(payload["disable_web_page_preview"], "true")
            self.assertIn("[run:test123]", payload["text"])

    def test_send_message_includes_topic_when_configured(self) -> None:
        captured: list[dict[str, str]] = []

        def fake_urlopen(request, timeout=0):
            payload = urllib.parse.parse_qs(request.data.decode("utf-8"))
            captured.append({key: values[0] for key, values in payload.items()})
            return _FakeResponse()

        bot = TelegramBot(name="codex", token="token-codex", chat_id="-100123", message_thread_id=77)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            bot.send_message("[run:test123] threaded message")

        self.assertEqual(captured[0]["message_thread_id"], "77")

    def test_split_telegram_text_keeps_chunks_under_limit(self) -> None:
        chunks = split_telegram_text("a" * 4097)
        self.assertEqual([len(item) for item in chunks], [4096, 1])

    def test_send_message_splits_long_text(self) -> None:
        captured: list[dict[str, str]] = []

        def fake_urlopen(request, timeout=0):
            payload = urllib.parse.parse_qs(request.data.decode("utf-8"))
            captured.append({key: values[0] for key, values in payload.items()})
            return _FakeResponse()

        bot = TelegramBot(name="codex", token="token-codex", chat_id="-100123")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            bot.send_message("a" * 4097)

        self.assertEqual(len(captured), 2)
        self.assertEqual(len(captured[0]["text"]), 4096)
        self.assertEqual(captured[1]["text"], "a")

    def test_send_photo_animation_and_sticker_use_expected_methods(self) -> None:
        captured: list[tuple[str, dict[str, str]]] = []

        def fake_urlopen(request, timeout=0):
            payload = urllib.parse.parse_qs(request.data.decode("utf-8"))
            captured.append((request.full_url, {key: values[0] for key, values in payload.items()}))
            return _FakeResponse()

        bot = TelegramBot(name="codex", token="token-codex", chat_id="-100123", message_thread_id=77)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            bot.send_photo("https://example.test/meme.jpg", caption="caption")
            bot.send_animation("https://example.test/reaction.gif", caption="gif caption")
            bot.send_sticker("sticker-file-id")

        self.assertEqual([url.rsplit("/", 1)[-1] for url, _ in captured], ["sendPhoto", "sendAnimation", "sendSticker"])
        self.assertEqual(captured[0][1]["photo"], "https://example.test/meme.jpg")
        self.assertEqual(captured[0][1]["caption"], "caption")
        self.assertEqual(captured[1][1]["animation"], "https://example.test/reaction.gif")
        self.assertEqual(captured[2][1]["sticker"], "sticker-file-id")
        for _, payload in captured:
            self.assertEqual(payload["message_thread_id"], "77")

    def test_trim_telegram_caption_truncates_to_limit(self) -> None:
        caption = trim_telegram_caption("a" * 1025)
        assert caption is not None
        self.assertEqual(len(caption), 1024)
        self.assertTrue(caption.endswith("..."))

    def test_message_matches_scope_checks_topic(self) -> None:
        bot = TelegramBot(name="codex", token="token-codex", chat_id="-100123", message_thread_id=77)
        message = TelegramMessage(
            update_id=1,
            chat_id="-100123",
            chat_type="supergroup",
            chat_title="Multicoders",
            sender_id="7",
            sender_name="Jelitox",
            is_bot=False,
            text="hello",
            date=1710000000,
            message_thread_id=88,
        )
        self.assertFalse(bot.message_matches_scope(message))


if __name__ == "__main__":
    unittest.main()
