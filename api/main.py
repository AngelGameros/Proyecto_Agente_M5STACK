"""API REST SmartFridge (FastAPI) basada en el diagrama de base de datos.

Ejecutar:  uvicorn api.main:app --reload
Docs:      http://127.0.0.1:8000/docs
"""
import hashlib
from datetime import datetime

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

import config
import storage
from api import schemas
from api.models import (Food, FoodImage, FoodStatus, PurchaseTicket, Refrigerator,
                        User, get_db, init_db)
from api.whatsapp import router as whatsapp_router

app = FastAPI(
    title="SmartFridge API",
    description="APIs del diagrama: users, refrigerators, purchase_tickets, foods, food_images. "
                "Las imágenes se guardan en MinIO.",
    version="1.0.0",
)
app.include_router(whatsapp_router)



@app.on_event("startup")
def _startup():
    init_db()
    for b in (config.MINIO_BUCKET_TICKETS, config.MINIO_BUCKET_FOODS):
        try:
            storage.ensure_bucket(b)
        except Exception as e:  # MinIO apagado: la API sigue funcionando
            print(f"[WARN] MinIO no disponible ({e})")


def _get_or_404(db: Session, model, obj_id: str):
    obj = db.get(model, obj_id)
    if not obj:
        raise HTTPException(404, f"{model.__tablename__} {obj_id} no encontrado")
    return obj


def _save(db: Session, obj):
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@app.get("/", tags=["health"])
def root():
    return {"status": "ok", "docs": "/docs"}


# ------------------------------ users ------------------------------
@app.post("/users", response_model=schemas.UserOut, status_code=201, tags=["users"])
def create_user(data: schemas.UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter_by(email=data.email).first():
        raise HTTPException(409, "El email ya existe")
    pw = hashlib.sha256(data.password.encode()).hexdigest()
    return _save(db, User(name=data.name, email=data.email, phone=data.phone, password_hash=pw))


@app.get("/users", response_model=list[schemas.UserOut], tags=["users"])
def list_users(email: str | None = None, db: Session = Depends(get_db)):
    q = db.query(User)
    if email:
        q = q.filter_by(email=email)
    return q.all()


@app.get("/users/{user_id}", response_model=schemas.UserOut, tags=["users"])
def get_user(user_id: str, db: Session = Depends(get_db)):
    return _get_or_404(db, User, user_id)


# --------------------------- refrigerators --------------------------
@app.post("/refrigerators", response_model=schemas.RefrigeratorOut, status_code=201,
          tags=["refrigerators"])
def create_refrigerator(data: schemas.RefrigeratorCreate, db: Session = Depends(get_db)):
    _get_or_404(db, User, data.user_id)
    return _save(db, Refrigerator(**data.model_dump()))


@app.get("/refrigerators", response_model=list[schemas.RefrigeratorOut], tags=["refrigerators"])
def list_refrigerators(user_id: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Refrigerator)
    if user_id:
        q = q.filter_by(user_id=user_id)
    return q.all()


# -------------------------- purchase_tickets ------------------------
@app.post("/purchase_tickets", response_model=schemas.TicketOut, status_code=201,
          tags=["purchase_tickets"])
def create_ticket(data: schemas.TicketCreate, db: Session = Depends(get_db)):
    _get_or_404(db, User, data.user_id)
    _get_or_404(db, Refrigerator, data.refrigerator_id)
    return _save(db, PurchaseTicket(**data.model_dump()))


@app.get("/purchase_tickets", response_model=list[schemas.TicketOut], tags=["purchase_tickets"])
def list_tickets(user_id: str | None = None, db: Session = Depends(get_db)):
    q = db.query(PurchaseTicket)
    if user_id:
        q = q.filter_by(user_id=user_id)
    return q.order_by(PurchaseTicket.created_at).all()


@app.patch("/purchase_tickets/{ticket_id}", response_model=schemas.TicketOut,
           tags=["purchase_tickets"])
def update_ticket(ticket_id: str, data: schemas.TicketUpdate, db: Session = Depends(get_db)):
    t = _get_or_404(db, PurchaseTicket, ticket_id)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(t, k, v)
    return _save(db, t)


# ------------------------------- foods ------------------------------
@app.post("/foods", response_model=schemas.FoodOut, status_code=201, tags=["foods"])
def create_food(data: schemas.FoodCreate, db: Session = Depends(get_db)):
    _get_or_404(db, Refrigerator, data.refrigerator_id)
    return _save(db, Food(**data.model_dump()))


@app.get("/foods", response_model=list[schemas.FoodOut], tags=["foods"])
def list_foods(refrigerator_id: str | None = None, status: FoodStatus | None = None,
               db: Session = Depends(get_db)):
    q = db.query(Food)
    if refrigerator_id:
        q = q.filter_by(refrigerator_id=refrigerator_id)
    if status:
        q = q.filter_by(status=status)
    return q.all()


@app.post("/foods/{food_id}/consume", response_model=schemas.FoodOut, tags=["foods"])
def consume_food(food_id: str, db: Session = Depends(get_db)):
    f = _get_or_404(db, Food, food_id)
    f.status, f.consumed_at = FoodStatus.consumed, datetime.utcnow()
    return _save(db, f)


# ---------------------------- food_images ---------------------------
@app.post("/food_images", response_model=schemas.FoodImageOut, status_code=201,
          tags=["food_images"])
def create_food_image(data: schemas.FoodImageCreate, db: Session = Depends(get_db)):
    if data.food_id:
        _get_or_404(db, Food, data.food_id)
    if data.ticket_id:
        _get_or_404(db, PurchaseTicket, data.ticket_id)
    return _save(db, FoodImage(**data.model_dump()))


@app.get("/food_images", response_model=list[schemas.FoodImageOut], tags=["food_images"])
def list_food_images(ticket_id: str | None = None, food_id: str | None = None,
                     db: Session = Depends(get_db)):
    q = db.query(FoodImage)
    if ticket_id:
        q = q.filter_by(ticket_id=ticket_id)
    if food_id:
        q = q.filter_by(food_id=food_id)
    return q.all()


# ------------------------------ uploads -----------------------------
@app.post("/uploads", response_model=schemas.UploadOut, tags=["uploads"])
async def upload_file(file: UploadFile = File(...),
                      bucket: str = Query(config.MINIO_BUCKET_TICKETS),
                      prefix: str = Query("")):
    """Sube una imagen a MinIO y devuelve su URL pública."""
    data = await file.read()
    url = storage.upload_bytes(bucket, data, file.filename or "file.png", prefix)
    return {"bucket": bucket, "url": url}


@app.get("/stats", tags=["health"])
def stats(db: Session = Depends(get_db)):
    return {
        "users": db.query(User).count(),
        "refrigerators": db.query(Refrigerator).count(),
        "purchase_tickets": db.query(PurchaseTicket).count(),
        "foods": db.query(Food).count(),
        "food_images": db.query(FoodImage).count(),
    }
