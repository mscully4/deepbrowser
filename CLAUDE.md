# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build and Development Commands

```bash
# Install dependencies
uv sync

# Install browser binaries
uv run playwright install chromium

# Run the test example
test

# Format code
uv run ruff format .

# Lint
uv run ruff check .

# Type check
uv run mypy src/
```

## Architecture Overview

DeepBrowser is an AI-powered browser automation framework that enables vision-capable LLMs to control web browsers through annotated screenshots.

### Core Flow

1. **Screenshot + DOM Capture**: CDP captures page screenshots and DOM snapshots
2. **Element Tagging**: Interactive elements are identified and annotated with numbered overlays
3. **LLM Decision**: Vision model receives annotated screenshot + element list, decides actions
4. **Browser Execution**: Actions executed via CDP (click, type, scroll, navigate)

### Key Layers

**Agent Layer** (`agent/`)
- `builder.py`: Agent factory using `deepagents` framework - creates the async `invoke()` function
- `middleware.py`: `BrowserMiddleware` intercepts model calls to inject page state (screenshot, elements, page details)
- `prompts.py`: System prompt defining agent behavior and browser interaction guidelines
- `state.py`: `WebBrowserAgentState` extends LangChain's AgentState with run_id and iteration

**Browser Layer** (`browser/`)
- `base.py`: Abstract interfaces (`AsyncBrowserPage`, `BasePageTool`)
- `cdp/browser.py`: `AsyncCDPBrowser` - main controller managing contexts and pages
- `cdp/page.py`: `AsyncCDPBrowserPage` - low-level CDP operations (DOM snapshots, screenshots, interactions)

**Tagging Layer** (`tagging/`)
- `tagify.py`: Main engine - extracts interactive elements from DOM, annotates screenshots with numbered labels
- `elements.py`: Element models (`Element`, `TextInputElement`, `SelectInputElement`, `CheckableInputElement`)
- `shapes.py`: `Rect` class for bounds manipulation with multi-frame coordinate transformation

### Multi-Context Architecture

- **Browser contexts**: Isolated profiles (separate cookies, storage, auth)
- **Pages**: Multiple tabs per context sharing session data
- **Active state**: Only one active context and one active page at a time
- All agent actions target the active page; use `switch_to_context`/`switch_to_page` to change

### Tool Registration

Browser tools are LangChain tool decorators defined in `cdp/browser.py` (context/page management) and `cdp/page.py` (interactions). The `browser_agent_builder` collects these via `browser.browser_tools` and `browser.active_page.page_tools`.

### Debug Artifacts

When `debug=True`, artifacts are saved to `artifacts/<run_id>/`:
- `human/N.txt`: Formatted page state
- `elements/N.json`: Element list
- `tagged/N.png`: Annotated screenshot

## Code Style

- Python 3.12+ with strict MyPy
- Ruff for formatting (double quotes, 100 char line length)
- Pydantic v2 for all data models
- Async throughout (asyncio + Playwright)
