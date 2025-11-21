# DeepBrowser

AI-powered web browser automation framework that enables intelligent agents to interact with and control web browsers programmatically.

## Overview

DeepBrowser combines LLM-driven decision-making with browser automation to execute complex, multi-step web browsing tasks. It uses Playwright with Chrome DevTools Protocol (CDP) for browser control.

## Features

- **Intelligent Web Browsing** - LLM-driven browser automation with context-aware decision making
- **Browser Abstraction** - Multiple isolated browser contexts, multiple tabs per context, full browser control
- **DOM Element Tagging** - Automatic detection and annotation of interactive elements with numbered overlays
- **Screenshot Annotation** - Visual overlays showing clickable elements with numbered labels
- **Agent Middleware** - State tracking, artifact generation, and error handling during task execution
- **Structured Output** - Pydantic-based structured responses with retry logic

## Requirements

- Python 3.12+
- Chrome/Chromium browser
- Model access

## Installation

```bash
# Clone the repository
git clone <repo-url>
cd deepbrowser

# Install dependencies using uv
uv sync
```

### Playwright Setup

```bash
# Install browser binaries
uv run playwright install chromium
```

## Quick Start

```python
import asyncio
import boto3
from playwright.async_api import async_playwright

from deepbrowser.agent.builder import browser_agent_builder
from deepbrowser.browser.cdp.browser import AsyncCDPBrowser
from langchain_aws.chat_models import ChatBedrock
from deepbrowser.utils.logging import create_stream_logging_handler

async def main():
    # Setup logging
    create_stream_logging_handler()

    # Initialize AWS Bedrock client
    session = boto3.Session(region_name="us-east-2")
    bedrock_client = session.client("bedrock-runtime")

    async with async_playwright() as p:
        # Launch browser
        browser = await p.chromium.launch(headless=False)
        cdp = await browser.new_browser_cdp_session()

        # Setup LLM
        model = ChatBedrock(
            client=bedrock_client,
            model="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
        )

        # Create browser wrapper
        cdp_browser = await AsyncCDPBrowser.create(
            browser=browser,
            browser_cdp_session=cdp
        )

        # Build agent
        agent = browser_agent_builder(
            browser=cdp_browser,
            model=model,
            debug=False
        )

        # Execute task
        result = await agent(prompt="Go to google.com and search for 'python'")
        print(result)

asyncio.run(main())
```

## CLI Usage

```bash
# Run the test example
test
```

## Configuration

### Agent Builder Options

```python
agent = browser_agent_builder(
    browser=cdp_browser,
    model=model,
    additional_instructions="",      # Custom system instructions
    additional_tools=None,           # Extra tools beyond browser tools
    additional_middleware=None,      # Custom middleware
    response_format=None,            # Pydantic model for structured output
    subagents=None,                  # Sub-agent definitions
    debug=False                      # Enable debug logging
)
```

## API Reference

### AsyncCDPBrowser

Main browser controller for managing contexts and pages.

```python
# Create browser
browser = await AsyncCDPBrowser.create(browser, browser_cdp_session)

# Navigation
await browser.goto("https://example.com")

# Interactions (using annotation numbers from tagged screenshot)
await browser.click(5)                    # Click element #5
await browser.enter_text(3, "hello")      # Type in element #3
await browser.scroll(2, "down")           # Scroll element #2
await browser.select(4, "option-value")   # Select option in element #4

# Context management
await browser.create_new_context()
await browser.switch_to_context(0)
await browser.create_new_page()
await browser.switch_to_page(1)
```

### Page Tools

Available browser actions the agent can use:

- `goto` - Navigate to URL
- `click` - Click an element
- `enter_text` - Type text into an input
- `scroll` - Scroll an element or page
- `select` - Select dropdown option
- `focus` - Focus an element
- `hover` - Hover over an element
- `go_back` / `go_forward` - Browser history navigation
- `wait` - Wait for specified duration
- `screenshot` - Take a screenshot

### Browser Tools

Context and page management:

- `create_new_context` - Create isolated browser context
- `switch_to_context` - Switch active context
- `create_new_page` - Create new tab
- `switch_to_page` - Switch active tab
- `close_current_context` - Close context and all pages
- `close_current_page` - Close current tab

## Debug Artifacts

When running with debug enabled, artifacts are generated in `artifacts/<run_id>/`:

- `human/N.txt` - Formatted page state for iteration N
- `elements/N.json` - Element list and metadata
- `tagged/N.png` - Annotated screenshot with numbered overlays

## Development

### Setup

```bash
# Install dev dependencies
uv sync --all-groups

# Format code
uv run ruff format .

# Lint
uv run ruff check .

# Type check
uv run mypy src/
```

### Code Quality

The project uses:
- **Ruff** for linting and formatting
- **MyPy** in strict mode for type checking
- Python 3.12+ type hints throughout

## License

Do whatever you want, I don't care
