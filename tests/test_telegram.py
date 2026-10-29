import json
from datetime import UTC, datetime

import httpx

from phobos.telegram import Telegram


def update(text="Hello", update_id=1, user_id=123, chat_type="private"):
    return {
        "update_id": update_id,
        "message": {
            "text": text,
            "from": {"id": user_id},
            "chat": {"id": 123, "type": chat_type},
        },
    }


def test_authorization_and_duplicate_updates(settings, store, no_network):
    bot = Telegram(settings, store, no_network)
    bot.handle(update(user_id=456))
    bot.handle(update(chat_type="group"))
    assert store.list() == []
    bot.handle(update())
    bot.handle(update())
    assert len(store.list()) == 1
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM outbox WHERE dedupe='reply:1'").fetchone()[0] == 1
    bot.handle(update("/remember Prefer short answers", 2))
    bot.handle(update("/remember Prefer short answers", 2))
    assert len(store.memories("personal")) == 1
    bot.handle(update("/forget 1", 3))
    assert store.memories("personal") == []


def test_polling_offsets_and_replies(settings, store):
    payloads = []

    def api(request):
        payload = json.loads(request.content)
        payloads.append((request.url.path.rsplit("/", 1)[-1], payload))
        result = (
            [update("/note Remember groceries", 45)]
            if request.url.path.endswith("getUpdates")
            else {}
        )
        return httpx.Response(200, json={"ok": True, "result": result})

    with httpx.Client(transport=httpx.MockTransport(api)) as client:
        bot = Telegram(settings, store, client)
        bot.poll_once()
        assert store.state("telegram_offset") == "46"
        assert bot.deliver()
        assert bot.deliver()
        assert any(
            method == "sendMessage" and body["chat_id"] == "123" for method, body in payloads
        )
        assert any(
            method == "sendMessage" and body["chat_id"] == "@testchannel"
            for method, body in payloads
        )
    assert store.list()[0]["workflow"] == "note"


def test_daily_report_deduplication_and_failed_delivery(settings, store):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(429))) as client:
        bot = Telegram(settings, store, client)
        now = datetime(2026, 10, 29, 19, tzinfo=UTC)
        bot.daily_report(now)
        bot.daily_report(now)
        assert bot.deliver()
        with store.connection() as db:
            rows = db.execute("SELECT * FROM outbox").fetchall()
            assert len(rows) == 1
            assert rows[0]["attempts"] == 1
            assert rows[0]["sent"] is None


def test_no_configured_channel_still_delivers_private_replies(settings, store):
    settings.telegram_channel = ""
    sent = []

    def api(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    with httpx.Client(transport=httpx.MockTransport(api)) as client:
        bot = Telegram(settings, store, client)
        bot.handle(update())
        assert bot.deliver()
        assert not bot.deliver()
    assert sent[0]["chat_id"] == "123"
