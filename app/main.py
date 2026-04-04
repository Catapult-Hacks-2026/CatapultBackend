from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import init_db
from app.routers.campaigns import router as campaigns_router
from app.routers.negotiations import router as negotiations_router
from app.routers.voice import router as voice_router
from app.routers.webhooks import router as webhooks_router


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
    init_db()
    # Campaign graph is built on demand per campaign via POST /campaigns/{id}/start
    yield


app = FastAPI(title="Autonomous AI Procurement Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(negotiations_router, prefix="/negotiations", tags=["negotiations"])
app.include_router(webhooks_router, prefix="/webhooks", tags=["webhooks"])
app.include_router(voice_router, prefix="/voice", tags=["voice"])
app.include_router(campaigns_router, prefix="/api", tags=["campaigns"])


@app.get("/")
async def health_check() -> dict:
    return {"status": "ok"}
