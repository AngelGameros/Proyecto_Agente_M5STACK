"""Esquemas Pydantic (entrada / salida) de la API."""
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from api.models import FoodStatus, ImageType


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- users ----------
class UserCreate(BaseModel):
    name: str
    email: str
    password: str = Field(min_length=4)
    phone: Optional[str] = None


class UserOut(ORM):
    id: str
    name: str
    email: str
    phone: Optional[str]
    created_at: datetime


# ---------- refrigerators ----------
class RefrigeratorCreate(BaseModel):
    user_id: str
    name: str
    brand: Optional[str] = None
    esp32_device_id: Optional[str] = None
    capacity_liters: Optional[int] = None
    is_active: bool = True


class RefrigeratorOut(ORM):
    id: str
    user_id: str
    name: str
    brand: Optional[str]
    esp32_device_id: Optional[str]
    capacity_liters: Optional[int]
    is_active: bool
    created_at: datetime


# ---------- purchase_tickets ----------
class TicketCreate(BaseModel):
    user_id: str
    refrigerator_id: str
    store_name: Optional[str] = None
    purchase_date: Optional[date] = None
    ticket_image_url: Optional[str] = None
    raw_text_ocr: Optional[str] = None
    processed_by_ai: bool = False


class TicketUpdate(BaseModel):
    store_name: Optional[str] = None
    purchase_date: Optional[date] = None
    ticket_image_url: Optional[str] = None
    raw_text_ocr: Optional[str] = None
    processed_by_ai: Optional[bool] = None


class TicketOut(ORM):
    id: str
    user_id: str
    refrigerator_id: str
    store_name: Optional[str]
    purchase_date: Optional[date]
    ticket_image_url: Optional[str]
    raw_text_ocr: Optional[str]
    processed_by_ai: bool
    created_at: datetime


# ---------- foods ----------
class FoodCreate(BaseModel):
    refrigerator_id: str
    name: str
    category: Optional[str] = None
    quantity: float = 1
    unit: Optional[str] = "pz"
    purchase_date: Optional[date] = None
    expiration_date: Optional[date] = None
    status: FoodStatus = FoodStatus.fresh


class FoodOut(ORM):
    id: str
    refrigerator_id: str
    name: str
    category: Optional[str]
    quantity: float
    unit: Optional[str]
    purchase_date: Optional[date]
    expiration_date: Optional[date]
    status: FoodStatus
    created_at: datetime
    consumed_at: Optional[datetime]


# ---------- food_images ----------
class FoodImageCreate(BaseModel):
    food_id: Optional[str] = None
    ticket_id: Optional[str] = None
    image_url: str
    image_type: ImageType = ImageType.product
    ocr_extracted_text: Optional[str] = None
    ai_analysis: Optional[dict[str, Any]] = None


class FoodImageOut(ORM):
    id: str
    food_id: Optional[str]
    ticket_id: Optional[str]
    image_url: str
    image_type: ImageType
    ocr_extracted_text: Optional[str]
    ai_analysis: Optional[dict[str, Any]]


class UploadOut(BaseModel):
    bucket: str
    url: str
