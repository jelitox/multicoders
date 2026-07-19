from __future__ import annotations

import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from multicoders.app import AgentConfig, ask_chat_agent, send_chat_response, write_agent_internal_log
from multicoders.providers import ProviderError, ProviderResult
from multicoders.telegram import TelegramBot, TelegramMessage, split_telegram_text, trim_telegram_caption


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps({"ok": True, "result": {"message_id": 1}}).encode("utf-8")


class _FakeBot:
    name = "fake-bot"
    chat_id = "-100123"
    message_thread_id = None

    def __init__(self) -> None:
        self.sent_messages: list[str] = []
        self.sent_photos: list[tuple[str, str | None]] = []
        self.sent_animations: list[tuple[str, str | None]] = []
        self.sent_stickers: list[str] = []

    def send_message(self, text: str) -> None:
        self.sent_messages.append(text)

    def send_photo(self, photo: str, caption: str | None = None) -> None:
        self.sent_photos.append((photo, caption))

    def send_animation(self, animation: str, caption: str | None = None) -> None:
        self.sent_animations.append((animation, caption))

    def send_sticker(self, sticker: str) -> None:
        self.sent_stickers.append(sticker)


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

    def test_chat_response_redacts_secret_before_telegram_message(self) -> None:
        bot = _FakeBot()
        agent = AgentConfig(provider="gemini", display_name="Gemini", model="gemini-test", bot=bot)

        send_chat_response(
            agent,
            "No publicar BOT_TOKEN=123456:abcdefghijklmnopqrstuvwxyz ni sk-testsecret1234567890",
            dry_run=False,
            run_id="run123",
        )

        self.assertEqual(len(bot.sent_messages), 1)
        sent = bot.sent_messages[0]
        self.assertIn("[REDACTED]", sent)
        self.assertNotIn("123456:abcdefghijklmnopqrstuvwxyz", sent)
        self.assertNotIn("sk-testsecret1234567890", sent)

    def test_chat_response_redacts_secret_before_telegram_media_caption(self) -> None:
        bot = _FakeBot()
        agent = AgentConfig(provider="gemini", display_name="Gemini", model="gemini-test", bot=bot)

        with patch("multicoders.app.media_catalogs", return_value={"gif": {"shipit": "https://example.test/shipit.gif"}}):
            send_chat_response(
                agent,
                "Caption con BOT_TOKEN=123456:abcdefghijklmnopqrstuvwxyz\n[gif:shipit]",
                dry_run=False,
                run_id="run123",
            )

        self.assertEqual(len(bot.sent_animations), 1)
        _, caption = bot.sent_animations[0]
        self.assertIsNotNone(caption)
        self.assertIn("[REDACTED]", caption or "")
        self.assertNotIn("123456:abcdefghijklmnopqrstuvwxyz", caption or "")

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

    def test_agent_internal_log_is_written_per_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            agent = AgentConfig(provider="gemini", display_name="Gemini", model="gemini-test", bot=None)
            result = ProviderResult(
                provider="gemini",
                stdout='{"response":"solo chat","BOT_TOKEN":"123456:abcdefghijklmnopqrstuvwxyz","stats":{"tokens":{"total":123}}}',
                stderr="ClearcutLogger: Flush already in progress\nAuthorization: Bearer sk-testsecret1234567890",
            )

            write_agent_internal_log(repo=repo, agent=agent, run_id="run123", result=result, phase="chat")

            log_path = repo / ".multicoders" / "agent-logs" / "gemini" / "run123.jsonl"
            payload = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(payload["provider"], "gemini")
            self.assertEqual(payload["run_id"], "run123")
            self.assertIn('"stats"', payload["stdout"])
            self.assertIn("ClearcutLogger", payload["stderr"])
            self.assertNotIn("123456:abcdefghijklmnopqrstuvwxyz", payload["stdout"])
            self.assertNotIn("sk-testsecret1234567890", payload["stderr"])
            self.assertIn("[REDACTED]", payload["stdout"])
            self.assertIn("[REDACTED]", payload["stderr"])

    def test_agent_internal_log_rotates_when_limit_is_reached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"MULTICODERS_AGENT_LOG_MAX_BYTES": "1"}):
            repo = Path(tmp)
            agent = AgentConfig(provider="gemini", display_name="Gemini", model="gemini-test", bot=None)
            result = ProviderResult(provider="gemini", stdout='{"response":"solo chat"}', stderr="")

            write_agent_internal_log(repo=repo, agent=agent, run_id="run123", result=result, phase="chat1")
            write_agent_internal_log(repo=repo, agent=agent, run_id="run123", result=result, phase="chat2")

            log_path = repo / ".multicoders" / "agent-logs" / "gemini" / "run123.jsonl"
            self.assertTrue(log_path.exists())
            self.assertTrue(log_path.with_name("run123.jsonl.1").exists())
            current_payload = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
            rotated_payload = json.loads(log_path.with_name("run123.jsonl.1").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(current_payload["phase"], "chat2")
            self.assertEqual(rotated_payload["phase"], "chat1")

    def test_chat_agent_rejects_empty_machine_only_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            agent = AgentConfig(provider="gemini", display_name="Gemini", model="gemini-test", bot=None)
            result = ProviderResult(
                provider="gemini",
                stdout='{"session_id":"abc","response":"","stats":{"tokens":{"total":123}}}',
                stderr="ClearcutLogger: Flush already in progress",
            )

            with patch("multicoders.app._run_provider_with_optional_cooldown", return_value=result):
                with self.assertRaises(ProviderError) as raised:
                    ask_chat_agent(
                        agent=agent,
                        sender_name="Jelitox",
                        user_message="hola",
                        prior_messages=[],
                        repo=repo,
                        timeout_sec=10,
                        dry_run=False,
                        run_id="run123",
                    )

            self.assertIn("no devolvió respuesta humana", raised.exception.summary)
            log_path = repo / ".multicoders" / "agent-logs" / "gemini" / "run123.jsonl"
            self.assertTrue(log_path.exists())

    def test_chat_agent_sends_only_human_response_from_verbose_provider_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            bot = _FakeBot()
            agent = AgentConfig(provider="claude", display_name="Claude", model="claude-test", bot=bot)
            result = ProviderResult(
                provider="claude",
                stdout=(
                    '{"session_id":"abc","response":"respuesta limpia",'
                    '"stats":{"tokens":{"total":123}}}'
                    "\nClearcutLogger: Flush already in progress, marking pending flush."
                ),
                stderr="",
            )

            with patch("multicoders.app._run_provider_with_optional_cooldown", return_value=result):
                answer = ask_chat_agent(
                    agent=agent,
                    sender_name="Jelitox",
                    user_message="hola",
                    prior_messages=[],
                    repo=repo,
                    timeout_sec=10,
                    dry_run=False,
                    run_id="run456",
                )

            self.assertEqual(answer, "respuesta limpia")
            self.assertEqual(len(bot.sent_messages), 1)
            sent = bot.sent_messages[0]
            self.assertIn("respuesta limpia", sent)
            self.assertNotIn("stats", sent)
            self.assertNotIn("session_id", sent)
            self.assertNotIn("ClearcutLogger", sent)
            log_path = repo / ".multicoders" / "agent-logs" / "claude" / "run456.jsonl"
            payload = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertIn("ClearcutLogger", payload["stdout"])


if __name__ == "__main__":
    unittest.main()
