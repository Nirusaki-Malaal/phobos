import secrets
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from phobos.config import Settings
from phobos.models import (
    FeedbackCreate,
    MemoryCreate,
    ProcedureCreate,
    ProcedureEvaluation,
    TicketCreate,
)
from phobos.store import Conflict, Store, public_ticket
from phobos.workspace import project_path


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    store = Store(settings.data_dir)

    @asynccontextmanager
    async def lifespan(app):
        yield

    app = FastAPI(title="Phobos", version="0.1.0", lifespan=lifespan)
    from importlib.resources import files

    app.mount(
        "/static", StaticFiles(directory=str(files("phobos").joinpath("static"))), name="static"
    )
    app.state.store = store

    def authorized(authorization: Annotated[str | None, Header()] = None):
        expected = "Bearer " + settings.api_token
        if not authorization or not secrets.compare_digest(
            authorization.encode(), expected.encode()
        ):
            raise HTTPException(401, "Owner token required", headers={"WWW-Authenticate": "Bearer"})

    owner = [Depends(authorized)]

    def get_ticket(ticket_id: int):
        try:
            return store.get(ticket_id)
        except KeyError:
            raise HTTPException(404, "Ticket not found") from None

    def validate_project(project: str):
        try:
            return project_path(settings.workspace_dir, project)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.get("/healthz")
    def health():
        with store.connection() as db:
            db.execute("SELECT 1").fetchone()
        return {"status": "ok", "name": "Phobos", "version": "0.1.0"}

    @app.get("/", response_class=HTMLResponse)
    def home():
        from importlib.resources import files

        return files("phobos").joinpath("dashboard.html").read_text()

    @app.post("/api/tickets", status_code=201, dependencies=owner)
    def create_ticket(ticket: TicketCreate):
        validate_project(ticket.project)
        if ticket.workflow in {"clip", "audio", "blender", "transcribe"}:
            from pydantic import ValidationError

            from phobos.models import CreativeRequest

            try:
                CreativeRequest.model_validate_json(ticket.prompt)
            except ValidationError:
                raise HTTPException(
                    422, "Creative tasks require source/output JSON and valid times"
                ) from None
        return public_ticket(store.create(ticket))

    @app.get("/api/tickets", dependencies=owner)
    def list_tickets(project: str | None = None, limit: int = Query(default=50, ge=1, le=100)):
        if project is not None:
            validate_project(project)
        return [public_ticket(ticket) for ticket in store.list(project, limit)]

    @app.get("/api/tickets/{ticket_id}", dependencies=owner)
    def ticket_detail(ticket_id: int):
        return public_ticket(get_ticket(ticket_id))

    @app.get("/api/tickets/{ticket_id}/events", dependencies=owner)
    def ticket_events(ticket_id: int):
        get_ticket(ticket_id)
        return store.events(ticket_id)

    @app.post("/api/tickets/{ticket_id}/{action}", dependencies=owner)
    def transition(ticket_id: int, action: Literal["pause", "resume", "cancel", "retry"]):
        get_ticket(ticket_id)
        try:
            return public_ticket(store.transition(ticket_id, action))
        except Conflict as error:
            raise HTTPException(409, str(error)) from None

    @app.get("/api/artifacts/{ticket_id}", dependencies=owner)
    def artifact(ticket_id: int):
        ticket = get_ticket(ticket_id)
        path = settings.data_dir / "artifacts" / f"{ticket_id}.md"
        if ticket["status"] != "completed" or not path.is_file():
            raise HTTPException(404, "Completed artifact not found")
        return FileResponse(path, media_type="text/markdown", filename=f"phobos-{ticket_id}.md")

    @app.get("/api/memories/{project}", dependencies=owner)
    def memories(project: str):
        validate_project(project)
        return store.memories(project)

    @app.post("/api/memories/{project}", status_code=201, dependencies=owner)
    def remember(project: str, memory: MemoryCreate):
        validate_project(project)
        return store.remember(project, memory.content)

    @app.delete("/api/memories/{project}/{memory_id}", status_code=204, dependencies=owner)
    def forget(project: str, memory_id: int):
        validate_project(project)
        if not store.forget(project, memory_id):
            raise HTTPException(404, "Memory not found")

    @app.post("/api/feedback/{ticket_id}", status_code=201, dependencies=owner)
    def feedback(ticket_id: int, feedback: FeedbackCreate):
        get_ticket(ticket_id)
        store.feedback(ticket_id, feedback.rating, feedback.comment)
        return {"recorded": True, "promoted_to_memory": False}

    @app.get("/api/report", dependencies=owner)
    def report():
        return store.report()

    @app.get("/api/overview", dependencies=owner)
    def overview(project: str = "personal"):
        import time

        from phobos.skills import SkillLibrary

        validate_project(project)
        with store.connection() as db:
            counts = {
                row[0]: row[1]
                for row in db.execute(
                    "SELECT status,count(*) FROM tickets WHERE project=? GROUP BY status",
                    (project,),
                )
            }
            completed = db.execute(
                "SELECT count(*) FROM tickets WHERE project=? "
                "AND status='completed' AND updated>=?",
                (project, time.time() - 86400),
            ).fetchone()[0]
            memories_count = db.execute(
                "SELECT count(*) FROM memories WHERE project=?", (project,)
            ).fetchone()[0]
        seen = store.state("worker_seen")
        return {
            "project": project,
            "counts": counts,
            "completed_today": completed,
            "memories": memories_count,
            "skills": len(SkillLibrary(settings).available()),
            "worker_online": bool(seen and time.time() - float(seen) < 30),
            "gateway_configured": bool(settings.model),
            "model": settings.model,
            "telegram_configured": bool(settings.telegram_token and settings.telegram_users),
            "graph_enabled": settings.graph_memory,
            "auto_install": settings.skill_auto_install,
        }

    def learning_call(project: str, operation, *args):
        validate_project(project)
        try:
            return operation(project, *args)
        except KeyError:
            raise HTTPException(404, "Procedure not found") from None
        except Conflict as error:
            raise HTTPException(409, str(error)) from None

    @app.get("/api/procedures/{project}", dependencies=owner)
    def procedures(project: str):
        return learning_call(project, store.procedures)

    @app.post("/api/procedures/{project}", status_code=201, dependencies=owner)
    def create_procedure(project: str, procedure: ProcedureCreate):
        return learning_call(
            project,
            store.create_procedure,
            procedure.name,
            procedure.instructions,
            procedure.source_ticket,
        )

    @app.get("/api/procedures/{project}/{procedure_id}", dependencies=owner)
    def procedure(project: str, procedure_id: int):
        return learning_call(project, store.procedure, procedure_id)

    @app.post("/api/procedures/{project}/{procedure_id}/evaluate", dependencies=owner)
    def evaluate_procedure(project: str, procedure_id: int, evaluation: ProcedureEvaluation):
        return learning_call(
            project,
            store.evaluate_procedure,
            procedure_id,
            evaluation.ticket_id,
            evaluation.passed,
            evaluation.notes,
        )

    @app.post("/api/procedures/{project}/{procedure_id}/state/{action}", dependencies=owner)
    def procedure_state(project: str, procedure_id: int, action: Literal["activate", "retire"]):
        return learning_call(project, store.transition_procedure, procedure_id, action)

    @app.get("/api/skills", dependencies=owner)
    def skills():
        from phobos.skills import SkillLibrary

        return SkillLibrary(settings).available()

    @app.get("/api/skills/{name}", dependencies=owner)
    def skill(name: str, file: str = "SKILL.md"):
        from phobos.skills import SkillLibrary

        try:
            return SkillLibrary(settings).view(name, file)
        except (ValueError, OSError):
            raise HTTPException(404, "Skill file not found") from None

    @app.get("/api/skill-search", dependencies=owner)
    def skill_search(
        query: str = Query(min_length=2, max_length=500),
        project: str | None = None,
        online: bool = False,
    ):
        from phobos.skills import SkillLibrary

        if project:
            validate_project(project)
        try:
            return SkillLibrary(settings).search(query, project, online)
        except Exception as error:
            import httpx

            if isinstance(error, httpx.HTTPError):
                raise HTTPException(502, "Skill source unavailable") from None
            raise

    @app.post("/api/skill-install/{name}", dependencies=owner)
    def skill_install(name: str):
        import httpx

        from phobos.skills import SkillLibrary

        try:
            return SkillLibrary(settings).install(name)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except httpx.HTTPError:
            raise HTTPException(502, "Skill source unavailable") from None

    @app.get("/api/graph/{project}", dependencies=owner)
    def graph_recall(project: str, query: str = Query(min_length=1, max_length=2000)):
        from phobos.memory import GraphMemory

        validate_project(project)
        return GraphMemory(settings, store).recall(project, query)

    @app.post("/api/graph/{project}/index", dependencies=owner)
    def graph_index(project: str):
        from phobos.memory import GraphMemory

        validate_project(project)
        return GraphMemory(settings, store).index_code(project)

    return app
