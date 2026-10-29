"""A real loopback HTTP server and separate worker process, without external providers."""

import os
import socket
import subprocess
import sys
import time

import httpx


def test_live_api_to_worker_to_artifact(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    token = "local-system-test-owner-token-12345"
    environment = {
        **os.environ,
        "PHOBOS_API_TOKEN": token,
        "PHOBOS_HOST": "127.0.0.1",
        "PHOBOS_PORT": str(port),
        "PHOBOS_DATA_DIR": str(tmp_path / "data"),
        "PHOBOS_WORKSPACE_DIR": str(tmp_path / "workspace"),
    }
    processes = []
    try:
        for command in ("serve", "worker"):
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-m", "phobos.cli", command],
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as client:
            deadline = time.monotonic() + 15
            while True:
                try:
                    if client.get("/healthz").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline, "API did not start"
                assert all(process.poll() is None for process in processes)
                time.sleep(0.05)
            assert client.get("/api/tickets").status_code == 401
            client.headers["Authorization"] = "Bearer " + token
            response = client.post(
                "/api/tickets",
                json={
                    "project": "personal",
                    "title": "Integration note",
                    "prompt": "A real workflow delivered this note.",
                    "workflow": "note",
                },
            )
            assert response.status_code == 201
            ticket_id = response.json()["id"]
            while True:
                ticket = client.get(f"/api/tickets/{ticket_id}").json()
                if ticket["status"] in {"completed", "failed"}:
                    break
                assert time.monotonic() < deadline, "Worker did not deliver output"
                time.sleep(0.05)
            assert ticket["status"] == "completed", ticket
            assert client.get(f"/api/artifacts/{ticket_id}").text == ticket["prompt"]
            assert (tmp_path / "workspace/personal/notes/ticket-1.md").read_text() == ticket[
                "prompt"
            ]
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
