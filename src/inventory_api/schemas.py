from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from inventory_api.models import MAX_STOCK

Quantity = Annotated[StrictInt, Field(ge=1, le=MAX_STOCK)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProductCreate(RequestModel):
    sku: str = Field(min_length=2, max_length=40, pattern=r"^[A-Z0-9][A-Z0-9_-]*$")
    name: str = Field(min_length=1, max_length=120)
    initial_stock: Annotated[StrictInt, Field(ge=0, le=MAX_STOCK)] = 0

    @field_validator("sku", mode="before")
    @classmethod
    def normalize_sku(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class AdjustmentCreate(RequestModel):
    delta: Annotated[StrictInt, Field(ge=-MAX_STOCK, le=MAX_STOCK)]
    reason: str = Field(min_length=3, max_length=200)

    @field_validator("delta")
    @classmethod
    def nonzero_delta(cls, value: int) -> int:
        if value == 0:
            raise ValueError("A stock adjustment must be nonzero")
        return value


class ReservationCreate(RequestModel):
    product_id: UUID
    quantity: Quantity


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    sku: str
    name: str
    on_hand: int
    reserved: int
    available: int
    created_at: datetime


class ProductPage(BaseModel):
    items: list[ProductOut]
    limit: int
    offset: int


class ReservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    product_id: UUID
    quantity: int
    status: Literal["active", "fulfilled", "cancelled"]
    created_at: datetime
    updated_at: datetime


class MovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: UUID
    delta: int
    on_hand_after: int
    kind: Literal["opening", "adjustment", "fulfillment"]
    reason: str
    reservation_id: UUID | None
    created_at: datetime


class MovementPage(BaseModel):
    items: list[MovementOut]
    limit: int
    offset: int


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[dict[str, object]] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthOut(BaseModel):
    status: Literal["alive", "ready"]
