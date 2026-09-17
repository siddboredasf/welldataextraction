from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator


ReviewStatus = Literal["candidate", "needs_review", "approved", "rejected", "missing"]


class Evidence(BaseModel):
    document_id: str
    page: int = Field(ge=1)
    raw_value: str
    bbox: list[float] | None = None
    ocr_confidence: float | None = Field(default=None, ge=0, le=1)
    extraction_method: str


class CandidateField(BaseModel):
    raw_value: str | None = None
    normalized_value: str | int | float | None = None
    status: ReviewStatus
    review_reason: str | None = None
    evidence: Evidence | None = None


class PressureRange(BaseModel):
    lower_bar: float | None = Field(default=None, ge=0)
    upper_bar: float | None = Field(default=None, gt=0)
    unit: Literal["bar"] = "bar"
    raw_value: str | None = None

    @field_validator("upper_bar")
    @classmethod
    def validate_order(cls, upper: float | None, info):
        lower = info.data.get("lower_bar")
        if lower is not None and upper is not None and upper < lower:
            raise ValueError("upper_bar must be greater than or equal to lower_bar")
        return upper


class InstrumentSample(BaseModel):
    serial_number: CandidateField
    instrument_tag: CandidateField
    model: CandidateField
    pressure_range: PressureRange | None = None
    required_set_point_bar: CandidateField
    actual_set_point_bar: CandidateField
    test_gauge_number: CandidateField
    row_evidence: list[Evidence] = []


class TestCertificateCandidate(BaseModel):
    record_type: Literal["instrument_test_certificate_candidate"]
    document_id: str
    page: int = Field(ge=1)
    certificate_number: CandidateField
    certificate_date: CandidateField
    purchaser: CandidateField
    purchase_order: CandidateField
    instruments: list[InstrumentSample]
    review_status: Literal["pending", "complete"] = "pending"