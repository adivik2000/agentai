"""
API functions for the agentai package
"""
from typing import Callable

import openai
import requests
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, retry_unless_exception_type

from .conversation import Conversation, Message
from .openai_function import ToolRegistry

logger.disable(__name__)

class InvalidInputError(Exception):
    pass

retry_unless_invalid = retry(retry=retry_unless_exception_type(InvalidInputError), stop=stop_after_attempt(3))

@retry_unless_invalid
def chat_complete(
    messages: list[Message], model: str, function_registry: ToolRegistry = None, return_function_params: bool = False
):
    if openai.api_key is None:
        raise InvalidInputError("Please set openai.api_key and try again")
    if not isinstance(messages, list) or len(messages) == 0 or not isinstance(messages[0], Message):
        raise InvalidInputError("Please provide a list of Message dictionaries")

    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + openai.api_key,
    }
    json_data = {"model": model, "messages": messages}
    if function_registry is not None:
        functions = function_registry.get_all_function_information()
        logger.debug(f"functions: {functions}")
        json_data.update({"functions": functions})

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=json_data,
    )
    response.raise_for_status()
    if return_function_params:
        message = response.json()["choices"][0]
        if message["finish_reason"] == "function_call":
            return response
        else:
            raise Exception(f"Unexpected message: {message}")
    else:
        content = response.json()["choices"][0]["message"]["content"]
        if content is None:
            raise Exception(f"OpenAI API returned unexpected output: {response.json()}")
        return response


def get_function_arguments(message, conversation: Conversation, function_registry: ToolRegistry, model: str):
    function_arguments = {}
    if message["finish_reason"] == "function_call":
        arguments = message["message"]["function_call"]["arguments"]
        try:
            function_arguments = eval(arguments)
        except SyntaxError:
            print("Syntax error, trying again")
            response = chat_complete(
                conversation.history, function_registry=function_registry, model=model
            )
            message = response.json()["choices"][0]
            function_arguments = get_function_arguments(
                message, conversation, function_registry=function_registry, model=model
            )
        return function_arguments
    raise Exception(f"Unexpected message: {message}")


@retry_unless_invalid
def chat_complete_execute_fn(
    conversation: Conversation,
    function_registry: ToolRegistry,
    callable_function: Callable,
    model: str,
):
    response = chat_complete(
        conversation.history,
        function_registry=function_registry,
        model=model,
        return_function_params=True,
    )
    message = response.json()["choices"][0]
    function_arguments = get_function_arguments(
        message=message, conversation=conversation, function_registry=function_registry, model=model
    )
    logger.debug(f"function_arguments: {function_arguments}")
    results = callable_function(**function_arguments)
    logger.debug(f"results: {results}")
    conversation.add_message(role="function", name=callable_function.__name__, content=str(results))

    response = chat_complete(
        conversation.history,
        function_registry=function_registry,
        model=model,
        return_function_params=False,
    )
    assistant_message = response.json()["choices"][0]["message"]["content"]
    logger.debug(f"assistant_message: {assistant_message}")
    conversation.add_message(role="assistant", content=assistant_message)
    return assistant_message
