import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from playwright.async_api import CDPSession


@asynccontextmanager
async def object_group(cdp_session: CDPSession) -> AsyncGenerator[str, None]:
    group_id = str(uuid.uuid4())
    try:
        yield group_id
    finally:
        await cdp_session.send("Runtime.releaseObjectGroup", {"objectGroup": group_id})


