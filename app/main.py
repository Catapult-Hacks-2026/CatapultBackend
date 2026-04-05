import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import init_db
from app.galileo.database import init_galileo_db
from app.galileo.router import router as galileo_router
from app.galileo.seed import seed_galileo_data
from app.routers.campaigns import router as campaigns_router
from app.routers.hotel_data import router as hotel_data_router
from app.routers.voice import router as voice_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: dict[str, list[WebSocket]] = {}

    async def connect(self, negotiation_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self.connections.setdefault(negotiation_id, []).append(ws)

    def disconnect(self, negotiation_id: str, ws: WebSocket) -> None:
        sockets = self.connections.get(negotiation_id, [])
        if ws in sockets:
            sockets.remove(ws)
        if not sockets and negotiation_id in self.connections:
            self.connections.pop(negotiation_id, None)

    async def broadcast(self, negotiation_id: str, data: dict) -> None:
        sockets = list(self.connections.get(negotiation_id, []))
        for socket in sockets:
            try:
                await socket.send_json(data)
            except Exception:
                self.disconnect(negotiation_id, socket)


manager = ConnectionManager()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_galileo_db()
    init_db()
    await seed_galileo_data()
    # Campaign graph is built on demand per campaign via POST /campaigns/{id}/start
    yield
    from app.core.shared_clients import close_shared_clients
    await close_shared_clients()


app = FastAPI(title="Autonomous AI Procurement Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(voice_router, prefix="/voice", tags=["voice"])
app.include_router(campaigns_router, prefix="/api", tags=["campaigns"])
app.include_router(hotel_data_router, prefix="/api/hotel-data", tags=["hotel-data"])
app.include_router(galileo_router, prefix="/api/galileo", tags=["galileo"])


@app.get("/")
async def health_check() -> dict:
    return {"status": "ok"}
