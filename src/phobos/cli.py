import argparse
import json
import logging
import os
import secrets
from pathlib import Path

from phobos.config import Settings
from phobos.models import TicketCreate
from phobos.store import Store, public_ticket


def initialize() -> None:
    path = Path(".env")
    content = (
        f"PHOBOS_API_TOKEN={secrets.token_urlsafe(32)}\n"
        "PHOBOS_DATA_DIR=./data\nPHOBOS_WORKSPACE_DIR=./workspace\n"
        "PHOBOS_GATEWAY_URL=http://127.0.0.1:4000/v1\nPHOBOS_GATEWAY_KEY=\nPHOBOS_MODEL=\n"
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(content)
    Path("workspace/personal").mkdir(parents=True, exist_ok=True)
    print("Created .env with a random owner token and workspace/personal. Keep .env private.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="phobos", description="Phobos personal AI helper")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "serve", "worker", "telegram", "report"):
        sub.add_parser(command)
    ticket = sub.add_parser("ticket", help="Queue a ticket")
    ticket.add_argument("prompt")
    ticket.add_argument("--project", default="personal")
    ticket.add_argument(
        "--workflow",
        choices=["assistant", "note", "report", "clip", "audio", "blender", "transcribe"],
        default="assistant",
    )
    backup = sub.add_parser("backup", help="Create a consistent SQLite snapshot")
    backup.add_argument("destination", type=Path)
    creative = sub.add_parser("creative", help="Local clip, audio, and Blender jobs")
    creative.add_argument(
        "kind",
        choices=[
            "capabilities",
            "clip",
            "audio",
            "blender",
            "transcribe",
            "motion-create",
            "motion-render",
        ],
    )
    creative.add_argument("--project", default="personal")
    creative.add_argument("--source")
    creative.add_argument("--output")
    creative.add_argument("--start", type=float, default=0)
    creative.add_argument("--duration", type=float, default=30)
    creative.add_argument("--model-dir", help="Provisioned local faster-whisper model directory")
    skill = sub.add_parser("skill", help="Find, install or inspect procedural skills")
    skill.add_argument("action", choices=["find", "install", "view", "list"])
    skill.add_argument("value", nargs="?", default="")
    skill.add_argument("--project", default="personal")
    skill.add_argument("--online", action="store_true")
    memory = sub.add_parser("memory", help="Query or rebuild local Graphify memory")
    memory.add_argument("action", choices=["recall", "rebuild", "index"])
    memory.add_argument("query", nargs="?", default="recent work")
    memory.add_argument("--project", default="personal")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    # httpx INFO logging includes Telegram's credential-bearing request URL.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if args.command == "init":
        try:
            initialize()
        except FileExistsError:
            parser.exit(1, ".env already exists; leaving it intact\n")
        return
    settings = Settings()
    if args.command == "skill":
        import httpx

        from phobos.skills import SkillLibrary

        library = SkillLibrary(settings)
        try:
            if args.action == "list":
                result = library.available()
            elif args.action == "find":
                result = library.search(args.value, args.project, args.online)
            elif args.action == "install":
                result = library.install(args.value)
            else:
                result = library.view(args.value)
            print(json.dumps(result, indent=2))
        except (ValueError, OSError, httpx.HTTPError) as error:
            parser.exit(1, f"Skill operation failed ({type(error).__name__})\n")
    elif args.command == "memory":
        from phobos.memory import GraphMemory

        memory = GraphMemory(settings, Store(settings.data_dir))
        if args.action == "index":
            result = memory.index_code(args.project)
        elif args.action == "rebuild":
            graph = memory.build(args.project)
            result = {"nodes": len(graph), "edges": graph.number_of_edges()}
        else:
            result = memory.recall(args.project, args.query)
        print(json.dumps(result, indent=2))
    elif args.command == "creative":
        from phobos import creative
        from phobos.workspace import project_path

        try:
            if args.kind == "capabilities":
                result = creative.capabilities()
            elif args.kind.startswith("motion-"):
                directory = args.output if args.kind == "motion-create" else args.source
                if not directory:
                    parser.error("motion-create needs --output; motion-render needs --source")
                root = project_path(settings.workspace_dir, args.project)
                result = creative.motion(root, directory, render=args.kind == "motion-render")
            else:
                if not args.source or not args.output:
                    parser.error("creative jobs require --source and --output")
                root = project_path(settings.workspace_dir, args.project)
                if args.kind == "blender":
                    result = creative.scene(root, args.source, args.output)
                elif args.kind == "transcribe":
                    if not args.model_dir:
                        parser.error("transcribe requires --model-dir")
                    result = creative.transcribe(root, args.source, args.output, args.model_dir)
                else:
                    result = creative.media(
                        root,
                        args.source,
                        args.output,
                        start=args.start,
                        duration=args.duration,
                        audio_only=args.kind == "audio",
                    )
            print(json.dumps(result, indent=2))
        except (ValueError, OSError) as error:
            parser.exit(1, f"Creative job failed: {error}\n")
    elif args.command == "serve":
        import uvicorn

        uvicorn.run("phobos.api:create_app", factory=True, host=settings.host, port=settings.port)
    elif args.command in {"worker", "telegram"}:
        from phobos import telegram, worker

        (worker.run if args.command == "worker" else telegram.run)(settings)
    elif args.command == "ticket":
        from phobos.workspace import project_path

        project_path(settings.workspace_dir, args.project)
        ticket = Store(settings.data_dir).create(
            TicketCreate(
                project=args.project,
                title=args.prompt[:160],
                prompt=args.prompt,
                workflow=args.workflow,
            )
        )
        print(json.dumps(public_ticket(ticket), indent=2))
    elif args.command == "report":
        print(json.dumps(Store(settings.data_dir).report(), indent=2))
    elif args.command == "backup":
        if args.destination.exists():
            parser.exit(1, "Backup destination already exists\n")
        Store(settings.data_dir).backup(args.destination)
        print(
            f"Database snapshot saved to {args.destination}; "
            "copy artifacts and workspace separately"
        )


if __name__ == "__main__":
    main()
