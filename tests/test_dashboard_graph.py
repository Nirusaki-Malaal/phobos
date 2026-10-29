import json

import httpx
import pytest
from fastapi.testclient import TestClient

from phobos.memory import GraphMemory
from phobos.models import TicketCreate
from phobos.skills import SkillLibrary


def test_dashboard_static_assets_and_owner_boundary(settings):
    from phobos.api import create_app

    with TestClient(create_app(settings)) as client:
        assert "dashboard.js" in client.get("/").text
        assert client.get("/static/dashboard.js").status_code == 200
        logo = client.get("/static/phobos-mark.svg")
        assert logo.status_code == 200 and "orbital P" in logo.text
        assert client.get("/api/overview").status_code == 401
        client.headers["Authorization"] = "Bearer " + settings.api_token
        overview = client.get("/api/overview").json()
        assert overview["skills"] == 70 and overview["memories"] == 0
        assert "api_token" not in overview and "telegram_token" not in overview


def test_graphify_recall_relations_isolation_and_forgetting(settings, store):
    ticket = store.create(
        TicketCreate(project="alpha", title="SQLite backup", prompt="Back up SQLite")
    )
    claimed = store.claim()
    store.finish(ticket["id"], claimed["claim"], result="SQLite backup complete")
    memory = store.remember("alpha", "Keep SQLite backup files local")
    store.feedback(ticket["id"], 1, "Verified the backup")
    graph = GraphMemory(settings, store)
    result = graph.recall("alpha", "SQLite backup")
    assert result["engine"] == "graphifyy"
    assert any(edge["relation"] == "evaluates" for edge in result["relations"])
    assert graph.recall("beta", "SQLite backup")["matches"] == []
    assert (settings.data_dir / "graphs/alpha/graph.json").exists()
    store.forget("alpha", memory["id"])
    assert not (settings.data_dir / "graphs/alpha/graph.json").exists()
    assert all(
        row["id"] != f"memory-{memory['id']}"
        for row in graph.recall("alpha", "SQLite backup")["matches"]
    )


def test_real_graphify_code_index_skips_escape_and_hidden(settings, store, tmp_path):
    root = settings.workspace_dir / "alpha"
    root.mkdir(parents=True)
    (root / "service.py").write_text('def backup_database():\n    return "snapshot"\n')
    (root / ".secret.py").write_text("def HIDDEN(): pass")
    outside = tmp_path / "outside.py"
    outside.write_text("def OUTSIDE(): pass")
    (root / "escape.py").symlink_to(outside)
    memory = GraphMemory(settings, store)
    result = memory.index_code("alpha")
    assert result["files"] == 1
    recalled = memory.recall("alpha", "backup_database")
    assert "backup_database" in json.dumps(recalled)
    assert "OUTSIDE" not in json.dumps(recalled) and "HIDDEN" not in json.dumps(recalled)


def test_skill_download_pinned_no_scripts_and_license(settings, monkeypatch):
    candidate = {
        "name": "example",
        "repo": "morluto/rea",
        "revision": "a" * 40,
        "path": "skills/example",
    }
    requests = []

    def remote(request):
        requests.append(str(request.url))
        if request.url.host == "api.github.com":
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {"path": "skills/example/SKILL.md", "type": "blob", "mode": "100644"},
                        {"path": "LICENSE", "type": "blob", "mode": "100644"},
                    ]
                },
            )
        return httpx.Response(
            200,
            text="MIT License"
            if request.url.path.endswith("LICENSE")
            else "---\nname: example\n---\nExample instructions",
        )

    with httpx.Client(transport=httpx.MockTransport(remote)) as client:
        library = SkillLibrary(settings, client)
        monkeypatch.setattr(library, "catalog", lambda: [candidate])
        result = library.install("example")
        assert result["scripts_executed"] is False
        assert all("a" * 40 in url for url in requests)
        assert library.view("example")["provenance"]["repo"] == "morluto/rea"
        assert (settings.data_dir / "skills/example/UPSTREAM_LICENSE").is_file()
        candidate["repo"] = "unknown/source"
        candidate["name"] = "unsafe"
        with pytest.raises(ValueError, match="approved"):
            library.install("unsafe")


def test_feedback_affects_project_skill_ranking(settings, store):
    ticket = store.create(TicketCreate(project="alpha", title="Go task", prompt="Go task"))
    claim = store.claim()
    store.finish(ticket["id"], claim["claim"], result="Done")
    store.record_skill(ticket["id"], "phobos-go", "a" * 64, "bundled")
    library = SkillLibrary(settings)
    initial = next(
        row for row in library.search("phobos go", "alpha") if row["name"] == "phobos-go"
    )
    store.feedback(ticket["id"], -1, "Not useful")
    after = next(row for row in library.search("phobos go", "alpha") if row["name"] == "phobos-go")
    assert after["score"] == initial["score"] - 1
    assert store.skill_scores("beta") == {}
