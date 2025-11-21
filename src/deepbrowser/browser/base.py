from abc import ABC, abstractmethod
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from deepbrowser.tagging.tags import BaseTag


class Viewport(BaseModel):
    width: int
    height: int


class PageDimensions(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )

    width: int
    height: int
    scroll_y: int
    scroll_x: int


class BrowserPageDetails(BaseModel):
    url: str
    title: str
    viewport: Viewport | None
    dimensions: PageDimensions


class ScreenshotDetails(BaseModel):
    b64_image: str
    error: Literal["", "unavailable"] = ""


class AsyncBrowserPage(ABC):
    @property
    @abstractmethod
    async def url(self) -> str:
        """
        The url of the page
        """

    @property
    @abstractmethod
    async def viewport(self) -> Viewport | None:
        """
        Returns the viewport of the current page
        """

    @property
    @abstractmethod
    async def dimensions(self) -> PageDimensions:
        """
        Returns the dimensions of the current page
        """

    @property
    @abstractmethod
    async def page_details(self) -> BrowserPageDetails:
        """
        Returns the details of the current page
        """

    @abstractmethod
    async def goto(self, url: str) -> None:
        """
        Navigate to the page at the given URL
        """

    @abstractmethod
    async def take_screenshot(self) -> ScreenshotDetails:
        """
        Takes a screenshot of the current page and returns it as base64-str
        """

    # @abstractmethod
    # async def run_js(self, js: str) -> Any:
    #     """
    #     Executes a JS script and returns the result
    #     """

    @abstractmethod
    async def tagify(self) -> dict[str, BaseTag]:
        """
        Run tagify script in all frames, returning element IDs
        """

    @abstractmethod
    async def untagify(self) -> None:
        """
        Reset all tagging state
        """

    @abstractmethod
    async def click(self, element_webrock_id: str) -> None:
        """
        Clicks on an element on the page
        """

    @abstractmethod
    async def enter_text(self, element_webrock_id: str, text: str) -> None:
        """
        Enters text into an element on the page
        """

    @abstractmethod
    async def press_key(self, key: str) -> None:
        """
        Presses a key on the keyboard
        """

    @abstractmethod
    async def focus(self, element_webrock_id: str) -> None:
        """
        Focus on an element on the page
        """

    @abstractmethod
    async def scroll(
        self, element_webrock_id: str, direction: Literal["up", "down", "left", "right"]
    ) -> None:
        """
        Scroll an element in the specified direction
        """

    @abstractmethod
    async def hover(self, x: int, y: int) -> None:
        """
        Hover at a position (x,y) on a page
        """

    @abstractmethod
    async def select(self, element_webrock_id: str, option: str) -> None:
        """
        Choose an option of a select element
        """


class BasePageTool(ABC, BaseModel):
    name: ClassVar[str]
    description: ClassVar[str]

    @abstractmethod
    async def arun(self, page: AsyncBrowserPage) -> None:
        """
        A method to asynchronously perform the tool's action
        """

    @classmethod
    def get_input_schema(cls) -> dict[str, Any]:
        return cls.model_json_schema(mode="serialization")
