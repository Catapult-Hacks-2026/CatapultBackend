import asyncio
from collections import defaultdict
from typing import AsyncGenerator

from app.models.schemas import WorkerEvent

_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)


async def publish_worker_event(campaign_id: str, event: WorkerEvent) -> None:
    if not campaign_id:
        return
    for queue in list(_subscribers.get(campaign_id, set())):
        await queue.put(event)


async def subscribe_worker_events(campaign_id: str) -> AsyncGenerator[WorkerEvent, None]:
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers[campaign_id].add(queue)
    try:
        while True:
            event = await queue.get()
            yield event
    finally:
        _subscribers[campaign_id].discard(queue)
        if not _subscribers[campaign_id]:
            _subscribers.pop(campaign_id, None)
