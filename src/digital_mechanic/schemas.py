"""Canonical models. Field names follow the official contract (TZ v6, section 5)."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class CheckStatus(str, Enum):
    normal = "normal"
    anomaly = "anomaly"
    check_failed = "check_failed"


class SignalQuality(str, Enum):
    good = "good"
    warning = "warning"
    bad = "bad"


class Urgency(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Vehicle(BaseModel):
    vehicle_id: str
    garage_no: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    vehicle_type: Optional[str] = None
    engine_model: str
    fuel: Optional[str] = None

    def model_post_init(self, _ctx) -> None:
        # The customer's mock has 12 MAZ trucks with ЯМЗ-536 and no model: the plant fits ЯМЗ-536 to the 5340С2/6312С3
        # family, whose manual (5440С9-3902002 РЭ) we index; the mock files carry the same value (audit/MANUAL_COVERAGE.md).
        if not self.model and (self.make or "").upper() == "МАЗ" and self.engine_model == "ЯМЗ-536":
            self.model = "МАЗ-5340С2"

    @property
    def label(self) -> str:
        name = self.model or self.make or ""
        return f"{self.vehicle_id} · {name} · {self.engine_model}"


class CheckResult(BaseModel):
    vehicle_id: str
    session_id: str
    status: CheckStatus
    anomaly_type: Optional[str] = None
    anomaly_title: Optional[str] = None
    anomaly_score: Optional[float] = None
    confidence: Optional[float] = None
    signal_quality: SignalQuality
    description: str = ""
    model_version: str = ""
    checked_at: Optional[str] = None


class Reference(BaseModel):
    manual: str = Field(min_length=1)
    pdf_page: int = Field(ge=1)


class TicketRequest(BaseModel):
    """Exactly the Mock TOiR contract: every field required."""

    vehicle_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    anomaly_type: str = Field(pattern=r"^F(0[1-9]|1[0-4])$")
    conclusion: str = Field(min_length=1)
    urgency: Urgency
    operations: list[str] = Field(min_length=1)
    spare_parts: list[str]
    references: list[Reference] = Field(min_length=1)


class TicketAck(BaseModel):
    ticket_id: str
    status: str = "created"


class PolicyDecision(str, Enum):
    repeat_recording = "repeat_recording"
    no_ticket_normal = "no_ticket_normal"
    propose_ticket = "propose_ticket"
    verify_then_ticket = "verify_then_ticket"  # low confidence / noisy record: inspect unit first
