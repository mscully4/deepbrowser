from langchain_core.tools import tool


@tool
def think_aloud(thought: str) -> None:
    """Think aloud"""
    pass
