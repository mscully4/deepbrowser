from logging import Logger
from typing import Any, Callable, Literal, TypedDict, cast
from uuid import uuid4

from langchain_core.tools import BaseTool, tool
from playwright.async_api import Browser, BrowserContext, CDPSession
from pydantic import BaseModel

from deepbrowser.browser.cdp.page import AsyncCDPBrowserPage
from deepbrowser.exceptions import DeepBrowserException


# from morphling_web_browser.network.monitor import CDPNetworkMonitor
# from morphling_web_browser.xss_context.listener import CDPXSSContextListener

logger = Logger(__name__)

DEFAULT_CONTEXT_ID = "default"


class CDPBrowserConfig(BaseModel):
    cdp_url: str


class AsyncCDPBrowserContextData(TypedDict):
    id: str
    context: BrowserContext


class AsyncCDPBrowserPageData(TypedDict):
    id: str
    context_id: str
    page: AsyncCDPBrowserPage
    cdp_session: CDPSession
    # xss_context_listener: CDPXSSContextListener | None


class AsyncCDPBrowser:
    def __init__(
        self,
        *,
        browser: Browser,
        browser_cdp_session: CDPSession,
        # enable_xss_context_listener: bool = False,
        # enable_network_monitor: bool = False,
    ):
        self._browser = browser
        self._browser_cdp_session = browser_cdp_session

        # self._enable_xss_context_listener = enable_xss_context_listener

        # self._network_monitor: CDPNetworkMonitor | None = None
        # if enable_network_monitor:
        # self._network_monitor = CDPNetworkMonitor(cdp_session=self._browser_cdp_session)

        self._contexts: dict[str, AsyncCDPBrowserContextData] = {}
        self._pages: dict[str, AsyncCDPBrowserPageData] = {}

        self._active_context: str | None = None
        self._active_page: str | None = None

    async def init(self) -> None:
        # Check for a default context and load it in if it exists
        if len(self._browser.contexts) == 1:
            context = self._browser.contexts[0]
            self._contexts[DEFAULT_CONTEXT_ID] = {
                "id": DEFAULT_CONTEXT_ID,
                "context": context,
            }
            self._active_context = DEFAULT_CONTEXT_ID

            if len(context.pages) == 1:
                page = context.pages[0]
                cdp_session = await context.new_cdp_session(page)
                cdp_page = AsyncCDPBrowserPage(
                    browser_context=page.context, cdp_session=cdp_session
                )
                # if self._enable_xss_context_listener:
                #     await cdp_page.register_xss_listener()

                page_id = await cdp_page.page_id
                self._active_page = page_id

                data: AsyncCDPBrowserPageData = {
                    "id": page_id,
                    "context_id": DEFAULT_CONTEXT_ID,
                    "cdp_session": cdp_session,
                    "page": cdp_page,
                    # Omitting this for now
                    # "network_monitor": None,
                    # "xss_context_listener": cdp_page.xss_listener,
                }
                self._pages[page_id] = data

                self._active_page = page_id

        # if self._network_monitor:
        #     await self._network_monitor.register_event_listeners()

    @classmethod
    async def create(
        cls,
        *,
        browser: Browser,
        browser_cdp_session: CDPSession,
        # enable_xss_context_listener: bool = False,
        # enable_network_monitor: bool = False,
    ) -> "AsyncCDPBrowser":
        """A factory method to create the Browser that should be used instead of the constructor"""
        cdp_browser = AsyncCDPBrowser(
            browser=browser,
            browser_cdp_session=browser_cdp_session,
            # enable_xss_context_listener=enable_xss_context_listener,
            # enable_network_monitor=enable_network_monitor,
        )
        await cdp_browser.init()
        return cdp_browser

    # @property
    # def network_monitor(self):
    #     return self._network_monitor

    async def get_browser_state(self) -> dict[str, Any]:
        """Describes the current state of the browser, including the active context and page"""
        context = self.get_active_context()
        page_data = self.get_active_page()
        return {
            "context": {context["id"]} if context else None,
            "page": {
                "id": page_data["id"],
                "details": await page_data["page"].page_details,
            }
            if page_data
            else None,
        }

    async def create_browser_context(self) -> str:
        """Creates a new browser context and sets it to be the active context"""
        context_id = str(uuid4())
        context = await self._browser.new_context(ignore_https_errors=True)

        self._contexts[context_id] = {"id": context_id, "context": context}

        self._active_context = context_id
        return context_id

    async def list_browser_contexts(self) -> list[str]:
        """Lists all browser contexts"""
        return list(self._contexts.keys())

    async def set_active_browser_context_and_page(
        self, browser_context_id: str, page_id: str
    ) -> None:
        """Sets the active browser context to the specified browser context id"""
        self._active_context = browser_context_id
        self._active_page = page_id

    def get_active_context(self) -> AsyncCDPBrowserContextData | None:
        if not self._active_context:
            return None

        context = self._contexts.get(self._active_context)

        if not context:
            raise DeepBrowserException("Invalid active context")

        return context

    async def create_page(self) -> str:
        """Creates a new browser page within the active context and sets it to be the active page"""
        context_data = self.get_active_context()
        if not context_data:
            raise DeepBrowserException("No active context set")

        context = context_data["context"]

        page = await context.new_page()
        cdp_session = await context.new_cdp_session(page)

        cdp_page = AsyncCDPBrowserPage(browser_context=page.context, cdp_session=cdp_session)

        await cdp_page.init()
        page_id = await cdp_page.page_id

        data: AsyncCDPBrowserPageData = {
            "id": page_id,
            "context_id": context_data["id"],
            "cdp_session": cdp_session,
            "page": cdp_page,
        }
        self._pages[page_id] = data

        self._active_page = page_id

        return page_id

    async def get_page(self, page_id: str) -> AsyncCDPBrowserPageData | None:
        return self._pages.get(page_id)

    def get_active_page(self) -> AsyncCDPBrowserPageData | None:
        if not self._active_page:
            return None

        return self._pages[self._active_page]

    def get_active_page_or_throw(self) -> AsyncCDPBrowserPageData:
        if not self._active_page:
            raise RuntimeError("No active page set")

        return self._pages[self._active_page]

    async def list_pages(self) -> dict[str, Any]:
        """Lists all browser pages"""
        return {k: await v["page"].page_details for (k, v) in self._pages.items()}

    async def set_active_page(self, page_id: str) -> None:
        "Sets the active page to the specified page id"
        self._active_page = page_id

    @property
    def browser_tools(self) -> list[BaseTool]:
        tool_methods = [
            self.get_browser_state,
            self.list_browser_contexts,
            self.create_browser_context,
            self.set_active_browser_context_and_page,
            self.list_pages,
            self.create_page,
        ]

        tools: list[BaseTool] = [
            tool(name_or_callable=cast(Callable[..., Any], func)) for func in tool_methods
        ]
        return tools

    # Not ideal, but we need to have pass-through methods for all the page tools
    # TODO: explore ways around this
    async def click(self, annotation_number: str) -> None:
        """Clicks the selected element on the active page"""
        page_data = self.get_active_page_or_throw()
        return await page_data["page"].click(annotation_number)

    async def enter_text(self, annotation_number: str, text: str) -> None:
        """Enters text in the selected element on the active page"""
        page_data = self.get_active_page_or_throw()
        return await page_data["page"].enter_text(annotation_number, text)

    async def press_key(self, key: str) -> None:
        """Presses a key on the active page"""
        page_data = self.get_active_page_or_throw()
        return await page_data["page"].press_key(key)

    async def select(self, annotation_number: str, option: str) -> None:
        """Selects an element on the active page"""
        page_data = self.get_active_page_or_throw()
        return await page_data["page"].select(annotation_number, option)

    async def goto(self, url: str) -> None:
        """Navigates to a url on the active page"""
        page_data = self.get_active_page_or_throw()
        return await page_data["page"].goto(url)

    async def scroll(
        self, annotation_number: str, direction: Literal["up", "down", "left", "right"]
    ) -> None:
        """Scrolls the active page in the given direction"""
        page_data = self.get_active_page_or_throw()
        return await page_data["page"].scroll(annotation_number, direction)

    async def focus(self, annotation_number: str) -> None:
        """Focuses on the provided element on the active page"""
        page_data = self.get_active_page_or_throw()
        return await page_data["page"].focus(annotation_number)

    async def hover(self, x: int, y: int) -> None:
        """Hovers on the selected element on the active page"""
        page_data = self.get_active_page_or_throw()
        return await page_data["page"].hover(x, y)

    @property
    def page_tools(self) -> list[BaseTool]:
        # Commenting out some unneeded tools for now
        tool_methods = [
            self.click,
            self.enter_text,
            # self.focus,
            self.goto,
            # self.hover,
            # self.scroll,
            # self.select,
            # self.press_key,
        ]

        tools: list[BaseTool] = [
            tool(name_or_callable=cast(Callable[..., Any], func)) for func in tool_methods
        ]
        return tools
