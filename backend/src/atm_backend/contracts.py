from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")]
Reason = Annotated[str, Field(min_length=5, max_length=2000)]
Action = Literal[
    "BLOCK_CARD",
    "HOLD_TRANSFER",
    "FREEZE_DIGITAL_ACCESS",
    "FREEZE_ACCOUNT",
    "STEP_UP_AUTH",
    "NO_ACTION",
]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TransactionContext(Input):
    card_id: Identifier | None = None
    destination_account_id: Identifier | None = None
    merchant_category: Annotated[str, Field(pattern=r"^[0-9]{4}$")] | None = None
    device_id: Identifier | None = None
    location_country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] | None = None
    location_city: Annotated[str, Field(min_length=1, max_length=100)] | None = None


class TransactionInput(Input):
    external_id: Identifier
    account_id: Identifier
    amount_minor: Annotated[int, Field(strict=True, gt=0, le=1_000_000_000_000)]
    currency: Literal["SAR", "USD", "EUR", "GBP", "AED", "KWD", "BHD", "QAR", "OMR"]
    occurred_at: AwareDatetime
    channel: Literal[
        "CARD", "TRANSFER", "ATM", "ONLINE", "CASH", "BILL_PAYMENT", "WALLET", "PAYROLL"
    ]
    context: TransactionContext = Field(default_factory=TransactionContext)

    @field_validator("occurred_at")
    @classmethod
    def event_time(cls, value: datetime) -> datetime:
        value = value.astimezone(UTC)
        if value > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError("Future event exceeds clock tolerance")
        return value


class BatchItem(TransactionInput):
    idempotency_key: Identifier


class BatchInput(Input):
    items: Annotated[list[BatchItem], Field(min_length=1, max_length=100)]


class VersionInput(Input):
    expected_version: Annotated[int, Field(strict=True, ge=1)]


class AssignmentInput(VersionInput):
    analyst_id: UUID


class NoteInput(VersionInput):
    text: Reason


class InvestigationInput(VersionInput):
    operation: Literal["start", "cancel", "resume"]


class CallInput(VersionInput):
    participant_id: UUID
    language: Literal["en", "ar"]


class CallTransition(VersionInput):
    operation: Literal["answer", "decline", "end"]


class TurnInput(VersionInput):
    text: Annotated[str, Field(min_length=1, max_length=2000)]


class TranscriptionInput(VersionInput):
    audio_base64: Annotated[str, Field(min_length=4, max_length=87384)]


class ProposalInput(VersionInput):
    action: Action
    reason: Reason


class ApprovalInput(VersionInput):
    approved: StrictBool


class CancellationInput(VersionInput):
    reason: Reason


class DecisionInput(VersionInput):
    disposition: Literal["CONFIRMED_FRAUD", "LEGITIMATE", "INCONCLUSIVE"]
    reason: Reason


class PolicyInput(Input):
    daily_review_capacity: Annotated[int, Field(strict=True, ge=1, le=100000)] | None = None
    medium_threshold: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] = 0.35
    high_threshold: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] = 0.65
    critical_threshold: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] = 0.85

    @model_validator(mode="after")
    def ordered_bands(self) -> "PolicyInput":
        if not self.medium_threshold < self.high_threshold < self.critical_threshold:
            raise ValueError("Severity thresholds must be strictly increasing")
        return self

    threshold: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    reason: Reason


class ModelInput(Input):
    name: Identifier
    digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    simulation: StrictBool
    precision: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    recall: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class TrainingComplete(Input):
    model_id: UUID
    snapshot_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class DriftInput(Input):
    model_id: UUID
    statistic: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    sample_size: Annotated[int, Field(strict=True, ge=100)]
    window_end: AwareDatetime


class FindingInput(VersionInput):
    category: Literal["ACCOUNT_HISTORY", "CUSTOMER_CONTACT", "TRANSACTION_PATTERN", "OTHER"]
    summary: Reason
    recommendation: Literal["REVIEW", "CONTACT_CUSTOMER", "PROPOSE_ACTION", "NO_ACTION"]
    evidence_transaction_ids: Annotated[list[UUID], Field(max_length=20)] = Field(
        default_factory=list
    )


class VerificationInput(VersionInput):
    response: Literal["AUTHORIZED", "NOT_AUTHORIZED", "UNSURE"]


class TrainingCancel(Input):
    reason: Reason


class SampleTrafficInput(Input):
    offset: Literal[0, 10, 20, 30, 40]
