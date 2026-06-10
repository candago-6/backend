from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, SessionLocal, engine
from app.routers import auth, users
from app.seed import seed_default_admin

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Service Manager", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)


@app.on_event("startup")
def on_startup() -> None:
    db = SessionLocal()
    try:
        seed_default_admin(db)
    finally:
        db.close()


@app.get("/api/v1/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "service-manager"}
