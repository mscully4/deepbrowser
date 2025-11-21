import asyncio
import base64
import logging
from contextlib import suppress
from typing import Any, Literal, cast

from playwright.async_api import BrowserContext, CDPSession

from deepbrowser.browser.base import (
    AsyncBrowserPage,
    BrowserPageDetails,
    PageDimensions,
    ScreenshotDetails,
    Viewport,
)
from deepbrowser.browser.cdp.types import DomSnapshot
from deepbrowser.tagging.elements import (
    CheckableInputElement,
    Element,
    Elements,
    SelectInputElement,
    TextInputElement,
)
from deepbrowser.tagging.shapes import Rect
from deepbrowser.tagging.tagify import (
    annotate_screenshot,
    extract_frame_bounds,
    get_elements_in_viewport,
    is_interactive,
    parse_adjacency_list,
    text_content,
)
from deepbrowser.tagging.tags import AnchorTag, BaseTag, InputTag, SelectTag
from deepbrowser.utils.cdp import object_group
from deepbrowser.utils.cdp.winkeycodes import KEY_TO_VK
from deepbrowser.utils.image_processing import make_not_available_image


# CSS properties requested in DOMSnapshot; used by visibility, interactivity, and text extraction
# helpers to identify clickable, scrollable, or visible elements without bloating snapshot data.
_QUERIED_STYLES = [
    "visibility",
    "display",
    "cursor",
    "border-left-width",
    "border-top-width",
    "border-right-width",
    "border-bottom-width",
    "padding-left",
    "padding-top",
    "padding-right",
    "padding-bottom",
    "overflow-x",
    "overflow-y",
    "white-space",
    # Additional useful properties
    "opacity",  # detect fully transparent elements
    "pointer-events",  # skip elements with pointer-events: none
    "z-index",  # assist stacking order heuristics
    "transform",  # detect translated or rotated elements
    "clip-path",  # identify clipped or masked content
]

_INNER_TEXT_MAX_LENGTH_DEFAULT = 100


class AsyncCDPBrowserPage(AsyncBrowserPage):
    def __init__(self, *, cdp_session: CDPSession, browser_context: BrowserContext) -> None:
        self._cdp_session = cdp_session
        self._browser_context = browser_context

        self._tagged_elements: list[Element] = []
        self._backend_node_id_point: dict[int, tuple[int, int]] = {}

    async def init(self) -> None:
        await self.enable_domains()
        await self._disable_web_auth_n()

    @classmethod
    async def create(
        cls, *, cdp_session: CDPSession, browser_context: BrowserContext
    ) -> "AsyncCDPBrowserPage":
        """A factory method to create this class that should be used instead of the constructor"""
        page = AsyncCDPBrowserPage(cdp_session=cdp_session, browser_context=browser_context)
        await page.init()
        return page

    @property
    async def page_id(self) -> str:
        info = await self._cdp_session.send("Target.getTargetInfo")
        page_id: str = info["targetInfo"]["targetId"]
        return page_id

    async def _disable_web_auth_n(self) -> None:
        await self._cdp_session.send("WebAuthn.enable", {"enableUI": False})
        await self._cdp_session.send(
            "WebAuthn.addVirtualAuthenticator",
            {
                "options": {
                    "protocol": "ctap2",
                    "transport": "usb",
                    "hasResidentKey": False,
                    "hasUserVerification": False,
                    "isUserVerified": False,
                }
            },
        )

    async def enable_domains(self) -> None:
        await self._cdp_session.send("Page.enable")
        await self._cdp_session.send("DOMSnapshot.enable")
        await self._cdp_session.send("DOM.enable")
        await self._cdp_session.send("Runtime.enable")

    async def _is_element_visible(
        self, element: Element, visible_rect: Rect
    ) -> tuple[int, int] | None:
        key_points = [
            # center point
            (
                element.bounds.x + element.bounds.width // 2,
                element.bounds.y + element.bounds.height // 2,
            ),
            # corners
            (element.bounds.x, element.bounds.y),
            (element.bounds.x + element.bounds.width, element.bounds.y),
            (element.bounds.x, element.bounds.y + element.bounds.height),
            (
                element.bounds.x + element.bounds.width,
                element.bounds.y + element.bounds.height,
            ),
        ]
        points_in_viewport = [p for p in key_points if visible_rect.contains(*p)]

        # Get root node info and point locations concurrently
        root_task = self._cdp_session.send(
            "DOM.describeNode",
            {"backendNodeId": element.backend_node_id, "depth": -1, "pierce": True},
        )

        point_tasks = [
            self._cdp_session.send(
                "DOM.getNodeForLocation",
                {
                    "x": int(point[0]),
                    "y": int(point[1]),
                },
            )
            for point in points_in_viewport
        ]

        # Wait for all async operations to complete
        try:
            root = await root_task
        except Exception:
            logging.exception(f"Determining visibility for {element}")
            return None

        # Process root node to get child IDs
        child_ids = set()
        q = [root["node"]]
        while q:
            node = q.pop(0)
            child_ids.add(node["backendNodeId"])
            q.extend(node.get("children", []))

        point_results = await asyncio.gather(*point_tasks, return_exceptions=True)
        for idx, result in enumerate(point_results):
            if isinstance(result, Exception):
                continue

            if result["backendNodeId"] in child_ids:  # type: ignore
                return points_in_viewport[idx]

        return None

    def _convert_to_tag(self, id_number: int, element: Element, inner_text: str) -> BaseTag:
        common_props: dict[str, Any] = {
            "html_id": element.attributes.get("id"),
            "html_class": element.attributes.get("class"),
        }
        if (
            element.can_scroll_up
            or element.can_scroll_down
            or element.can_scroll_left
            or element.can_scroll_right
        ):
            common_props.update(
                {
                    "can_scroll_up": element.can_scroll_up,
                    "can_scroll_down": element.can_scroll_down,
                    "can_scroll_left": element.can_scroll_left,
                    "can_scroll_right": element.can_scroll_right,
                }
            )
        if element.name == Elements.INPUT:
            value: str | None = None
            if isinstance(element, TextInputElement):
                value = element.text
            elif isinstance(element, CheckableInputElement):
                value = str(element.selected).lower()
            return InputTag(
                tag_name="INPUT",
                annotation_number=id_number,
                inner_text=inner_text,
                type=str(element.attributes.get("type")),
                placeholder=element.attributes.get("placeholder"),
                aria_label=element.attributes.get("aria-label"),
                value=value,
                **common_props,
            )
        if isinstance(element, SelectInputElement):
            return SelectTag(
                tag_name="SELECT",
                annotation_number=id_number,
                inner_text=inner_text,
                aria_label=element.attributes.get("aria-label"),
                options=element.options,
                **common_props,
            )
        if element.name.lower() == "a":
            return AnchorTag(
                tag_name="A",
                annotation_number=id_number,
                inner_text=inner_text,
                role=element.attributes.get("role"),
                aria_label=element.attributes.get("aria-label"),
                **common_props,
            )
        return BaseTag(
            tag_name=element.name,
            annotation_number=id_number,
            inner_text=inner_text,
            aria_label=None,
            **common_props,
        )

    @property
    async def url(self) -> str:
        result = await self._cdp_session.send("Page.getNavigationHistory")
        cur_idx = result["currentIndex"]
        return cast(str, result["entries"][cur_idx]["url"])

    @property
    async def viewport(self) -> Viewport | None:
        result = await self._cdp_session.send("Page.getLayoutMetrics")
        visual_viewport = result["cssVisualViewport"]

        return Viewport(
            width=int(visual_viewport["clientWidth"]),
            height=int(visual_viewport["clientHeight"]),
        )

    @property
    async def dimensions(self) -> PageDimensions:
        result = await self._cdp_session.send("Page.getLayoutMetrics")
        visual_viewport = result["cssVisualViewport"]

        return PageDimensions(
            width=int(result["cssContentSize"]["width"]),
            height=int(result["cssContentSize"]["height"]),
            scroll_x=int(visual_viewport["pageX"]),
            scroll_y=int(visual_viewport["pageY"]),
        )

    @property
    async def title(self) -> str:
        result = await self._cdp_session.send("Page.getNavigationHistory")
        cur_idx = result["currentIndex"]
        return cast(str, result["entries"][cur_idx]["title"])

    @property
    async def page_details(self) -> BrowserPageDetails:
        return BrowserPageDetails(
            url=await self.url,
            viewport=await self.viewport,
            dimensions=await self.dimensions,
            title=await self.title,
        )

    async def _get_visible_rect(self) -> Rect:
        page_info = await self.page_details
        return Rect(
            x=page_info.dimensions.scroll_x,
            y=page_info.dimensions.scroll_y,
            width=page_info.viewport.width if page_info.viewport else 0,
            height=page_info.viewport.height if page_info.viewport else 0,
        )

    async def goto(self, url: str) -> None:
        # use the CDP to navigate the page to a new URL
        # await self._cdp_session.send("Page.navigate", {"url": url})
        # Don't wait for navigation completion when interception rules might pause responses
        with suppress(TimeoutError):
            await asyncio.wait_for(self._cdp_session.send("Page.navigate", {"url": url}), 3)

    async def take_screenshot(self) -> ScreenshotDetails:
        try:
            screenshot = await asyncio.wait_for(
                self._cdp_session.send("Page.captureScreenshot", {"format": "png"}),
                timeout=5.0,
            )
            page_info = await asyncio.wait_for(self.page_details, timeout=5.0)
            visible_rect = Rect(
                x=page_info.dimensions.scroll_x,
                y=page_info.dimensions.scroll_y,
                width=page_info.viewport.width if page_info.viewport else 0,
                height=page_info.viewport.height if page_info.viewport else 0,
            )
            img = base64.b64decode(screenshot["data"])
            annotate_img = annotate_screenshot(img, visible_rect, self._tagged_elements)
            return ScreenshotDetails(b64_image=base64.b64encode(annotate_img).decode("utf-8"))
        except TimeoutError:
            # Fallback to a "not available" image if screenshot times out
            return ScreenshotDetails(b64_image=make_not_available_image(), error="unavailable")

    # async def run_js(self, js: str) -> Any:
    #     pass

    async def tagify(self) -> dict[str, Any]:
        """
        Identify all visible and interactive elements on the current page.

        This method captures a DOM snapshot via the Chrome DevTools Protocol (CDP),
        determines which elements are visible within the viewport, checks if they are
        interactable (e.g., clickable, scrollable, focusable), and converts them into
        typed tag models for later use (clicking, typing, etc.).
        """
        # we want to figure out all the interactable elements, and create a dict with their
        # information. First, we'll use CDP to take a "DOMSnapshot", which should give us
        # all the elements on the page and other relevant info like their paint order
        visible_rect = await self._get_visible_rect()
        snapshot: DomSnapshot = cast(
            DomSnapshot,
            await self._cdp_session.send(
                "DOMSnapshot.captureSnapshot",
                {
                    "computedStyles": _QUERIED_STYLES,
                    "includePaintOrder": True,
                    "includeDOMRects": True,
                },
            ),
        )

        # Compute bounding boxes for each frame/iframe in the snapshot
        frame_bounds = extract_frame_bounds(snapshot, _QUERIED_STYLES)

        # Prepare structures to hold parsed elements and their metadata
        elements: list[Element] = []  # List of Element objects in the viewport
        node_inner_text: dict[int, str] = {}  # Maps backendNodeId -> text content
        backend_node_id_point = {}  # Maps backendNodeId -> representative (x, y) point

        # --- Iterate through each document (main frame + iframes) in the snapshot ---
        for doc_idx, document in enumerate(snapshot["documents"]):
            # Get this document’s absolute bounding box from extract_frame_bounds
            doc_bounds = frame_bounds[snapshot["strings"][document["frameId"]]]

            # Skip frames that are off-screen or have no visible intersection
            if not doc_bounds or not visible_rect.intersects_with(doc_bounds):
                continue

            # Build adjacency list (parent -> children) for this document's node tree
            adjacency_list = parse_adjacency_list(document=document)

            # Identify elements that are currently within the viewport and visible
            # Includes bounding box clipping and style check
            in_viewport = get_elements_in_viewport(
                document=document,
                adjacency_list=adjacency_list,
                strings=snapshot["strings"],
                computed_styles=_QUERIED_STYLES,
                doc_absolute_bounds=doc_bounds,
                visible_rect=visible_rect,
                is_root_frame=doc_idx == 0,
            )

            # Filter down to elements considered "interactive" (links, inputs, buttons, etc.)
            interactive = [e for e in in_viewport.values() if is_interactive(e, in_viewport)]

            # --- Determine which of those are actually visible on-screen ---
            # This checks if any part of the element is visually rendered
            visible_tasks = [
                self._is_element_visible(element, visible_rect) for element in interactive
            ]
            visible_points = await asyncio.gather(*visible_tasks)

            # Collect elements that are both interactive and visually visible
            for e, pt in zip(interactive, visible_points):
                # Skip invisible elements
                if pt is None:
                    continue

                elements.append(e)
                backend_node_id_point[e.backend_node_id] = pt
                node_inner_text[e.backend_node_id] = text_content(
                    document,
                    snapshot["strings"],
                    _QUERIED_STYLES,
                    adjacency_list,
                    e.node_id,
                    max_length=_INNER_TEXT_MAX_LENGTH_DEFAULT,
                )

        self._tagged_elements = elements
        self._backend_node_id_point = backend_node_id_point
        as_tags = [
            self._convert_to_tag(i, e, node_inner_text.get(e.backend_node_id, ""))
            for i, e in enumerate(elements)
        ]
        return {str(i): t for i, t in enumerate(as_tags)}

    async def untagify(self) -> None:
        self._tagged_elements = []
        self._backend_node_id_point = {}

    async def click(self, element_webrock_id: str) -> None:
        element = self._tagged_elements[int(element_webrock_id)]
        abs_x, abs_y = self._backend_node_id_point[element.backend_node_id]
        x, y = (await self._get_visible_rect()).relative_point(abs_x, abs_y)
        await self._cdp_session.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseMoved",
                "x": x,
                "y": y,
            },
        )
        await asyncio.sleep(0.3)
        await self._cdp_session.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mousePressed",
                "button": "left",
                "x": x,
                "y": y,
                "clickCount": 1,
            },
        )
        await asyncio.sleep(0.1)
        await self._cdp_session.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseReleased",
                "button": "left",
                "x": x,
                "y": y,
                "clickCount": 1,
            },
        )
        await self._cdp_session.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseMoved",
                "x": x,
                "y": y,
            },
        )

    async def focus(self, element_webrock_id: str) -> None:
        element = self._tagged_elements[int(element_webrock_id)]
        await self._cdp_session.send(
            "DOM.focus",
            {
                "backendNodeId": element.backend_node_id,
            },
        )

    async def hover(self, x: int, y: int) -> None:
        await self._cdp_session.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseMoved",
                "x": x,
                "y": y,
            },
        )
        await asyncio.sleep(0.3)
        await self._cdp_session.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseMoved",
                "x": x,
                "y": y,
            },
        )

    async def scroll(
        self, element_webrock_id: str, direction: Literal["up", "down", "left", "right"]
    ) -> None:
        element = self._tagged_elements[int(element_webrock_id)]
        if direction not in ["up", "down", "left", "right"]:
            raise ValueError(f"Invalid direction: {direction}")

        if not getattr(element, f"can_scroll_{direction}"):
            raise ValueError(f"Element {element_webrock_id} cannot be scrolled {direction}")

        async with object_group(self._cdp_session) as obj_group:
            resolved = await self._cdp_session.send(
                "DOM.resolveNode",
                {
                    "backendNodeId": element.backend_node_id,
                    "objectGroup": obj_group,
                },
            )
            script = """
                function scrollElement(direction) {
                    var element = this;

                    // Get element's visible dimensions
                    // (for html elements, the clientRect is equal to the viewport dimensions)
                    const elementHeight = element.clientHeight;
                    const elementWidth = element.clientWidth;

                    // Calculate 90% of visible dimensions
                    const scrollHeightAmount = elementHeight * 0.9;
                    const scrollWidthAmount = elementWidth * 0.9;

                    switch (direction.toLowerCase()) {
                        case 'up':
                            element.scrollBy({
                                top: -scrollHeightAmount,
                            });
                            break;
                        case 'down':
                            element.scrollBy({
                                top: scrollHeightAmount,
                            });
                            break;
                        case 'left':
                            element.scrollBy({
                                left: -scrollWidthAmount,
                            });
                            break;
                        case 'right':
                            element.scrollBy({
                                left: scrollWidthAmount,
                            });
                            break;
                        default:
                            console.error('Invalid direction. Use: up, down, left, or right');
                    }
                }
            """
            await self._cdp_session.send(
                "Runtime.callFunctionOn",
                {
                    "functionDeclaration": script,
                    "objectId": resolved["object"]["objectId"],
                    "arguments": [{"value": direction}],
                    "returnByValue": True,
                },
            )

    async def enter_text(self, element_webrock_id: str, text: str) -> None:
        await self.focus(element_webrock_id)
        await self._cdp_session.send(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "commands": ["selectAll", "delete"]},
        )
        await self._cdp_session.send(
            "Input.dispatchKeyEvent",
            {"type": "keyUp", "commands": ["selectAll", "delete"]},
        )
        await self._cdp_session.send(
            "Input.insertText",
            {
                "text": text,
            },
        )

    async def press_key(self, key: str) -> None:
        key_code = KEY_TO_VK[key.lower()]
        await self._cdp_session.send(
            "Input.dispatchKeyEvent",
            {
                "type": "keyDown",
                "windowsVirtualKeyCode": key_code,
                "code": "PageDown",
                "key": "PageDown",
            },
        )
        await self._cdp_session.send(
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "windowsVirtualKeyCode": key_code,
                "code": "PageDown",
                "key": "PageDown",
            },
        )

    async def select(self, element_webrock_id: str, option: str) -> None:
        element = self._tagged_elements[int(element_webrock_id)]
        if not isinstance(element, SelectInputElement):
            raise ValueError(f"Element {element_webrock_id} is not a select element")

        if option not in element.options:
            raise ValueError(
                f"Option {option} is not valid for select element {element_webrock_id} "
                f"(valid options: {element.options}"
            )

        option_index = element.options.index(option)
        option_value = element.option_values[option_index]

        async with object_group(self._cdp_session) as obj_group:
            resolved = await self._cdp_session.send(
                "DOM.resolveNode",
                {
                    "backendNodeId": element.backend_node_id,
                    "objectGroup": obj_group,
                },
            )
            script = """
                function setSelectValue(value) {
                    var element = this;

                    element.value = value;
                    element.dispatchEvent(new Event('change'));
                }
            """
            await self._cdp_session.send(
                "Runtime.callFunctionOn",
                {
                    "functionDeclaration": script,
                    "objectId": resolved["object"]["objectId"],
                    "arguments": [{"value": option_value}],
                    "returnByValue": True,
                },
            )

    # def generate_page_tools(self) -> list[BaseTool]:
    #     tool_functions = [
    #         self.click,
    #         self.enter_text,
    #         self.focus,
    #         self.goto,
    #         self.hover,
    #         self.scroll,
    #         self.select,
    #         self.press_key,
    #     ]

    #     tools = [tool(func) for func in tool_functions]
    #     return tools

    # @classmethod
    # def page_tools(cls) -> list[Any]:
    #     return [
    #         cls.click,
    #         cls.enter_text,
    #         cls.focus,
    #         cls.goto,
    #         cls.hover,
    #         cls.scroll,
    #         cls.select,
    #         cls.press_key,
    #     ]
