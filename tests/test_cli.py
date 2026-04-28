from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from multicoders.app import (
    AgentConfig,
    TelegramState,
    load_env_file,
    main,
    parse_brainstorming_cancel_command,
    parse_brainstorming_status_command,
    parse_media_command,
    parse_chat_command,
    parse_random_task_command,
    parse_task_command,
    prepare_provider_args,
    process_service_commands,
)
from multicoders.providers import ProviderResult
from multicoders.storage import connect_db, get_task, init_db
from multicoders.telegram import TelegramMessage


class FakeTelegramBot:
    def __init__(self, chat_id: str, updates: list[TelegramMessage], *, name: str | None = None) -> None:
        self.name = name or "fake-bot"
        self.chat_id = chat_id
        self._updates = updates
        self.sent_messages: list[str] = []
        self.sent_photos: list[tuple[str, str | None]] = []
        self.sent_animations: list[tuple[str, str | None]] = []
        self.sent_stickers: list[str] = []
        self.requested_offsets: list[int] = []

    def get_updates(self, *, offset: int | None = None, timeout_sec: int = 0) -> list[TelegramMessage]:
        minimum = offset or 0
        self.requested_offsets.append(minimum)
        return [item for item in self._updates if item.update_id >= minimum]

    def send_message(self, text: str) -> None:
        self.sent_messages.append(text)

    def send_photo(self, photo: str, caption: str | None = None) -> None:
        self.sent_photos.append((photo, caption))

    def send_animation(self, animation: str, caption: str | None = None) -> None:
        self.sent_animations.append((animation, caption))

    def send_sticker(self, sticker: str) -> None:
        self.sent_stickers.append(sticker)


class SendTestMessagesCliTests(unittest.TestCase):
    def _default_agents(self) -> list[AgentConfig]:
        return [
            AgentConfig(provider="codex", display_name="codex-bot", model=None, bot=None),
            AgentConfig(provider="claude", display_name="claude-bot", model=None, bot=None),
            AgentConfig(provider="gemini", display_name="gemini-bot", model=None, bot=None),
        ]

    def test_parse_task_command_supports_plain_group_syntax(self) -> None:
        parsed = parse_task_command("/task repo=/tmp/repo type=bugfix corregir bug de login")
        self.assertEqual(parsed, ("/tmp/repo", "bugfix", "corregir bug de login"))

    def test_parse_task_command_supports_quoted_repo_and_type_case(self) -> None:
        parsed = parse_task_command('/task repo="/tmp/repo con espacios" type=Feature "preparar reporte" final')
        self.assertEqual(parsed, ("/tmp/repo con espacios", "feature", "preparar reporte final"))

    def test_parse_chat_command_supports_plain_and_group_syntax(self) -> None:
        self.assertEqual(parse_chat_command("/chat que es el sol?"), "que es el sol?")
        self.assertEqual(parse_chat_command("/chat@multicoders_codexitox_bot que es el sol?"), "que es el sol?")

    def test_parse_random_task_command_supports_group_syntax(self) -> None:
        parsed = parse_random_task_command(
            "/random-task@multicoders_codexitox_bot repo=/tmp/repo type=bugfix actualizar dependencias"
        )
        self.assertEqual(parsed, ("/tmp/repo", "bugfix", "actualizar dependencias"))

    def test_parse_media_command_supports_caption(self) -> None:
        self.assertEqual(parse_media_command("/gif shipit listo para prod"), ("gif", "shipit", "listo para prod"))
        self.assertEqual(parse_media_command('/meme@bot deploy "otra vez"'), ("meme", "deploy", "otra vez"))

    def test_parse_brainstorming_control_commands(self) -> None:
        self.assertTrue(parse_brainstorming_status_command("/brainstorming-status"))
        self.assertTrue(parse_brainstorming_cancel_command("/brainstorming-cancel"))

    def test_plain_task_and_approve_commands_work_without_bot_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = connect_db(Path(temp_dir) / "service.db")
            init_db(conn)
            state = TelegramState(state_file=Path(temp_dir) / "telegram-state.json", discussion_runs=[])
            updates = [
                TelegramMessage(
                    update_id=50,
                    chat_id="-100123",
                    chat_type="group",
                    chat_title="Multicoders",
                    sender_id="7",
                    sender_name="Jelitox",
                    is_bot=False,
                    text="/task repo=/tmp/repo type=bugfix corregir bug de login",
                    date=1710000000,
                ),
                TelegramMessage(
                    update_id=51,
                    chat_id="-100123",
                    chat_type="group",
                    chat_title="Multicoders",
                    sender_id="7",
                    sender_name="Jelitox",
                    is_bot=False,
                    text="/approve 1",
                    date=1710000001,
                ),
            ]
            bot = FakeTelegramBot(chat_id="-100123", updates=updates)

            process_service_commands(
                bot=bot,
                conn=conn,
                telegram_state=state,
                dry_run=False,
                providers=["codex", "claude", "gemini"],
                agents=self._default_agents(),
                provider_timeout_sec=30,
                conversation_repo=Path(temp_dir),
            )

            task = get_task(conn, 1)
            assert task is not None
            self.assertEqual(task.status, "approved")
            self.assertIsNone(task.lead_provider)
            self.assertEqual(state.last_update_id, 51)
            self.assertEqual(len(bot.sent_messages), 2)
            self.assertIn("Recibí una tarea nueva", bot.sent_messages[0])
            self.assertIn("La tarea fue aprobada", bot.sent_messages[1])

    def test_service_ignores_messages_from_other_topics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = connect_db(Path(temp_dir) / "service.db")
            init_db(conn)
            state = TelegramState(state_file=Path(temp_dir) / "telegram-state.json", discussion_runs=[])
            update = TelegramMessage(
                update_id=70,
                chat_id="-100123",
                chat_type="supergroup",
                chat_title="Multicoders",
                sender_id="7",
                sender_name="Jelitox",
                is_bot=False,
                text="/task repo=/tmp/repo type=bugfix no debe entrar",
                date=1710000000,
                message_thread_id=88,
            )
            bot = FakeTelegramBot(chat_id="-100123", updates=[update])
            bot.message_thread_id = 77

            process_service_commands(
                bot=bot,
                conn=conn,
                telegram_state=state,
                dry_run=False,
                providers=["codex", "claude", "gemini"],
                agents=self._default_agents(),
                provider_timeout_sec=30,
                conversation_repo=Path(temp_dir),
            )

            self.assertIsNone(get_task(conn, 1))
            self.assertEqual(bot.sent_messages, [])

    def test_random_task_command_assigns_random_lead_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = connect_db(Path(temp_dir) / "service.db")
            init_db(conn)
            state = TelegramState(state_file=Path(temp_dir) / "telegram-state.json", discussion_runs=[])
            update = TelegramMessage(
                update_id=41,
                chat_id="-100123",
                chat_type="group",
                chat_title="Multicoders",
                sender_id="7",
                sender_name="Jelitox",
                is_bot=False,
                text="/random-task@multicoders_codexitox_bot repo=/tmp/repo type=feature preparar changelog",
                date=1710000000,
            )
            bot = FakeTelegramBot(chat_id="-100123", updates=[update])

            with patch("multicoders.app.random.choice", return_value="claude"):
                process_service_commands(
                    bot=bot,
                    conn=conn,
                    telegram_state=state,
                    dry_run=False,
                    providers=["codex", "claude", "gemini"],
                    agents=self._default_agents(),
                    provider_timeout_sec=30,
                    conversation_repo=Path(temp_dir),
                )

            task = get_task(conn, 1)
            assert task is not None
            self.assertEqual(task.status, "pending")
            self.assertEqual(task.lead_provider, "claude")
            self.assertEqual(task.task_type, "feature")
            self.assertIn("lead: claude", bot.sent_messages[0])

    def test_plain_text_message_triggers_three_bot_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = connect_db(Path(temp_dir) / "service.db")
            init_db(conn)
            state = TelegramState(state_file=Path(temp_dir) / "telegram-state.json", discussion_runs=[])
            update = TelegramMessage(
                update_id=61,
                chat_id="-100123",
                chat_type="group",
                chat_title="Multicoders",
                sender_id="7",
                sender_name="Jelitox",
                is_bot=False,
                text="que es el sol?",
                date=1710000010,
            )
            observer_bot = FakeTelegramBot(chat_id="-100123", updates=[update])
            agent_bots = [
                FakeTelegramBot(chat_id="-100123", updates=[]),
                FakeTelegramBot(chat_id="-100123", updates=[]),
                FakeTelegramBot(chat_id="-100123", updates=[]),
            ]
            agents = [
                AgentConfig(provider="codex", display_name="codex-bot", model=None, bot=agent_bots[0]),
                AgentConfig(provider="claude", display_name="claude-bot", model=None, bot=agent_bots[1]),
                AgentConfig(provider="gemini", display_name="gemini-bot", model=None, bot=agent_bots[2]),
            ]
            prompts: list[str] = []
            outputs = [
                "El sol es una estrella.",
                "Sumo algo: también es la fuente principal de energía de la Tierra.",
                "Complemento: su gravedad mantiene a los planetas en órbita.",
            ]

            def fake_run_provider(provider_name, prompt, repo, model, timeout_sec):
                prompts.append(prompt)
                return ProviderResult(provider=provider_name, stdout=outputs[len(prompts) - 1], stderr="")

            with patch("multicoders.app.run_provider", side_effect=fake_run_provider):
                process_service_commands(
                    bot=observer_bot,
                    conn=conn,
                    telegram_state=state,
                    dry_run=False,
                    providers=["codex", "claude", "gemini"],
                    agents=agents,
                    provider_timeout_sec=30,
                    conversation_repo=Path(temp_dir),
                )

            self.assertEqual(len(observer_bot.sent_messages), 1)
            self.assertIn("Jelitox preguntó: que es el sol?", observer_bot.sent_messages[0])
            self.assertTrue(agent_bots[0].sent_messages[0].endswith(outputs[0]))
            self.assertTrue(agent_bots[1].sent_messages[0].endswith(outputs[1]))
            self.assertTrue(agent_bots[2].sent_messages[0].endswith(outputs[2]))
            self.assertIn("No previous agent messages yet.", prompts[0])
            self.assertIn(outputs[0], prompts[1])
            self.assertIn(outputs[0], prompts[2])
            self.assertIn(outputs[1], prompts[2])
            assert state.bot_chat is not None
            self.assertTrue(state.bot_chat["active"])
            self.assertEqual(state.bot_chat["next_provider_index"], 0)
            self.assertEqual(len(state.bot_chat["transcript"]), 3)

    def test_chat_command_triggers_three_bot_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = connect_db(Path(temp_dir) / "service.db")
            init_db(conn)
            state = TelegramState(state_file=Path(temp_dir) / "telegram-state.json", discussion_runs=[])
            update = TelegramMessage(
                update_id=62,
                chat_id="-100123",
                chat_type="group",
                chat_title="Multicoders",
                sender_id="7",
                sender_name="Jelitox",
                is_bot=False,
                text="/chat que es el sol?",
                date=1710000011,
            )
            observer_bot = FakeTelegramBot(chat_id="-100123", updates=[update])
            agent_bots = [
                FakeTelegramBot(chat_id="-100123", updates=[]),
                FakeTelegramBot(chat_id="-100123", updates=[]),
                FakeTelegramBot(chat_id="-100123", updates=[]),
            ]
            agents = [
                AgentConfig(provider="codex", display_name="codex-bot", model=None, bot=agent_bots[0]),
                AgentConfig(provider="claude", display_name="claude-bot", model=None, bot=agent_bots[1]),
                AgentConfig(provider="gemini", display_name="gemini-bot", model=None, bot=agent_bots[2]),
            ]

            def fake_run_provider(provider_name, prompt, repo, model, timeout_sec):
                return ProviderResult(provider=provider_name, stdout=f"respuesta de {provider_name}", stderr="")

            with patch("multicoders.app.run_provider", side_effect=fake_run_provider):
                process_service_commands(
                    bot=observer_bot,
                    conn=conn,
                    telegram_state=state,
                    dry_run=False,
                    providers=["codex", "claude", "gemini"],
                    agents=agents,
                    provider_timeout_sec=30,
                    conversation_repo=Path(temp_dir),
                )

            self.assertEqual(len(agent_bots[0].sent_messages), 1)
            self.assertEqual(len(agent_bots[1].sent_messages), 1)
            self.assertEqual(len(agent_bots[2].sent_messages), 1)
            self.assertIsNotNone(state.bot_chat)

    def test_chat_response_can_send_configured_gif_and_emoji(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = connect_db(Path(temp_dir) / "service.db")
            init_db(conn)
            state = TelegramState(state_file=Path(temp_dir) / "telegram-state.json", discussion_runs=[])
            update = TelegramMessage(
                update_id=64,
                chat_id="-100123",
                chat_type="group",
                chat_title="Multicoders",
                sender_id="7",
                sender_name="Jelitox",
                is_bot=False,
                text="/chat modo meme",
                date=1710000013,
            )
            observer_bot = FakeTelegramBot(chat_id="-100123", updates=[update])
            agent_bots = [
                FakeTelegramBot(chat_id="-100123", updates=[]),
                FakeTelegramBot(chat_id="-100123", updates=[]),
                FakeTelegramBot(chat_id="-100123", updates=[]),
            ]
            agents = [
                AgentConfig(provider="codex", display_name="codex-bot", model=None, bot=agent_bots[0]),
                AgentConfig(provider="claude", display_name="claude-bot", model=None, bot=agent_bots[1]),
                AgentConfig(provider="gemini", display_name="gemini-bot", model=None, bot=agent_bots[2]),
            ]
            outputs = [
                "Esto pide un deploy con casco.\n[emoji:\U0001f605]\n[gif:shipit]",
                "Aporto el disclaimer legal.",
                "Cierro con contexto.",
            ]

            def fake_run_provider(provider_name, prompt, repo, model, timeout_sec):
                self.assertIn("Available GIF keys: shipit", prompt)
                return ProviderResult(provider=provider_name, stdout=outputs.pop(0), stderr="")

            with patch.dict(os.environ, {"MULTICODERS_GIF_ASSETS": "shipit=https://example.test/shipit.gif"}):
                with patch("multicoders.app.run_provider", side_effect=fake_run_provider):
                    process_service_commands(
                        bot=observer_bot,
                        conn=conn,
                        telegram_state=state,
                        dry_run=False,
                        providers=["codex", "claude", "gemini"],
                        agents=agents,
                        provider_timeout_sec=30,
                        conversation_repo=Path(temp_dir),
                    )

            self.assertIn("\U0001f605", agent_bots[0].sent_messages[0])
            run_prefix = agent_bots[0].sent_messages[0].split("] ", 1)[0] + "]"
            self.assertEqual(agent_bots[0].sent_animations, [("https://example.test/shipit.gif", f"{run_prefix} Esto pide un deploy con casco.")])

    def test_media_command_sends_configured_meme(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = connect_db(Path(temp_dir) / "service.db")
            init_db(conn)
            state = TelegramState(state_file=Path(temp_dir) / "telegram-state.json", discussion_runs=[])
            update = TelegramMessage(
                update_id=65,
                chat_id="-100123",
                chat_type="group",
                chat_title="Multicoders",
                sender_id="7",
                sender_name="Jelitox",
                is_bot=False,
                text="/meme deploy esto compila en mi maquina",
                date=1710000014,
            )
            observer_bot = FakeTelegramBot(chat_id="-100123", updates=[update])
            codex_bot = FakeTelegramBot(chat_id="-100123", updates=[])
            agents = [
                AgentConfig(provider="codex", display_name="codex-bot", model=None, bot=codex_bot),
                AgentConfig(provider="claude", display_name="claude-bot", model=None, bot=FakeTelegramBot(chat_id="-100123", updates=[])),
                AgentConfig(provider="gemini", display_name="gemini-bot", model=None, bot=FakeTelegramBot(chat_id="-100123", updates=[])),
            ]

            with patch.dict(os.environ, {"MULTICODERS_MEME_ASSETS": "deploy=https://example.test/deploy.jpg"}):
                with patch("multicoders.app.random.choice", return_value=agents[0]):
                    process_service_commands(
                        bot=observer_bot,
                        conn=conn,
                        telegram_state=state,
                        dry_run=False,
                        providers=["codex", "claude", "gemini"],
                        agents=agents,
                        provider_timeout_sec=30,
                        conversation_repo=Path(temp_dir),
                    )

            self.assertEqual(codex_bot.sent_photos, [("https://example.test/deploy.jpg", "esto compila en mi maquina")])

    def test_active_bot_chat_advances_one_turn_without_new_updates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = connect_db(Path(temp_dir) / "service.db")
            init_db(conn)
            state = TelegramState(
                state_file=Path(temp_dir) / "telegram-state.json",
                discussion_runs=[],
                bot_chat={
                    "active": True,
                    "run_id": "chat-active",
                    "sender_name": "Jelitox",
                    "seed_message": "hablen de arquitectura",
                    "next_provider_index": 1,
                    "transcript": [{"speaker": "codex-bot", "text": "Yo arrancaria por el contrato."}],
                },
            )
            observer_bot = FakeTelegramBot(chat_id="-100123", updates=[])
            agent_bots = [
                FakeTelegramBot(chat_id="-100123", updates=[]),
                FakeTelegramBot(chat_id="-100123", updates=[]),
                FakeTelegramBot(chat_id="-100123", updates=[]),
            ]
            agents = [
                AgentConfig(provider="codex", display_name="codex-bot", model=None, bot=agent_bots[0]),
                AgentConfig(provider="claude", display_name="claude-bot", model=None, bot=agent_bots[1]),
                AgentConfig(provider="gemini", display_name="gemini-bot", model=None, bot=agent_bots[2]),
            ]

            def fake_run_provider(provider_name, prompt, repo, model, timeout_sec):
                self.assertEqual(provider_name, "claude")
                self.assertIn("Yo arrancaria por el contrato.", prompt)
                return ProviderResult(provider=provider_name, stdout="Claude responde al punto de Codex.", stderr="")

            with patch("multicoders.app.run_provider", side_effect=fake_run_provider):
                process_service_commands(
                    bot=observer_bot,
                    conn=conn,
                    telegram_state=state,
                    dry_run=False,
                    providers=["codex", "claude", "gemini"],
                    agents=agents,
                    provider_timeout_sec=30,
                    conversation_repo=Path(temp_dir),
                )

            self.assertEqual(agent_bots[0].sent_messages, [])
            self.assertTrue(agent_bots[1].sent_messages[0].endswith("Claude responde al punto de Codex."))
            self.assertEqual(agent_bots[2].sent_messages, [])
            assert state.bot_chat is not None
            self.assertEqual(state.bot_chat["next_provider_index"], 2)
            self.assertEqual(len(state.bot_chat["transcript"]), 2)

    def test_chat_response_unwraps_provider_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = connect_db(Path(temp_dir) / "service.db")
            init_db(conn)
            state = TelegramState(state_file=Path(temp_dir) / "telegram-state.json", discussion_runs=[])
            update = TelegramMessage(
                update_id=68,
                chat_id="-100123",
                chat_type="group",
                chat_title="Multicoders",
                sender_id="7",
                sender_name="Jelitox",
                is_bot=False,
                text="/chat respondan corto",
                date=1710000017,
            )
            observer_bot = FakeTelegramBot(chat_id="-100123", updates=[update])
            agent_bots = [
                FakeTelegramBot(chat_id="-100123", updates=[]),
                FakeTelegramBot(chat_id="-100123", updates=[]),
                FakeTelegramBot(chat_id="-100123", updates=[]),
            ]
            agents = [
                AgentConfig(provider="codex", display_name="codex-bot", model=None, bot=agent_bots[0]),
                AgentConfig(provider="claude", display_name="claude-bot", model=None, bot=agent_bots[1]),
                AgentConfig(provider="gemini", display_name="gemini-bot", model=None, bot=agent_bots[2]),
            ]

            def fake_run_provider(provider_name, prompt, repo, model, timeout_sec):
                return ProviderResult(provider=provider_name, stdout=f'{{"response":"respuesta de {provider_name}"}}', stderr="")

            with patch("multicoders.app.run_provider", side_effect=fake_run_provider):
                process_service_commands(
                    bot=observer_bot,
                    conn=conn,
                    telegram_state=state,
                    dry_run=False,
                    providers=["codex", "claude", "gemini"],
                    agents=agents,
                    provider_timeout_sec=30,
                    conversation_repo=Path(temp_dir),
                )

            self.assertTrue(agent_bots[0].sent_messages[0].endswith("respuesta de codex"))
            self.assertTrue(agent_bots[1].sent_messages[0].endswith("respuesta de claude"))
            self.assertTrue(agent_bots[2].sent_messages[0].endswith("respuesta de gemini"))

    def test_active_bot_chat_agent_failure_skips_to_next_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = connect_db(Path(temp_dir) / "service.db")
            init_db(conn)
            state = TelegramState(
                state_file=Path(temp_dir) / "telegram-state.json",
                discussion_runs=[],
                bot_chat={
                    "active": True,
                    "run_id": "chat-active",
                    "sender_name": "Jelitox",
                    "seed_message": "hablen de arquitectura",
                    "next_provider_index": 2,
                    "transcript": [{"speaker": "claude-bot", "text": "Claude dejo contexto."}],
                },
            )
            observer_bot = FakeTelegramBot(chat_id="-100123", updates=[])
            agent_bots = [
                FakeTelegramBot(chat_id="-100123", updates=[]),
                FakeTelegramBot(chat_id="-100123", updates=[]),
                FakeTelegramBot(chat_id="-100123", updates=[]),
            ]
            agents = [
                AgentConfig(provider="codex", display_name="codex-bot", model=None, bot=agent_bots[0]),
                AgentConfig(provider="claude", display_name="claude-bot", model=None, bot=agent_bots[1]),
                AgentConfig(provider="gemini", display_name="gemini-bot", model=None, bot=agent_bots[2]),
            ]
            calls: list[str] = []

            def fake_run_provider(provider_name, prompt, repo, model, timeout_sec):
                calls.append(provider_name)
                if provider_name == "gemini":
                    raise RuntimeError("gemini timeout")
                self.assertEqual(provider_name, "codex")
                self.assertIn("Claude dejo contexto.", prompt)
                return ProviderResult(provider=provider_name, stdout="Codex sigue sin esperar a Gemini.", stderr="")

            with patch("multicoders.app.run_provider", side_effect=fake_run_provider):
                process_service_commands(
                    bot=observer_bot,
                    conn=conn,
                    telegram_state=state,
                    dry_run=False,
                    providers=["codex", "claude", "gemini"],
                    agents=agents,
                    provider_timeout_sec=30,
                    conversation_repo=Path(temp_dir),
                )

            self.assertEqual(calls, ["gemini", "codex"])
            self.assertTrue(agent_bots[0].sent_messages[0].endswith("Codex sigue sin esperar a Gemini."))
            self.assertEqual(agent_bots[1].sent_messages, [])
            self.assertEqual(agent_bots[2].sent_messages, [])
            self.assertTrue(any("gemini-bot no pudo responder" in message for message in observer_bot.sent_messages))
            assert state.bot_chat is not None
            self.assertEqual(state.bot_chat["next_provider_index"], 1)
            self.assertEqual(len(state.bot_chat["transcript"]), 2)

    def test_human_message_during_active_chat_triggers_full_new_round(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = connect_db(Path(temp_dir) / "service.db")
            init_db(conn)
            state = TelegramState(
                state_file=Path(temp_dir) / "telegram-state.json",
                discussion_runs=[],
                bot_chat={
                    "active": True,
                    "run_id": "chat-active",
                    "sender_name": "Jelitox",
                    "seed_message": "hablen de arquitectura",
                    "next_provider_index": 1,
                    "transcript": [{"speaker": "codex-bot", "text": "Antes deciamos contrato."}],
                },
            )
            update = TelegramMessage(
                update_id=66,
                chat_id="-100123",
                chat_type="group",
                chat_title="Multicoders",
                sender_id="7",
                sender_name="Jelitox",
                is_bot=False,
                text="ahora respondan sobre latencia",
                date=1710000015,
            )
            observer_bot = FakeTelegramBot(chat_id="-100123", updates=[update])
            agent_bots = [
                FakeTelegramBot(chat_id="-100123", updates=[]),
                FakeTelegramBot(chat_id="-100123", updates=[]),
                FakeTelegramBot(chat_id="-100123", updates=[]),
            ]
            agents = [
                AgentConfig(provider="codex", display_name="codex-bot", model=None, bot=agent_bots[0]),
                AgentConfig(provider="claude", display_name="claude-bot", model=None, bot=agent_bots[1]),
                AgentConfig(provider="gemini", display_name="gemini-bot", model=None, bot=agent_bots[2]),
            ]
            prompts: list[str] = []
            outputs = ["Codex sobre latencia.", "Claude suma colas.", "Gemini cierra con metricas."]

            def fake_run_provider(provider_name, prompt, repo, model, timeout_sec):
                prompts.append(prompt)
                return ProviderResult(provider=provider_name, stdout=outputs[len(prompts) - 1], stderr="")

            with patch("multicoders.app.run_provider", side_effect=fake_run_provider):
                process_service_commands(
                    bot=observer_bot,
                    conn=conn,
                    telegram_state=state,
                    dry_run=False,
                    providers=["codex", "claude", "gemini"],
                    agents=agents,
                    provider_timeout_sec=30,
                    conversation_repo=Path(temp_dir),
                )

            self.assertEqual(len(agent_bots[0].sent_messages), 1)
            self.assertEqual(len(agent_bots[1].sent_messages), 1)
            self.assertEqual(len(agent_bots[2].sent_messages), 1)
            self.assertIn("Antes deciamos contrato.", prompts[0])
            self.assertIn("ahora respondan sobre latencia", prompts[0])
            self.assertIn(outputs[0], prompts[1])
            assert state.bot_chat is not None
            self.assertEqual(state.bot_chat["seed_message"], "ahora respondan sobre latencia")
            self.assertEqual(state.bot_chat["next_provider_index"], 0)
            self.assertEqual(len(state.bot_chat["transcript"]), 4)

    def test_chat_agent_failure_does_not_block_remaining_bots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = connect_db(Path(temp_dir) / "service.db")
            init_db(conn)
            state = TelegramState(state_file=Path(temp_dir) / "telegram-state.json", discussion_runs=[])
            update = TelegramMessage(
                update_id=67,
                chat_id="-100123",
                chat_type="group",
                chat_title="Multicoders",
                sender_id="7",
                sender_name="Jelitox",
                is_bot=False,
                text="/chat revisen el plan",
                date=1710000016,
            )
            observer_bot = FakeTelegramBot(chat_id="-100123", updates=[update])
            agent_bots = [
                FakeTelegramBot(chat_id="-100123", updates=[]),
                FakeTelegramBot(chat_id="-100123", updates=[]),
                FakeTelegramBot(chat_id="-100123", updates=[]),
            ]
            agents = [
                AgentConfig(provider="codex", display_name="codex-bot", model=None, bot=agent_bots[0]),
                AgentConfig(provider="claude", display_name="claude-bot", model=None, bot=agent_bots[1]),
                AgentConfig(provider="gemini", display_name="gemini-bot", model=None, bot=agent_bots[2]),
            ]

            def fake_run_provider(provider_name, prompt, repo, model, timeout_sec):
                if provider_name == "claude":
                    raise RuntimeError("claude unavailable")
                return ProviderResult(provider=provider_name, stdout=f"respuesta de {provider_name}", stderr="")

            with patch("multicoders.app.run_provider", side_effect=fake_run_provider):
                process_service_commands(
                    bot=observer_bot,
                    conn=conn,
                    telegram_state=state,
                    dry_run=False,
                    providers=["codex", "claude", "gemini"],
                    agents=agents,
                    provider_timeout_sec=30,
                    conversation_repo=Path(temp_dir),
                )

            self.assertTrue(agent_bots[0].sent_messages[0].endswith("respuesta de codex"))
            self.assertEqual(agent_bots[1].sent_messages, [])
            self.assertTrue(agent_bots[2].sent_messages[0].endswith("respuesta de gemini"))
            self.assertTrue(any("claude-bot no pudo responder" in message for message in observer_bot.sent_messages))
            assert state.bot_chat is not None
            self.assertEqual(len(state.bot_chat["transcript"]), 2)

    def test_silencio_stops_active_bot_chat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = connect_db(Path(temp_dir) / "service.db")
            init_db(conn)
            state = TelegramState(
                state_file=Path(temp_dir) / "telegram-state.json",
                discussion_runs=[],
                bot_chat={
                    "active": True,
                    "run_id": "chat-active",
                    "sender_name": "Jelitox",
                    "seed_message": "hablen",
                    "next_provider_index": 0,
                    "transcript": [{"speaker": "gemini-bot", "text": "Sigo."}],
                },
            )
            update = TelegramMessage(
                update_id=63,
                chat_id="-100123",
                chat_type="group",
                chat_title="Multicoders",
                sender_id="7",
                sender_name="Jelitox",
                is_bot=False,
                text="silencio",
                date=1710000012,
            )
            observer_bot = FakeTelegramBot(chat_id="-100123", updates=[update])
            agent_bots = [
                FakeTelegramBot(chat_id="-100123", updates=[]),
                FakeTelegramBot(chat_id="-100123", updates=[]),
                FakeTelegramBot(chat_id="-100123", updates=[]),
            ]
            agents = [
                AgentConfig(provider="codex", display_name="codex-bot", model=None, bot=agent_bots[0]),
                AgentConfig(provider="claude", display_name="claude-bot", model=None, bot=agent_bots[1]),
                AgentConfig(provider="gemini", display_name="gemini-bot", model=None, bot=agent_bots[2]),
            ]

            with patch("multicoders.app.run_provider", side_effect=AssertionError("should not advance")):
                process_service_commands(
                    bot=observer_bot,
                    conn=conn,
                    telegram_state=state,
                    dry_run=False,
                    providers=["codex", "claude", "gemini"],
                    agents=agents,
                    provider_timeout_sec=30,
                    conversation_repo=Path(temp_dir),
                )

            self.assertIsNone(state.bot_chat)
            self.assertIn("silencio recibido", observer_bot.sent_messages[0])
            self.assertEqual(agent_bots[0].sent_messages, [])
            self.assertEqual(agent_bots[1].sent_messages, [])
            self.assertEqual(agent_bots[2].sent_messages, [])

    def test_listener_offsets_are_independent_per_bot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = connect_db(Path(temp_dir) / "service.db")
            init_db(conn)
            state = TelegramState(
                state_file=Path(temp_dir) / "telegram-state.json",
                last_update_id=999999999,
                discussion_runs=[],
                bot_offsets={"multicoders_clauditox_bot": 121848036},
            )
            codex_bot = FakeTelegramBot(chat_id="-100123", updates=[], name="multicoders_codexitox_bot")
            claude_bot = FakeTelegramBot(chat_id="-100123", updates=[], name="multicoders_clauditox_bot")
            gemini_bot = FakeTelegramBot(chat_id="-100123", updates=[], name="multicoders_geminitox_bot")
            agents = [
                AgentConfig(provider="codex", display_name="codex-bot", model=None, bot=codex_bot),
                AgentConfig(provider="claude", display_name="claude-bot", model=None, bot=claude_bot),
                AgentConfig(provider="gemini", display_name="gemini-bot", model=None, bot=gemini_bot),
            ]

            process_service_commands(
                bot=codex_bot,
                conn=conn,
                telegram_state=state,
                dry_run=False,
                providers=["codex", "claude", "gemini"],
                agents=agents,
                provider_timeout_sec=30,
                conversation_repo=Path(temp_dir),
            )

            self.assertEqual(codex_bot.requested_offsets[0], 1)
            self.assertEqual(claude_bot.requested_offsets[0], 121848037)
            self.assertEqual(gemini_bot.requested_offsets[0], 1)

    def test_send_test_messages_dry_run_prints_three_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BOT_GEMINI=gemini-bot",
                        "BOT_GEMINI_KEY=token-gemini",
                        "BOT_CODEX=codex-bot",
                        "BOT_CODEX_KEY=token-codex",
                        "BOT_CLAUDE=claude-bot",
                        "BOT_CLAUDE_KEY=token-claude",
                        "TELEGRAM_GROUP=-100123",
                        "TELEGRAM_TOPIC_ID=77",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            captured = io.StringIO()
            with patch.dict(os.environ, {}, clear=True):
                with redirect_stdout(captured):
                    exit_code = main(
                        [
                            "send-test-messages",
                            "--env-file",
                            str(env_file),
                            "--message",
                            "hello group",
                            "--dry-run",
                        ]
                    )

            self.assertEqual(exit_code, 0)
            output = captured.getvalue().splitlines()
            self.assertTrue(output[0].startswith("test_messages:"))
            payload = json.loads("\n".join(output[1:-1]))
            self.assertEqual(len(payload), 3)
            providers = [item["provider"] for item in payload]
            self.assertEqual(providers, ["codex", "claude", "gemini"])
            run_ids = {item["text"].split("] ", 1)[0] for item in payload}
            self.assertEqual(len(run_ids), 1)
            for item in payload:
                self.assertEqual(item["chat_id"], "-100123")
                self.assertEqual(item["message_thread_id"], "77")
                self.assertIn("hello group", item["text"])

    def test_discover_telegram_chat_dry_run_prints_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BOT_GEMINI=gemini-bot",
                        "BOT_GEMINI_KEY=token-gemini",
                        "BOT_CODEX=codex-bot",
                        "BOT_CODEX_KEY=token-codex",
                        "BOT_CLAUDE=claude-bot",
                        "BOT_CLAUDE_KEY=token-claude",
                        "TELEGRAM_GROUP=-100123",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            captured = io.StringIO()
            with patch.dict(os.environ, {}, clear=True):
                with redirect_stdout(captured):
                    exit_code = main(
                        [
                            "discover-telegram-chat",
                            "--env-file",
                            str(env_file),
                            "--dry-run",
                        ]
                    )

            self.assertEqual(exit_code, 0)
            output = captured.getvalue().splitlines()
            self.assertTrue(output[0].startswith("telegram_discovery:"))
            payload = json.loads("\n".join(output[1:]))
            self.assertEqual(payload["providers_checked"], ["codex", "claude", "gemini"])
            self.assertEqual(len(payload["per_provider"]), 3)
            self.assertEqual(len(payload["combined_candidates"]), 2)
            self.assertEqual(payload["recommended_env"][0]["TELEGRAM_GROUP"], "-1001234567890")

    def test_prepare_provider_args_dry_run_does_not_require_binaries(self) -> None:
        args = type(
            "Args",
            (),
            {"env_file": "/tmp/does-not-exist.env", "providers": "codex,claude,gemini", "dry_run": True},
        )()

        with patch("multicoders.app.available_providers", side_effect=AssertionError("should not check binaries")):
            prepare_provider_args(args)

        self.assertEqual(args.providers, ["codex", "claude", "gemini"])

    def test_dry_run_accepts_non_git_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            repo.mkdir()
            (repo / "README.md").write_text("sample\n", encoding="utf-8")
            state_file = Path(temp_dir) / "state.json"
            captured = io.StringIO()

            with redirect_stdout(captured):
                exit_code = main(
                    [
                        "--repo",
                        str(repo),
                        "--task",
                        "smoke",
                        "--dry-run",
                        "--no-telegram",
                        "--telegram-state-file",
                        str(state_file),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("winner: codex-solution", captured.getvalue())

    def test_load_env_file_supports_export_and_inline_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "export BOT_CODEX=codex-bot # local name",
                        "BOT_CLAUDE='claude # not a comment'",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                load_env_file(env_file)
                self.assertEqual(os.environ["BOT_CODEX"], "codex-bot")
                self.assertEqual(os.environ["BOT_CLAUDE"], "claude # not a comment")


if __name__ == "__main__":
    unittest.main()
