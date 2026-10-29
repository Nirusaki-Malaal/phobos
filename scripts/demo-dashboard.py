"""Run an isolated sample workspace. Never opens the owner's real data or model gateway."""

import argparse
import logging
import secrets
import threading
import time
from pathlib import Path

import uvicorn

from phobos.api import create_app
from phobos.config import Settings
from phobos.models import TicketCreate
from phobos.store import Store
from phobos.worker import run
from phobos.workspace import atomic_text


def seed(settings: Settings) -> None:
    store = Store(settings.data_dir)
    if store.state("demo_seeded"):
        return
    tasks = [
        (
            "Phobos workspace launch checklist",
            "Phobos launch checklist\n\n✓ Configure the model gateway\n"
            "✓ Keep credentials in .env\n✓ Enable Telegram for private updates\n"
            "✓ Review reusable procedures before activation",
        ),
        (
            "A quieter morning routine",
            "Morning focus\n\n1. Review the three most useful tasks.\n"
            "2. Batch notifications into two windows.\n"
            "3. Keep one uninterrupted hour for deep work.",
        ),
        (
            "Phobos Raspberry Pi setup notes",
            "Phobos on your Raspberry Pi\n\nUse a 64-bit OS and local "
            "storage. Start the lightweight API and worker. Connect heavy rendering elsewhere.\n"
            "Use Tailscale Serve for private remote access. "
            "Hardware verification is still pending.",
        ),
        (
            "Weekly work summary",
            "This week's focus\n\n• A clean self-hosted workspace\n"
            "• A local-first memory graph\n• Reviewed procedures worth reusing\n\n"
            "Next: verify the connected services with real credentials.",
        ),
    ]
    identifiers = []
    for index, (title, content) in enumerate(tasks):
        ticket = store.create(
            TicketCreate(project="personal", title=title, prompt=content, workflow="note")
        )
        claim = store.claim()
        atomic_text(
            settings.workspace_dir / "personal" / "notes" / f"ticket-{ticket['id']}.md", content
        )
        atomic_text(settings.data_dir / "artifacts" / f"{ticket['id']}.md", content)
        store.finish(ticket["id"], claim["claim"], result=content)
        with store.connection() as db:
            db.execute(
                "UPDATE tickets SET created=?,updated=? WHERE id=?",
                (time.time() - (4 - index) * 1800, time.time() - (4 - index) * 1200, ticket["id"]),
            )
        identifiers.append(ticket["id"])
    for preference in [
        "Keep my Phobos summaries concise, with clear next steps.",
        "Keep Phobos project data local. Ask before publishing externally.",
        "Prefer practical examples and editable source files.",
    ]:
        store.remember("personal", preference, "demo-fixture")
    store.feedback(identifiers[0], 1, "Sample review: clear Phobos checklist and next steps.")
    store.record_skill(identifiers[0], "phobos-graph-memory", "d" * 64, "demo-fixture")
    procedure = store.create_procedure(
        "personal",
        "daily-planning",
        "Start with three priorities. Keep the plan concise. Finish with one concrete next step.",
        identifiers[0],
    )
    store.evaluate_procedure(
        "personal",
        procedure["id"],
        identifiers[1],
        True,
        "Demo fixture: checked the three-priority format.",
    )
    store.transition_procedure("personal", procedure["id"], "activate")
    store.create(
        TicketCreate(
            project="personal",
            title="Create a 3D Phobos scene",
            workflow="blender",
            prompt='{"source":"scene.json","output":"renders/phobos.blend"}',
        )
    )
    paused = store.create(
        TicketCreate(
            project="personal",
            title="Plan next week’s priorities",
            workflow="note",
            prompt="Review this plan before resuming.",
        )
    )
    store.transition(paused["id"], "pause")
    store.state("demo_seeded", "yes")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("/tmp/phobos-dashboard-demo"))
    parser.add_argument("--port", type=int, default=8094)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_path = args.directory / "owner-token"
    if not token_path.exists():
        atomic_text(token_path, secrets.token_urlsafe(32))
        token_path.chmod(0o600)
    settings = Settings(
        _env_file=None,
        api_token=token_path.read_text().strip(),
        data_dir=args.directory / "data",
        workspace_dir=args.directory / "workspace",
        model="",
        telegram_token="",
        telegram_users="",
        skill_auto_install=False,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    seed(settings)
    threading.Thread(target=run, args=(settings,), daemon=True).start()
    print(f"Isolated demo: http://127.0.0.1:{args.port}/?demo=1", flush=True)
    print(f"Owner token file: {token_path} (demo access only)", flush=True)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
