from typing import Literal

from pydantic import BaseModel, Field

Workflow = Literal["assistant", "note", "report", "clip", "audio", "blender", "transcribe"]


class TicketCreate(BaseModel):
    project: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=24000)
    workflow: Workflow = "assistant"


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class FeedbackCreate(BaseModel):
    rating: Literal[-1, 1]
    comment: str = Field(default="", max_length=2000)


class ProcedureCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    instructions: str = Field(min_length=1, max_length=8000)
    source_ticket: int = Field(gt=0)


class ProcedureEvaluation(BaseModel):
    ticket_id: int = Field(gt=0)
    passed: bool
    notes: str = Field(min_length=1, max_length=2000)


class CreativeRequest(BaseModel):
    source: str = Field(min_length=1, max_length=1000)
    output: str = Field(min_length=1, max_length=1000)
    start: float = Field(default=0, ge=0, allow_inf_nan=False)
    duration: float = Field(default=30, gt=0, le=600, allow_inf_nan=False)
