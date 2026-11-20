"""Budget API contracts. Money is entered in the trip currency, with no hidden FX conversion."""

from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

BudgetCategory = Literal["travel", "stay", "food", "activities", "other"]
BudgetCurrency = Literal["INR", "JPY", "USD", "EUR", "GBP", "AUD", "CAD"]


class BudgetSettingsUpdate(BaseModel):
    currency: BudgetCurrency
    target_amount: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=2)


class BudgetItemInput(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    category: BudgetCategory
    place_name: Optional[str] = Field(default=None, max_length=255)
    quantity: Decimal = Field(default=Decimal("1"), gt=0, le=1000, max_digits=8, decimal_places=2)
    unit_amount: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    is_included: bool = True

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Budget item label cannot be blank.")
        return value

    @field_validator("place_name")
    @classmethod
    def clean_place(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() or None if value is not None else None


class BudgetItemResponse(BaseModel):
    id: UUID
    source_key: Optional[str]
    scope: str
    category: BudgetCategory
    label: str
    place_name: Optional[str]
    quantity: Decimal
    unit_amount: Optional[Decimal]
    quote_text: Optional[str]
    estimate_amount: Optional[Decimal] = None
    estimate_note: Optional[str] = None
    is_current: bool
    is_included: bool
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BudgetResponse(BaseModel):
    trip_id: UUID
    currency: BudgetCurrency
    target_amount: Optional[Decimal]
    is_locked: bool = False
    locked_at: Optional[datetime] = None
    locked_total: Optional[Decimal] = None
    priced_total: Decimal
    remaining: Optional[Decimal]
    unpriced_count: int
    # priced_total plus suggested amounts for rows still missing a traveler amount
    projected_total: Decimal = Decimal("0.00")
    unestimated_count: int = 0
    review_count: int
    category_totals: dict[str, Decimal]
    place_totals: dict[str, Decimal]
    items: list[BudgetItemResponse]


class BudgetLockRequest(BaseModel):
    accept_suggestions: bool = False
