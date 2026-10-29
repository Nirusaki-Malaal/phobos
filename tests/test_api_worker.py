import json

import httpx
import pytest
from fastapi.testclient import TestClient

from phobos.api import create_app
from phobos.models import TicketCreate
from phobos.worker import run_once


def request_ticket(workflow="note"):
    return {
        "project": "personal",
        "title": "My note",
        "prompt": "Hello Phobos",
        "workflow": workflow,
    }


def test_authenticated_note_roundtrip(settings, no_network):
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").json()["name"] == "Phobos"
        assert client.get("/api/tickets").status_code == 401
        assert client.post("/api/tickets", json=request_ticket()).status_code == 401
        client.headers["Authorization"] = f"Bearer {settings.api_token}"
        response = client.post("/api/tickets", json=request_ticket())
        assert response.status_code == 201
        ticket = response.json()
        assert "claim" not in ticket
        assert client.get(f"/api/artifacts/{ticket['id']}").status_code == 404
        assert run_once(settings, client.app.state.store, no_network)
        completed = client.get(f"/api/tickets/{ticket['id']}").json()
        assert completed["status"] == "completed"
        assert completed["result"] == "Hello Phobos"
        assert client.get(f"/api/artifacts/{ticket['id']}").text == "Hello Phobos"
        assert (settings.workspace_dir / "personal/notes/ticket-1.md").read_text() == "Hello Phobos"
        assert client.get("/api/report").json()["completed"] == 1
        assert client.post(f"/api/tickets/{ticket['id']}/pause").status_code == 409


def test_api_validation_memory_feedback_and_safe_dashboard(settings):
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = f"Bearer {settings.api_token}"
        assert (
            client.post("/api/tickets", json={**request_ticket(), "project": "../bad"}).status_code
            == 422
        )
        assert client.get("/api/tickets/999").status_code == 404
        memory = client.post("/api/memories/personal", json={"content": "Keep it concise"}).json()
        assert client.get("/api/memories/other").json() == []
        assert client.delete(f"/api/memories/other/{memory['id']}").status_code == 404
        assert client.delete(f"/api/memories/personal/{memory['id']}").status_code == 204
        ticket = client.post("/api/tickets", json=request_ticket()).json()
        assert client.post(f"/api/feedback/{ticket['id']}", json={"rating": 0}).status_code == 422
        feedback = client.post(f"/api/feedback/{ticket['id']}", json={"rating": 1}).json()
        assert feedback["promoted_to_memory"] is False
        page = client.get("/").text
        assert "innerHTML" not in page
        assert "localStorage" not in page


def test_gateway_tool_execution_and_memory_isolation(settings, store):
    ticket = store.create(TicketCreate(**request_ticket("assistant")))
    store.remember("personal", "Use bullet points")
    store.remember("secret-project", "THIS MUST NOT LEAK")
    requests = []

    def gateway(request):
        body = json.loads(request.content)
        requests.append(body)
        assert str(request.url) == settings.gateway_url + "/chat/completions"
        assert "THIS MUST NOT LEAK" not in request.content.decode()
        if len(requests) == 1:
            assert "Use bullet points" in request.content.decode()
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "save_note",
                            "arguments": json.dumps({"content": "Generated note"}),
                        },
                    }
                ],
            }
        else:
            assert body["messages"][-1]["role"] == "tool"
            assert "notes/ticket-1.md" in body["messages"][-1]["content"]
            message = {"role": "assistant", "content": "Saved your note."}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    with httpx.Client(transport=httpx.MockTransport(gateway)) as client:
        run_once(settings, store, client)
    assert len(requests) == 2
    assert store.get(ticket["id"])["status"] == "completed"
    assert (settings.workspace_dir / "personal/notes/ticket-1.md").read_text() == "Generated note"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": ""}}],
            },
        ),
    ],
)
def test_gateway_failure_never_claims_completion(settings, store, response):
    ticket = store.create(TicketCreate(**request_ticket("assistant")))
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
        run_once(settings, store, client)
    assert store.get(ticket["id"])["status"] == "failed"
    assert not (settings.data_dir / "artifacts/1.md").exists()


def test_old_worker_stops_after_cancel_retry_and_new_claim(settings, store):
    ticket = store.create(TicketCreate(**request_ticket("assistant")))
    current = []

    def gateway(request):
        store.transition(ticket["id"], "cancel")
        store.transition(ticket["id"], "retry")
        current.append(store.claim())
        return httpx.Response(200, json={"choices": [{"message": {"content": "Stale result"}}]})

    with httpx.Client(transport=httpx.MockTransport(gateway)) as client:
        run_once(settings, store, client)
    assert store.get(ticket["id"])["status"] == "running"
    assert store.get(ticket["id"])["claim"] == current[0]["claim"]
    assert not (settings.data_dir / "artifacts/1.md").exists()
