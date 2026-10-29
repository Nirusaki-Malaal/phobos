"""One long-polling bot instance; persistent updates, replies and channel delivery."""

import logging
import time
from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from phobos.config import Settings
from phobos.models import TicketCreate
from phobos.store import Conflict, Store, report_text
from phobos.workspace import project_path

log = logging.getLogger(__name__)
HELP = (
    "Phobos · your personal AI helper\n"
    "Send a message to create an assistant ticket.\n"
    "/new PROJECT PROMPT · /note TEXT\n"
    "/tasks · /status ID · /result ID\n"
    "/pause ID · /resume ID · /cancel ID · /retry ID\n"
    "/remember TEXT · /memories · /forget ID · /report\n"
    "/recall QUERY · /skills QUERY\n"
    "Memory commands use the configured default project."
)


class Telegram:
    def __init__(self, settings: Settings, store: Store, client: httpx.Client):
        if not settings.telegram_token or not settings.allowed_users:
            raise ValueError(
                "Telegram needs a bot token and at least one numeric authorized user ID"
            )
        project_path(settings.workspace_dir, settings.telegram_project)
        self.settings, self.store, self.client = settings, store, client

    def call(self, method: str, payload: dict) -> object:
        response = self.client.post(
            f"https://api.telegram.org/bot{self.settings.telegram_token}/{method}",
            json=payload,
            timeout=40,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError("Telegram rejected request")
        return body.get("result")

    def handle(self, update: dict) -> None:
        message = update.get("message", {})
        user = message.get("from", {}).get("id")
        chat = message.get("chat", {})
        if user not in self.settings.allowed_users or chat.get("type") != "private":
            return
        text = message.get("text", "").strip()
        try:
            reply = self.command(text, update["update_id"])
        except (ValueError, KeyError, Conflict, ValidationError):
            reply = "Request invalid or unavailable. Use /help for commands and valid ticket IDs."
        self.store.enqueue_message(f"reply:{update['update_id']}", reply, str(chat["id"]))

    def command(self, text: str, update_id: int) -> str:
        project = self.settings.telegram_project
        command, _, argument = text.partition(" ")
        command = command.split("@")[0].lower()
        argument = argument.strip()
        if command in {"/start", "/help"}:
            return HELP
        if not text:
            return "This version accepts text. Voice and media workflows are on the roadmap."
        if command == "/report":
            return report_text(self.store.report())
        if command == "/recall":
            from phobos.memory import GraphMemory

            result = GraphMemory(self.settings, self.store).recall(project, argument)
            return (
                "\n\n".join(
                    f"{row['label']} ({row.get('source', '')})\n" + row.get("description", "")[:800]
                    for row in result["matches"][:5]
                )
                or "No matching graph memories"
            )
        if command == "/skills":
            from phobos.skills import SkillLibrary

            rows = SkillLibrary(self.settings).search(argument or "coding", project)
            return (
                "\n".join(
                    f"{row['name']} · " + ("installed" if row["installed"] else "available online")
                    for row in rows
                )
                or "No matching skills"
            )
        if command == "/tasks":
            tickets = self.store.list(limit=15)
            return (
                "\n".join(f"#{t['id']} [{t['status']}] {t['title']}" for t in tickets)
                or "No tickets"
            )
        if command in {"/status", "/result"}:
            ticket = self.store.get(int(argument))
            if command == "/result":
                return ticket["result"] or ticket["error"] or "No result yet"
            return f"#{ticket['id']} · {ticket['status']}\n{ticket['title']}\n{ticket['error']}"
        if command in {"/cancel", "/pause", "/resume", "/retry"}:
            ticket = self.store.transition(int(argument), command[1:])
            return f"#{ticket['id']} · {ticket['status']}"
        if command == "/remember":
            if not argument or len(argument) > 2000:
                raise ValueError("memory must contain 1–2000 characters")
            memory = self.store.remember(project, argument, f"telegram:{update_id}")
            return f"Remembered #{memory['id']} for {project}"
        if command == "/memories":
            return (
                "\n".join(f"#{m['id']} {m['content']}" for m in self.store.memories(project))
                or "No saved preferences"
            )
        if command == "/forget":
            return "Forgotten" if self.store.forget(project, int(argument)) else "Memory not found"
        workflow = "assistant"
        if command == "/new":
            project, separator, text = argument.partition(" ")
            if not separator:
                raise ValueError("use /new PROJECT PROMPT")
        elif command == "/note":
            workflow, text = "note", argument
        elif text.startswith("/"):
            return "Unknown command. Use /help."
        project_path(self.settings.workspace_dir, project)
        ticket = self.store.create(
            TicketCreate(project=project, title=text[:160], prompt=text, workflow=workflow),
            origin_key=f"telegram:{update_id}",
        )
        return f"Phobos #{ticket['id']} · {ticket['status']}\n{ticket['title']}"

    def deliver(self) -> bool:
        message = self.store.claim_message(include_channel=bool(self.settings.telegram_channel))
        if not message:
            return False
        success = False
        try:
            recipient = message["recipient"] or self.settings.telegram_channel
            body = message["body"]
            if len(body) > 3500:
                body = (
                    body[:3400]
                    + "\n[Truncated. Full output is available through the authenticated API.]"
                )
            self.call("sendMessage", {"chat_id": recipient, "text": body})
            success = True
        except (httpx.HTTPError, RuntimeError, ValueError):
            # Bot API URLs contain the token. Never log exception strings or full request URLs.
            log.warning("Telegram delivery failed; queued for retry")
        self.store.ack_message(message, success)
        return True

    def daily_report(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        if self.settings.telegram_channel and now.hour >= self.settings.report_hour_utc:
            self.store.enqueue_message(f"daily:{now.date()}", report_text(self.store.report()))

    def poll_once(self) -> None:
        offset = int(self.store.state("telegram_offset") or "0")
        updates = self.call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": 25,
                "allowed_updates": ["message"],
            },
        )
        for update in updates:
            self.handle(update)
            self.store.state("telegram_offset", str(update["update_id"] + 1))


def run(settings: Settings) -> None:
    with httpx.Client() as client:
        bot = Telegram(settings, Store(settings.data_dir), client)
        while True:
            try:
                bot.daily_report()
                for _ in range(20):
                    if not bot.deliver():
                        break
                bot.poll_once()
            except (httpx.HTTPError, RuntimeError, ValueError):
                log.warning("Telegram polling unavailable; retrying in five seconds")
                time.sleep(5)
