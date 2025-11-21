import time
from logging import getLogger
from textwrap import dedent
from typing import Any, TypeVar, cast

from langchain.chat_models import BaseChatModel
from langchain_aws.chat_models import ChatBedrock
from langchain_core.language_models.base import LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.prompt_values import ChatPromptValue
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel


logger = getLogger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)

# Some models tend to mess up output format (claude), so this provides
# a mechanism for the model to retry, while having access to the error message
_parsing_error_message_format_str = dedent("""
You have previously generated output that did not conform to the provided schema
Fix your error!

## Previous Output
{output}

## Error
{error}
""")


def structured_output_runnable_with_retries(
    *,
    chat_template: ChatPromptTemplate,
    model: BaseChatModel,
    output_model: type[TModel],
    invoke_args: dict[str, Any],
    max_retries: int = 3,
) -> TModel:
    """
    A function that wraps a structured output runnable and retries if the model provides
    incorrect output
    """
    for i in range(1, max_retries + 1):
        sequence = chat_template | model.with_structured_output(output_model, include_raw=True)
        logger.info(f"Runnable attempt: {i} for model: {output_model.__name__}")

        response = cast(dict[str, Any], sequence.invoke(invoke_args))
        logger.info("Usage metadata: %s", response["raw"].usage_metadata)

        # If the run was successful, we'll get a Pydantic Model here
        if "parsed" in response and isinstance(response["parsed"], output_model):
            logger.info("Parsing was successful...returning")
            return response["parsed"]

        # If not, check with a parsing error. Add the raw output and error to the prompt
        raw = cast(AIMessage, response["raw"]).tool_calls[0]["args"]
        if "parsing_error" in response:
            logger.info("Parsing error detected...retrying")
            parsing_error = response["parsing_error"]
            logger.info("Error: %s", parsing_error)
            error_message = _parsing_error_message_format_str.format(
                attempt=i, error=parsing_error, output=raw
            )
            chat_template.append(HumanMessage(content=error_message))
            continue

        # This should never happen, but if it does, then throw an error
        raise RuntimeError(f"Sequence failed with unexpected error: {response}")

    raise RuntimeError("Max retries exceeded")


def normalize_input(input: LanguageModelInput) -> list[BaseMessage]:
    if isinstance(input, str):
        return [HumanMessage(content=input)]
    if isinstance(input, ChatPromptValue):
        return input.to_messages()
    if isinstance(input, BaseMessage):
        return [input]
    if isinstance(input, list) and all(isinstance(m, BaseMessage) for m in input):
        return cast(list[BaseMessage], input)
    return []


class ThrottledChatBedrock(ChatBedrock):
    """A Langchain ChatBedrock client that pauses between model calls to avoid throttling"""

    def __init__(self, sleep_seconds: float = 0, **kwargs: Any):
        super().__init__(**kwargs)
        self._sleep_seconds = sleep_seconds

    def invoke(
        self,
        input: LanguageModelInput,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> AIMessage:
        time.sleep(self._sleep_seconds)

        return super().invoke(input, config=config, **kwargs)

    async def ainvoke(
        self,
        input: LanguageModelInput,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> AIMessage:
        return self.invoke(input, config=config, **kwargs)
