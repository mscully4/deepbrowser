from typing import Any

from langchain.agents.middleware.types import AgentState


class WebBrowserAgentState(AgentState[Any]):
    run_id: str
    iteration: int
