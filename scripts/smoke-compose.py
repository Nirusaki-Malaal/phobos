"""Exercise the built image with disposable, project-scoped Docker volumes."""

import argparse
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from phobos.config import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", help="Docker context; leaves the global selection unchanged")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    settings = Settings()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    docker = ["docker"] + (["--context", args.context] if args.context else [])
    with tempfile.TemporaryDirectory(prefix="phobos-smoke-") as temporary:
        override = Path(temporary) / "compose.yaml"
        override.write_text(
            "services:\n"
            "  api:\n"
            f"    ports: !override ['127.0.0.1:{port}:8080']\n"
            "    volumes: ['smoke-workspace:/workspace']\n"
            "  worker:\n"
            "    volumes: ['smoke-workspace:/workspace']\n"
            "volumes:\n"
            "  smoke-workspace:\n"
        )
        compose = docker + [
            "compose",
            "-p",
            "phobos-smoke",
            "-f",
            str(root / "compose.yaml"),
            "-f",
            str(override),
        ]
        try:
            subprocess.run(compose + ["config", "--quiet"], cwd=root, check=True)
            subprocess.run(
                compose + ["up", "-d", "--no-build", "api", "worker"], cwd=root, check=True
            )
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3) as client:
                deadline = time.monotonic() + 45
                while True:
                    try:
                        if client.get("/healthz").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Container API did not become healthy")
                    time.sleep(0.5)
                if client.get("/api/tickets").status_code != 401:
                    raise RuntimeError("API authentication check failed")
                client.headers["Authorization"] = "Bearer " + settings.api_token
                response = client.post(
                    "/api/tickets",
                    json={
                        "project": "personal",
                        "title": "Container smoke test",
                        "prompt": "Delivered by a non-root Docker worker.",
                        "workflow": "note",
                    },
                )
                response.raise_for_status()
                ticket_id = response.json()["id"]
                while True:
                    ticket = client.get(f"/api/tickets/{ticket_id}").json()
                    if ticket["status"] in {"completed", "failed"}:
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Container worker did not complete the note")
                    time.sleep(0.5)
                if ticket["status"] != "completed":
                    raise RuntimeError(f"Container task failed: {ticket['error']}")
                artifact = client.get(f"/api/artifacts/{ticket_id}")
                artifact.raise_for_status()
                if artifact.text != ticket["prompt"]:
                    raise RuntimeError("Artifact differs from delivered note")
                print("Docker smoke passed: authentication → API → worker → file artifact.")
        except Exception:
            subprocess.run(compose + ["logs", "--tail=60"], cwd=root, check=False)
            raise
        finally:
            # These resources belong exclusively to this disposable smoke-test project.
            subprocess.run(compose + ["down", "--volumes"], cwd=root, check=True)


if __name__ == "__main__":
    main()
