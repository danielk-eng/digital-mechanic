"""Canonical fields published by organisers before the brief (chat + official Q&A).

Official OpenAPI may use different names. Map them in adapters.py only.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class CheckStatus(str, Enum):
    normal = "normal"
    anomaly = "anomaly"
    check_failed = "check_failed"


class RecordingQuality(str, Enum):
    good = "good"
    warning = "warning"
    bad = "bad"


class Priority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Vehicle(BaseModel):
    vehicle_id: str
    engine_type: str
    model: Optional[str] = None
    # Other fleet fields arrive on 26.11; keep extras out of the agent.


class FaultType(BaseModel):
    """F01…F14. Codes are synthetic and do not appear in the manuals."""

    code: str
    title: str


class CheckResult(BaseModel):
    vehicle_id: str
    session_id: str
    status: CheckStatus
    fault_code: Optional[str] = None
    fault_title: Optional[str] = None
    anomaly_score: Optional[float] = None
    confidence: Optional[float] = None
    quality: Optional[RecordingQuality] = None
    symptoms: Optional[str] = None
    model_version: Optional[str] = None


class SourcePage(BaseModel):
    document: str = Field(..., min_length=1)
    page: int = Field(..., ge=1)
    quote: Optional[str] = None

    @field_validator("document")
    @classmethod
    def document_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("document must be a non-empty official title")
        return v


class WorkItem(BaseModel):
    title: str
    source: SourcePage


class PartItem(BaseModel):
    title: str
    source: Optional[SourcePage] = None


class TicketDraft(BaseModel):
    vehicle_id: str
    session_id: str
    fault_code: Optional[str] = None
    diagnosis: str
    priority: Priority
    works: list[WorkItem] = Field(..., min_length=1)
    parts: list[PartItem] = Field(default_factory=list)
    source_pages: list[SourcePage] = Field(..., min_length=1)


class TicketAck(BaseModel):
    ticket_id: str


class PolicyDecision(str, Enum):
    repeat_recording = "repeat_recording"
    no_ticket_normal = "no_ticket_normal"
    propose_ticket = "propose_ticket"
    advise_teardown_no_ticket = "advise_teardown_no_ticket"
    propose_ticket_general_inspection = "propose_ticket_general_inspection"
