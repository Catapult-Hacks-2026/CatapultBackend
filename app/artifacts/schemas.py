from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RateMatrixItem(BaseModel):
    roomOrFareType: str
    negotiatedRateUSD: float | str
    discountFromBAR: str


class ContractParties(BaseModel):
    clientName: str
    vendorName: str


class ContractTerm(BaseModel):
    startDate: str
    endDate: str


class ContractCriticalClauses(BaseModel):
    inventoryGuarantee: str
    blackoutDates: list[str] = Field(default_factory=list)
    cancellationPolicy: str


class BillingAndSettlement(BaseModel):
    method: str


class NegotiatedRateAgreement(BaseModel):
    documentTitle: str = "Corporate Negotiated Rate Agreement - 2026"
    galileoReferenceId: str
    parties: ContractParties
    term: ContractTerm
    rateMatrix: list[RateMatrixItem] = Field(default_factory=list)
    criticalClauses: ContractCriticalClauses
    concessions: list[str] = Field(default_factory=list)
    billingAndSettlement: BillingAndSettlement


class EmailAttachment(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"
    data: bytes


class ReceiptArtifacts(BaseModel):
    contract_json: dict[str, Any]
    json_path: str
    pdf_path: str
    email_sent_to: str = ""
