from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

MAX_TELEGRAM_MESSAGE_LENGTH = 4096
MAX_TELEGRAM_CAPTION_LENGTH = 1024


def split_telegram_text(text: str, max_length: int = MAX_TELEGRAM_MESSAGE_LENGTH) -> list[str]:
    if max_length <= 0:
        raise ValueError("max_length must be positive")
    if not text:
        return [" "]
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > max_length:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:max_length])
            line = line[max_length:]
        if len(current) + len(line) > max_length:
            if current:
                chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks


def trim_telegram_caption(caption: str | None, max_length: int = MAX_TELEGRAM_CAPTION_LENGTH) -> str | None:
    if caption is None:
        return None
    compact = caption.strip()
    if not compact:
        return None
    if len(compact) <= max_length:
        return compact
    return compact[: max_length - 3] + "..."


@dataclass(slots=True)
class TelegramMessage:
    update_id: int
    chat_id: str
    chat_type: str
    chat_title: str
    sender_id: str
    sender_name: str
    is_bot: bool
    text: str
    date: int
    message_thread_id: int | None = None


class TelegramError(RuntimeError):
    pass


@dataclass(slots=True)
class TelegramBot:
    name: str
    token: str
    chat_id: str
    message_thread_id: int | None = None

    def _call_api(self, method: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = urllib.parse.urlencode(payload).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read().decode("utf-8")
            except Exception:
                raw = ""
            details = raw.strip() or exc.reason or str(exc)
            raise TelegramError(
                f"Telegram API call failed for {self.name}: {method} "
                f"(HTTP {exc.code}) {details}"
            ) from exc
        except urllib.error.URLError as exc:
            raise TelegramError(f"Telegram API call failed for {self.name}: {method} ({exc.reason})") from exc

        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TelegramError(f"Telegram returned invalid JSON for {self.name}") from exc
        if not result.get("ok"):
            raise TelegramError(f"Telegram rejected {method} for {self.name}: {result}")
        return result

    def send_message(self, text: str) -> None:
        for chunk in split_telegram_text(text):
            payload: dict[str, object] = {
                "chat_id": self.chat_id,
                "text": chunk,
                "disable_web_page_preview": "true",
            }
            if self.message_thread_id is not None:
                payload["message_thread_id"] = self.message_thread_id
            self._call_api("sendMessage", payload)

    def send_photo(self, photo: str, caption: str | None = None) -> None:
        payload: dict[str, object] = {
            "chat_id": self.chat_id,
            "photo": photo,
        }
        trimmed_caption = trim_telegram_caption(caption)
        if trimmed_caption is not None:
            payload["caption"] = trimmed_caption
        if self.message_thread_id is not None:
            payload["message_thread_id"] = self.message_thread_id
        self._call_api("sendPhoto", payload)

    def send_animation(self, animation: str, caption: str | None = None) -> None:
        payload: dict[str, object] = {
            "chat_id": self.chat_id,
            "animation": animation,
        }
        trimmed_caption = trim_telegram_caption(caption)
        if trimmed_caption is not None:
            payload["caption"] = trimmed_caption
        if self.message_thread_id is not None:
            payload["message_thread_id"] = self.message_thread_id
        self._call_api("sendAnimation", payload)

    def send_sticker(self, sticker: str) -> None:
        payload: dict[str, object] = {
            "chat_id": self.chat_id,
            "sticker": sticker,
        }
        if self.message_thread_id is not None:
            payload["message_thread_id"] = self.message_thread_id
        self._call_api("sendSticker", payload)

    def get_updates(self, *, offset: int | None = None, timeout_sec: int = 0) -> list[TelegramMessage]:
        payload: dict[str, object] = {"timeout": max(timeout_sec, 0)}
        if offset is not None:
            payload["offset"] = offset
        result = self._call_api("getUpdates", payload)
        updates = result.get("result")
        if not isinstance(updates, list):
            return []
        messages: list[TelegramMessage] = []
        for item in updates:
            if not isinstance(item, dict):
                continue
            message = item.get("message") or item.get("edited_message")
            if not isinstance(message, dict):
                continue
            chat = message.get("chat")
            sender = message.get("from")
            text = message.get("text")
            update_id = item.get("update_id")
            date = message.get("date")
            if not isinstance(chat, dict) or not isinstance(sender, dict) or not isinstance(text, str):
                continue
            if not isinstance(update_id, int) or not isinstance(date, int):
                continue
            messages.append(
                TelegramMessage(
                    update_id=update_id,
                    chat_id=str(chat.get("id", "")),
                    chat_type=str(chat.get("type", "")),
                    chat_title=str(chat.get("title") or chat.get("username") or chat.get("first_name") or ""),
                    sender_id=str(sender.get("id", "")),
                    sender_name=str(sender.get("username") or sender.get("first_name") or "unknown"),
                    is_bot=bool(sender.get("is_bot")),
                    text=text.strip(),
                    date=date,
                    message_thread_id=message.get("message_thread_id") if isinstance(message.get("message_thread_id"), int) else None,
                )
            )
        return messages

    def message_matches_scope(self, message: TelegramMessage) -> bool:
        if message.chat_id != self.chat_id:
            return False
        if self.message_thread_id is None:
            return True
        return message.message_thread_id == self.message_thread_id

    def wait_for_human_messages(
        self,
        *,
        baseline_update_id: int,
        wait_sec: int,
        max_messages: int,
        ignored_sender_names: set[str],
    ) -> list[TelegramMessage]:
        deadline = time.time() + max(wait_sec, 0)
        offset = baseline_update_id + 1
        messages: list[TelegramMessage] = []
        while time.time() < deadline and len(messages) < max_messages:
            timeout = max(1, min(15, int(deadline - time.time())))
            updates = self.get_updates(offset=offset, timeout_sec=timeout)
            if not updates:
                continue
            for update in updates:
                offset = max(offset, update.update_id + 1)
                if not self.message_matches_scope(update):
                    continue
                if update.is_bot or not update.text:
                    continue
                if update.sender_name in ignored_sender_names:
                    continue
                messages.append(update)
                if len(messages) >= max_messages:
                    break
        return messages
