"""Learning and creative regression coverage, restored after a disk-full write."""

import json
import shutil
import sqlite3
import subprocess

import httpx
import pytest
from fastapi.testclient import TestClient

from phobos import creative
from phobos.agent import Agent
from phobos.api import create_app
from phobos.models import TicketCreate
from phobos.store import Conflict, Store


def complete(store, project="alpha"):
    ticket = store.create(TicketCreate(project=project, title="Example", prompt="Useful output"))
    claim = store.claim()
    assert claim["id"] == ticket["id"]
    store.finish(ticket["id"], claim["claim"], result="Reviewed artifact")
    return ticket["id"]


def test_learning_lifecycle_and_version_replacement(store):
    source, evidence = complete(store), complete(store)
    draft = store.create_procedure("alpha", "reports", "Cite sources", source)
    with pytest.raises(Conflict):
        store.transition_procedure("alpha", draft["id"], "activate")
    with pytest.raises(Conflict):
        store.evaluate_procedure("alpha", draft["id"], source, True, "Same source")
    with pytest.raises(KeyError):
        store.procedure("beta", draft["id"])
    wrong_project = complete(store, "beta")
    with pytest.raises(Conflict):
        store.evaluate_procedure("alpha", draft["id"], wrong_project, True, "Wrong project")
    store.evaluate_procedure("alpha", draft["id"], evidence, True, "Checked sources")
    store.transition_procedure("alpha", draft["id"], "activate")
    newer = store.create_procedure("alpha", "reports", "Cite sources and dates", source)
    assert newer["version"] == 2
    store.evaluate_procedure("alpha", newer["id"], evidence, True, "Checked dates")
    store.transition_procedure("alpha", newer["id"], "activate")
    assert store.procedure("alpha", draft["id"])["status"] == "retired"
    assert len(store.procedures("alpha", active_only=True)) == 1
    store.evaluate_procedure("alpha", newer["id"], evidence, False, "Date missing")
    assert not store.procedures("alpha", active_only=True)


def test_learning_api_authorization_and_constraints(settings, store):
    source = complete(store)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/procedures/alpha").status_code == 401
        client.headers["Authorization"] = "Bearer " + settings.api_token
        response = client.post(
            "/api/procedures/alpha",
            json={"name": "reports", "instructions": "Keep concise", "source_ticket": source},
        )
        assert response.status_code == 201
        identifier = response.json()["id"]
        assert client.post(f"/api/procedures/alpha/{identifier}/state/activate").status_code == 409
        assert client.get(f"/api/procedures/beta/{identifier}").status_code == 404


def test_only_active_project_procedures_enter_model_context(settings, store):
    source, evidence = complete(store), complete(store)
    procedure = store.create_procedure("alpha", "reports", "ALPHA REVIEWED", source)
    store.create_procedure("alpha", "draft", "NEVER INCLUDE DRAFT", source)
    beta_source = complete(store, "beta")
    store.create_procedure("beta", "private", "BETA PRIVATE", beta_source)
    store.evaluate_procedure("alpha", procedure["id"], evidence, True, "Review complete")
    store.transition_procedure("alpha", procedure["id"], "activate")
    ticket = store.create(TicketCreate(project="alpha", title="Next", prompt="Prepare report"))

    def gateway(request):
        text = json.dumps(json.loads(request.content)["messages"])
        assert "ALPHA REVIEWED" in text
        assert "NEVER INCLUDE DRAFT" not in text and "BETA PRIVATE" not in text
        return httpx.Response(200, json={"choices": [{"message": {"content": "Done"}}]})

    with httpx.Client(transport=httpx.MockTransport(gateway)) as client:
        assert Agent(settings, store, client).execute(ticket, lambda: None) == "Done"
    loaded = [event for event in store.events(ticket["id"]) if event["kind"] == "procedures_loaded"]
    assert json.loads(loaded[0]["detail"]) == [{"id": procedure["id"], "version": 1}]


def test_version_one_database_upgrade_preserves_state(tmp_path):
    store = Store(tmp_path)
    source = complete(store)
    with store.connection() as db:
        db.execute("DROP TABLE procedure_evaluations")
        db.execute("DROP TABLE procedures")
        db.execute("PRAGMA user_version=1")
    upgraded = Store(tmp_path)
    assert upgraded.get(source)["status"] == "completed"
    assert upgraded.create_procedure("alpha", "new", "Instructions", source)["version"] == 1
    with sqlite3.connect(upgraded.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3


def test_creative_paths_time_limits_and_missing_dependency(tmp_path, monkeypatch):
    (tmp_path / "recording.mp4").write_bytes(b"dummy")
    for start, duration in [(float("nan"), 1), (0, 601), (-1, 1)]:
        with pytest.raises(ValueError):
            creative.media(tmp_path, "recording.mp4", "clip.mp4", start=start, duration=duration)
    with pytest.raises(ValueError):
        creative.media(tmp_path, "../outside.mp4", "clip.mp4")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="Install ffmpeg"):
        creative.media(tmp_path, "recording.mp4", "clip.mp4")


def test_blender_scene_validates_and_generates_fixed_command(tmp_path, monkeypatch):
    from pathlib import Path

    spec = tmp_path / "scene.json"
    spec.write_text(json.dumps({"objects": [{"shape": "sphere"}]}))

    def render(binary, args, timeout):
        assert binary == "blender" and "--disable-autoexec" in args and timeout == 600
        config = json.loads(args[-1])
        Path(config["blend"]).write_bytes(b"blend")
        Path(config["preview"]).write_bytes(b"png")

    monkeypatch.setattr(creative, "_run", render)
    assert (
        creative.scene(tmp_path, "scene.json", "renders/scene.blend")["preview"]
        == "renders/scene.png"
    )
    with pytest.raises(ValueError, match="unused"):
        creative.scene(tmp_path, "scene.json", "renders/scene.blend")
    spec.write_text(json.dumps({"objects": [{"shape": "cube", "scale": [float("inf"), 1, 1]}]}))
    with pytest.raises(ValueError, match="scale"):
        creative.scene(tmp_path, "scene.json", "other.blend")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="Optional FFmpeg not installed")
def test_real_ffmpeg_clip_and_audio(tmp_path):
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:r=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(tmp_path / "recording.mp4"),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    assert creative.media(tmp_path, "recording.mp4", "clips/out.mp4", duration=0.5)["bytes"] > 0
    assert (
        creative.media(tmp_path, "recording.mp4", "audio/out.wav", duration=0.5, audio_only=True)[
            "bytes"
        ]
        > 0
    )
