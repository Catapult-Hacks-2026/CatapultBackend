from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import init_db
from app.routers.campaigns import router as campaigns_router
from app.routers.voice import router as voice_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield
    from app.core.shared_clients import close_shared_clients
    await close_shared_clients()


app = FastAPI(title="Catapult26 Hotel Rate Negotiation Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(voice_router, prefix="/voice", tags=["voice"])
app.include_router(campaigns_router, prefix="/api", tags=["campaigns"])


@app.get("/")
async def health_check() -> dict:
    return {"status": "ok"}
