from concurrent.futures import ThreadPoolExecutor

import pytest

from phobos.models import TicketCreate
from phobos.store import Conflict, Store


def create(store, **kwargs):
    return store.create(TicketCreate(project="personal", title="Test", prompt="Hello", **kwargs))


def test_exactly_one_concurrent_claim(store):
    ticket = create(store)
    with ThreadPoolExecutor(max_workers=12) as pool:
        claims = list(pool.map(lambda _: store.claim(), range(24)))
    valid = [claim for claim in claims if claim]
    assert len(valid) == 1
    assert valid[0]["id"] == ticket["id"]
    assert [event["kind"] for event in store.events(ticket["id"])] == ["queued", "running"]


def test_duplicate_origin_does_not_duplicate_events(store):
    request = TicketCreate(project="personal", title="Ticket", prompt="Do it")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.create(request, "telegram:42"), range(16)))
    assert len({ticket["id"] for ticket in results}) == 1
    assert len(store.events(results[0]["id"])) == 1


def test_cancel_retry_invalidates_old_claim(store):
    ticket = create(store)
    old = store.claim()
    store.transition(ticket["id"], "cancel")
    store.transition(ticket["id"], "retry")
    new = store.claim()
    assert not store.finish(ticket["id"], old["claim"], result="stale")
    assert not store.heartbeat(ticket["id"], old["claim"])
    assert store.finish(ticket["id"], new["claim"], result="valid")
    assert store.get(ticket["id"])["result"] == "valid"


def test_expired_worker_fails_without_replay(store):
    ticket = create(store)
    old = store.claim(lease_seconds=-1)
    assert not store.heartbeat(ticket["id"], old["claim"])
    assert store.claim() is None
    assert store.get(ticket["id"])["status"] == "failed"
    assert not store.finish(ticket["id"], old["claim"], result="late")


def test_state_transitions_and_missing_ticket(store):
    ticket = create(store)
    store.transition(ticket["id"], "pause")
    assert store.claim() is None
    with pytest.raises(Conflict):
        store.transition(ticket["id"], "retry")
    store.transition(ticket["id"], "resume")
    assert store.claim()["id"] == ticket["id"]
    with pytest.raises(KeyError):
        store.get(999)


def test_project_memories_and_backup(store, tmp_path):
    memory = store.remember("alpha", "Use short sentences")
    assert store.memories("beta") == []
    assert not store.forget("beta", memory["id"])
    destination = tmp_path / "backup" / "phobos.db"
    store.backup(destination)
    restored = Store(destination.parent)
    assert restored.memories("alpha")[0]["content"] == "Use short sentences"
    assert store.forget("alpha", memory["id"])
    assert store.memories("alpha") == []


def test_outbox_retries_and_excludes_unconfigured_channel(store):
    create(store)
    assert store.claim_message(include_channel=False) is None
    store.enqueue_message("reply", "Hello", "123")
    message = store.claim_message(include_channel=False)
    assert message["recipient"] == "123"
    assert store.claim_message(include_channel=False) is None
    store.ack_message(message, success=False)
    assert store.claim_message(include_channel=False) is None
    with store.connection() as db:
        db.execute("UPDATE outbox SET due=0 WHERE id=?", (message["id"],))
    retry = store.claim_message(include_channel=False)
    assert retry["attempts"] == 1
    store.ack_message(retry, success=True)
    assert store.claim_message(include_channel=False) is None
