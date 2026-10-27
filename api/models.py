"""Modelos SQLAlchemy basados en el diagrama 'DIAGRAMA DE BASE DE DATOS'
(flujo de tickets: users, refrigerators, purchase_tickets, foods, food_images)."""
import enum
import uuid
from datetime import datetime

from sqlalchemy import (JSON, Boolean, Column, Date, DateTime, Enum, ForeignKey,
                        Integer, Numeric, String, Text, create_engine)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

import config

engine = create_engine(config.DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def _uuid() -> str:
    return str(uuid.uuid4())


class FoodStatus(str, enum.Enum):
    fresh = "fresh"
    expiring_soon = "expiring_soon"
    expired = "expired"
    consumed = "consumed"


class ImageType(str, enum.Enum):
    ticket = "ticket"
    product = "product"
    label = "label"


class User(Base):
    __tablename__ = "users"
    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(120), nullable=False)
    email = Column(String(160), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    phone = Column(String(30))
    created_at = Column(DateTime, default=datetime.utcnow)
    refrigerators = relationship("Refrigerator", back_populates="user")


class Refrigerator(Base):
    __tablename__ = "refrigerators"
    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    name = Column(String(120), nullable=False)
    brand = Column(String(80))
    esp32_device_id = Column(String(80))
    capacity_liters = Column(Integer)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="refrigerators")
    foods = relationship("Food", back_populates="refrigerator")


class PurchaseTicket(Base):
    __tablename__ = "purchase_tickets"
    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    refrigerator_id = Column(String(36), ForeignKey("refrigerators.id"), nullable=False)
    store_name = Column(String(120))
    purchase_date = Column(Date)
    ticket_image_url = Column(Text)
    raw_text_ocr = Column(Text)
    processed_by_ai = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    images = relationship("FoodImage", back_populates="ticket")


class Food(Base):
    __tablename__ = "foods"
    id = Column(String(36), primary_key=True, default=_uuid)
    refrigerator_id = Column(String(36), ForeignKey("refrigerators.id"), nullable=False)
    name = Column(String(120), nullable=False)
    category = Column(String(60))
    quantity = Column(Numeric(10, 2), default=1)
    unit = Column(String(20))
    purchase_date = Column(Date)
    expiration_date = Column(Date)
    status = Column(Enum(FoodStatus), default=FoodStatus.fresh)
    created_at = Column(DateTime, default=datetime.utcnow)
    consumed_at = Column(DateTime, nullable=True)
    refrigerator = relationship("Refrigerator", back_populates="foods")
    images = relationship("FoodImage", back_populates="food")


class FoodImage(Base):
    __tablename__ = "food_images"
    id = Column(String(36), primary_key=True, default=_uuid)
    food_id = Column(String(36), ForeignKey("foods.id"), nullable=True)
    ticket_id = Column(String(36), ForeignKey("purchase_tickets.id"), nullable=True)
    image_url = Column(Text, nullable=False)
    image_type = Column(Enum(ImageType), default=ImageType.product)
    ocr_extracted_text = Column(Text)
    ai_analysis = Column(JSON)  # JSONB en PostgreSQL
    food = relationship("Food", back_populates="images")
    ticket = relationship("PurchaseTicket", back_populates="images")


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
