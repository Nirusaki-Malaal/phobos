import json
import logging
import threading
import time

import httpx

from phobos import creative
from phobos.agent import Agent, Cancelled
from phobos.config import Settings
from phobos.models import CreativeRequest
from phobos.store import Store, report_text
from phobos.workspace import atomic_text, confined, project_path

log = logging.getLogger(__name__)


def run_once(settings: Settings, store: Store, client: httpx.Client) -> bool:
    capabilities = creative.capabilities()
    workflows = ("assistant", "note", "report") + tuple(
        name for name in ("clip", "audio", "blender", "transcribe") if capabilities[name]
    )
    ticket = store.claim(workflows=workflows)
    if ticket is None:
        return False
    stop = threading.Event()
    lost = threading.Event()

    def check() -> None:
        current = store.get(ticket["id"])
        if lost.is_set() or current["status"] != "running" or current["claim"] != ticket["claim"]:
            raise Cancelled("ticket cancelled or worker lease lost")

    def heartbeat() -> None:
        while not stop.wait(10):
            try:
                if not store.heartbeat(ticket["id"], ticket["claim"]):
                    lost.set()
                    return
            except Exception:
                log.exception("Heartbeat failed for ticket %s", ticket["id"])
                lost.set()
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        root = project_path(settings.workspace_dir, ticket["project"])
        root.mkdir(parents=True, exist_ok=True)
        check()
        if ticket["workflow"] == "note":
            result = ticket["prompt"]
            atomic_text(confined(root, f"notes/ticket-{ticket['id']}.md"), result)
        elif ticket["workflow"] == "report":
            result = report_text(store.report())
        elif ticket["workflow"] in {"clip", "audio", "blender", "transcribe"}:
            request = CreativeRequest.model_validate_json(ticket["prompt"])
            if ticket["workflow"] == "blender":
                output = creative.scene(root, request.source, request.output)
            elif ticket["workflow"] == "transcribe":
                output = creative.transcribe(
                    root, request.source, request.output, str(settings.transcription_model_dir)
                )
            else:
                output = creative.media(
                    root,
                    request.source,
                    request.output,
                    start=request.start,
                    duration=request.duration,
                    audio_only=ticket["workflow"] == "audio",
                )
            result = json.dumps(output, indent=2)
        else:
            result = Agent(settings, store, client).execute(ticket, check)
        check()
        atomic_text(settings.data_dir / "artifacts" / f"{ticket['id']}.md", result)
        store.finish(ticket["id"], ticket["claim"], result=result)
    except Cancelled:
        log.info("Ticket %s stopped; prior tool side effects may exist", ticket["id"])
    except Exception as error:
        # HTTP exceptions can include URLs; do not persist credential-bearing endpoint details.
        detail = "Gateway request failed" if isinstance(error, httpx.HTTPError) else str(error)
        log.error("Ticket %s failed (%s)", ticket["id"], type(error).__name__)
        store.finish(ticket["id"], ticket["claim"], error=detail[:1000])
    finally:
        stop.set()
        thread.join(timeout=2)
    return True


def run(settings: Settings) -> None:
    store = Store(settings.data_dir)
    with httpx.Client() as client:
        while True:
            store.state("worker_seen", str(time.time()))
            if not run_once(settings, store, client):
                time.sleep(1)
