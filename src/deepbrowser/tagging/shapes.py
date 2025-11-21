import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from playwright.async_api import CDPSession
from pydantic import BaseModel, field_validator


@asynccontextmanager
async def object_group(cdp_session: CDPSession) -> AsyncGenerator[str, None]:
    group_id = str(uuid.uuid4())
    try:
        yield group_id
    finally:
        await cdp_session.send("Runtime.releaseObjectGroup", {"objectGroup": group_id})


class Rect(BaseModel):
    x: int
    y: int
    width: int
    height: int

    @field_validator("x", "y", "width", "height", mode="before")
    def convert_to_int(cls, v: int | float) -> int:
        if isinstance(v, float):
            return int(v)
        return v

    def intersects_with(self, other: "Rect") -> bool:
        x_intersects = (other.x <= self.x < other.x + other.width) or (
            self.x < other.x <= (self.x + self.width)
        )
        y_intersects = (other.y <= self.y < other.y + other.height) or (
            self.y < other.y <= (self.y + self.height)
        )
        return x_intersects and y_intersects

    def contains(self, x: int, y: int) -> bool:
        return (self.x <= x <= self.x + self.width) and (self.y <= y <= self.y + self.height)

    def contains_rect(self, rect: "Rect") -> bool:
        return self.contains(rect.x, rect.y) and self.contains(
            rect.x + rect.width, rect.y + rect.height
        )

    def translate(self, x: int, y: int) -> "Rect":
        return Rect(x=self.x + x, y=self.y + y, width=self.width, height=self.height)

    def relative_point(self, x: int, y: int) -> tuple[int, int]:
        return (x - self.x, y - self.y)

    @classmethod
    def from_cdp(cls, rect: tuple[int, int, int, int]) -> "Rect":
        return cls(x=rect[0], y=rect[1], width=rect[2], height=rect[3])

    def clip_to(self, rect: "Rect") -> "Rect":
        x = max(self.x, rect.x)
        y = max(self.y, rect.y)
        width = min(self.x + self.width, rect.x + rect.width) - x
        height = min(self.y + self.height, rect.y + rect.height) - y

        return Rect(x=x, y=y, width=width, height=height)
