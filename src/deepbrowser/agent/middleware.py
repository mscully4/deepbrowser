import base64
import json
import time
from logging import getLogger
from pathlib import Path
from typing import Any, Awaitable, Callable, cast, override

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse, wrap_tool_call
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import HumanMessage, ToolMessage

from deepbrowser.agent.prompts import BROWSER_TASK_PROMPT
from deepbrowser.agent.state import WebBrowserAgentState
from deepbrowser.browser.cdp.browser import AsyncCDPBrowser
from deepbrowser.browser.cdp.page import AsyncCDPBrowserPage


ARTIFACTS_DIR = "artifacts"
BROWSER_MSG_TAG = "BROWSER_MSG"


logger = getLogger(__name__)


async def _make_browser_message(
    browser: AsyncCDPBrowser, page: AsyncCDPBrowserPage, state: WebBrowserAgentState
) -> HumanMessage:
    await page.untagify()

    tagged_elements = await page.tagify()
    logger.info("Collected %s tagged elements", len(tagged_elements))
    annotated_screenshot = await page.take_screenshot()

    page_details = await page.page_details

    # paused_messages: list[PausedMessage] = []
    # interception_rules: list = []
    # if browser.network_monitor:
    #     paused_messages.extend(await browser.network_monitor.get_paused_messages())
    #     interception_rules.extend(
    #         await browser.network_monitor.get_interception_rules()
    #     )

    # events = await page.xss_listener.get_events() if page.xss_listener else None

    elements = {k: v.model_dump() for (k, v) in tagged_elements.items()}
    human_msg = BROWSER_TASK_PROMPT.format(
        tagged_elements=elements,
        page_details=page_details.model_dump(),
        # xss_events=events,
        # existing_proxy_rules=[rule.model_dump() for rule in interception_rules],
        # paused_proxy_messages=[msg.model_dump() for msg in paused_messages],
    )

    human_msg_file: Path = (
        Path(ARTIFACTS_DIR) / state["run_id"] / "human" / f"{state['iteration']}.txt"
    )
    human_msg_file.parent.mkdir(exist_ok=True, parents=True)
    with human_msg_file.open("w") as fh:
        fh.write(human_msg)

    elements_file: Path = (
        Path(ARTIFACTS_DIR) / state["run_id"] / "elements" / f"{state['iteration']}.json"
    )
    elements_file.parent.mkdir(exist_ok=True, parents=True)
    with elements_file.open("w") as fh:
        json.dump(elements, fh, indent=4)

    screenshot_file = Path(ARTIFACTS_DIR) / state["run_id"] / "tagged" / f"{state['iteration']}.png"
    screenshot_file.parent.mkdir(exist_ok=True, parents=True)
    with screenshot_file.open("wb") as fh:
        fh.write(base64.b64decode(annotated_screenshot.b64_image))

    return HumanMessage(
        content=[
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "data": annotated_screenshot.b64_image,
                    "media_type": "image/webp",
                },
            },
            human_msg,
        ],
        # Add a "tag" here for when we need to modify message history
        additional_kwargs={"TYPE": BROWSER_MSG_TAG},
    )


class BrowserMiddleware(AgentMiddleware[WebBrowserAgentState]):
    state_schema = WebBrowserAgentState

    def __init__(self, browser: AsyncCDPBrowser, sleep_time: float = 3):
        super().__init__()
        self._browser = browser
        self._sleep_time = sleep_time

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        time.sleep(self._sleep_time)
        # Run the tagging process on the current page and generate a message for it
        active_page_data = self._browser.get_active_page()
        state = cast(WebBrowserAgentState, request.state)
        if active_page_data:
            active_page = active_page_data["page"]
            new_msg = await _make_browser_message(self._browser, active_page, state)
            request.messages.append(new_msg)

        return await handler(request)

    @override
    async def aafter_model(self, state: WebBrowserAgentState, runtime: Any) -> dict[str, Any]:
        return {
            **state,
            "iteration": 0 if "iteration" not in state else state["iteration"] + 1,
        }


# The below function works, however the typing seems to be mis-aligned
@wrap_tool_call  # type: ignore
async def handle_tool_errors(
    request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolMessage]]
) -> ToolMessage:
    try:
        return await handler(request)  # run the tool
    except Exception as e:
        # Hand back a ToolMessage so the model can recover/decide next step
        return ToolMessage(
            content=f"Tool error: {e}",
            tool_call_id=request.tool_call["id"],
        )
