import asyncio

import boto3
from playwright.async_api import async_playwright

from deepbrowser.agent.builder import browser_agent_builder
from deepbrowser.browser.cdp.browser import AsyncCDPBrowser
from deepbrowser.utils.langchain import ThrottledChatBedrock


def main():
    asyncio.run(test())


async def test():
    print("FOO")

    ses = boto3.Session()
    bedrock_client = ses.client("bedrock-runtime")

    async with async_playwright() as p:
        # Launch headless Chromium
        browser = await p.chromium.launch(headless=True)
        cdp = await browser.new_browser_cdp_session()

        # Enable the Network domain and log outgoing requests
        # await cdp.send("Network.enable")

        # # Clean up
        # await context.close()
        # await browser.close()
        CLAUDE_SONNET_4_CRI = "us.anthropic.claude-sonnet-4-20250514-v1:0"
        model = ThrottledChatBedrock(
            client=bedrock_client,
            sleep_seconds=15,
            model=CLAUDE_SONNET_4_CRI,
        )

        cdp_browser = await AsyncCDPBrowser.create(browser=browser, browser_cdp_session=cdp)

        session = browser_agent_builder(browser=cdp_browser, model=model, debug=True)
        result = await session(prompt="Go to amazon.com")
        print(result)
