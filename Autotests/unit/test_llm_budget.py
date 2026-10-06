"""Unit tests for LLM token-budget handling in providers/ (no container, network or token)."""
import importlib.util
import json
import logging
import os
import sys
import types
from types import SimpleNamespace as NS

import pytest

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
_PROVIDERS_DIR = os.path.join(_REPO_ROOT, "providers")

if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _load_modules():
    openai_stub = types.ModuleType("openai")
    openai_stub.OpenAI = object

    config_stub = types.ModuleType("config")
    config_stub.config_get_by_key = lambda key, default=None: default

    providers_spec = importlib.util.spec_from_file_location(
        "providers", os.path.join(_REPO_ROOT, "src", "providers.py")
    )
    providers_stub = importlib.util.module_from_spec(providers_spec)

    stubs = {"openai": openai_stub, "config": config_stub, "providers": providers_stub}
    saved = {name: sys.modules.get(name) for name in list(stubs) + ["lib_llm_ext"]}
    sys.modules.update(stubs)
    try:
        providers_spec.loader.exec_module(providers_stub)
        loaded = {}
        for name in ("lib_llm_ext", "openrouter", "openai_provider", "asione"):
            file_name = "openai.py" if name == "openai_provider" else f"{name}.py"
            spec = importlib.util.spec_from_file_location(name, os.path.join(_PROVIDERS_DIR, file_name))
            module = importlib.util.module_from_spec(spec)
            if name == "lib_llm_ext":
                sys.modules["lib_llm_ext"] = module
            spec.loader.exec_module(module)
            loaded[name] = module
        return loaded
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


_MODULES = _load_modules()
llm = _MODULES["lib_llm_ext"]
openrouter = _MODULES["openrouter"]
openai_provider = _MODULES["openai_provider"]
asione = _MODULES["asione"]

def request(max_tokens=6000, reasoning="medium"):
    return (_MODULES["lib_llm_ext"].LLMRequest()
            .with_messages([
                _MODULES["lib_llm_ext"].LLMMessage()
                .with_role("user")
                .with_content("Write an empty line to /tmp/paths.txt")
            ])
            .with_max_tokens(max_tokens)
            .with_reasoning_mode(reasoning)
            .with_tools([
                _MODULES["lib_llm_ext"].LLMTool()
                .with_name("send")
                .with_description("Send a message")
                .with_parameters([
                    _MODULES["lib_llm_ext"].LLMToolParameter()
                    .with_name("content")
                ])
            ]))


def chat_response(content, finish_reason, completion_tokens=6000, reasoning_tokens=6000):
    tool_calls = []
    if content is not None:
        tool_calls.append(NS(
            id="call-1",
            function=NS(name="send", arguments=json.dumps({"content": content})),
        ))
    return NS(
        id="response-1",
        choices=[NS(index=0, finish_reason=finish_reason,
                    message=NS(role="assistant", content=None,
                               tool_calls=tool_calls))],
        usage=NS(prompt_tokens=2900, completion_tokens=completion_tokens, total_tokens=2900 + completion_tokens,
                 prompt_tokens_details=NS(cached_tokens=2600),
                 completion_tokens_details=NS(reasoning_tokens=reasoning_tokens)),
    )


def responses_response(output_text, status, reason=None, output_tokens=120, reasoning_tokens=120):
    output = []
    if output_text is not None:
        output.append(NS(type="function_call", id="call-1", name="send",
                         arguments=json.dumps({"content": output_text})))
    return NS(
        id="response-1",
        output=output,
        output_text=output_text,
        status=status,
        incomplete_details=NS(reason=reason) if reason else None,
        usage=NS(input_tokens=1200, output_tokens=output_tokens, total_tokens=1200 + output_tokens,
                 input_tokens_details=NS(cached_tokens=0),
                 output_tokens_details=NS(reasoning_tokens=reasoning_tokens)),
    )


class FakeCreate:
    """Returns the queued responses in order and records every call's kwargs."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_openrouter(create, model="z-ai/glm-5.2"):
    provider = openrouter.OpenRouterProviderImpl("OpenRouter", "OPENROUTER_API_KEY", model, "https://openrouter.ai/api/v1")
    provider._client = NS(chat=NS(completions=NS(create=create)))
    return provider


def make_openai(create):
    provider = openai_provider.OpenAIProviderImpl("OpenAI", "OPENAI_API_KEY", "gpt-5.5", "https://api.openai.com/v1")
    provider._client = NS(responses=NS(create=create))
    return provider


def make_asione(create):
    provider = asione.ASIOneProviderImpl("ASIOne", "ASIONE_API_KEY", "asi1-ultra", "https://api.asi1.ai/v1")
    provider._client = NS(chat=NS(completions=NS(create=create)))
    return provider


def sent_text(reply):
    """Content of a single structured `send` tool call, or None."""
    if len(reply.calls) != 1 or reply.calls[0].name != "send":
        return None
    return reply.calls[0].arguments.get("content")


def swallowed_errors(caplog):
    return [r for r in caplog.records if r.exc_info]


def test_normal_reply_is_returned_after_a_single_call():
    create = FakeCreate(chat_response("hi", "stop", completion_tokens=40, reasoning_tokens=30))
    assert sent_text(make_openrouter(create).chat(request())) == "hi"
    assert len(create.calls) == 1


def test_empty_reply_out_of_budget_is_explained():
    create = FakeCreate(chat_response(None, "length"))
    assert sent_text(make_openrouter(create).chat(request())) == llm.LLM_EMPTY_RESPONSE_MESSAGE


def test_empty_reply_with_stop_is_not_blamed_on_the_budget(caplog):
    create = FakeCreate(chat_response(None, "stop", completion_tokens=0, reasoning_tokens=0))
    assert make_openrouter(create).chat(request()).calls == []
    assert swallowed_errors(caplog) == []


def test_openai_empty_reply_out_of_budget_is_explained():
    create = FakeCreate(responses_response(None, "incomplete", "max_output_tokens"))
    assert sent_text(make_openai(create).chat(request(max_tokens=120))) == llm.LLM_EMPTY_RESPONSE_MESSAGE


def test_openai_empty_reply_without_incomplete_reason_returns_empty(caplog):
    create = FakeCreate(responses_response(None, "completed", output_tokens=0, reasoning_tokens=0))
    assert make_openai(create).chat(request()).calls == []
    assert swallowed_errors(caplog) == []


def test_asione_empty_reply_out_of_budget_is_explained():
    create = FakeCreate(chat_response(None, "length"))
    assert sent_text(make_asione(create).chat(request())) == llm.LLM_EMPTY_RESPONSE_MESSAGE


@pytest.mark.parametrize("effort, max_tokens, want_budget, want_enabled", [
    ("medium", 6000, 3000, True),
    ("high", 6000, 4800, True),
    ("medium", 120, 60, True),
    ("none", 6000, 0, False),
])
def test_asione_reasoning_budget(effort, max_tokens, want_budget, want_enabled):
    create = FakeCreate(chat_response("hi", "stop", completion_tokens=40, reasoning_tokens=30))
    make_asione(create).chat(request(max_tokens=max_tokens, reasoning=effort))
    body = create.calls[0]["extra_body"]
    assert body["thinking_budget"] == want_budget
    assert body["enable_thinking"] is want_enabled


@pytest.mark.parametrize("effort, want", [
    ("medium", {"enabled": True, "effort": "medium", "exclude": True}),
    ("none", {"enabled": False, "effort": "none", "exclude": True}),
])
def test_openrouter_reasoning_body(effort, want):
    create = FakeCreate(chat_response("hi", "stop", completion_tokens=40, reasoning_tokens=30))
    make_openrouter(create).chat(request(reasoning=effort))
    assert create.calls[0]["extra_body"]["reasoning"] == want


def test_usage_is_logged_at_info(caplog):
    caplog.set_level(logging.INFO)
    create = FakeCreate(chat_response("hi", "stop", completion_tokens=40, reasoning_tokens=30))
    make_openrouter(create).chat(request())
    usage = [r for r in caplog.records if "[LLM_USAGE]" in r.getMessage()]
    assert usage and all(r.levelno == logging.INFO for r in usage)
    assert "finish_reason=stop" in usage[0].getMessage()


def test_openai_usage_is_logged_at_info(caplog):
    caplog.set_level(logging.INFO)
    create = FakeCreate(responses_response("hi", "completed", output_tokens=40, reasoning_tokens=30))
    make_openai(create).chat(request())
    usage = [r for r in caplog.records if "[LLM_USAGE]" in r.getMessage()]
    assert usage and all(r.levelno == logging.INFO for r in usage)


def test_api_error_returns_structured_error():
    create = FakeCreate(RuntimeError("401 invalid api key"))
    response = make_openrouter(create).chat(request())
    assert response.calls == []
    assert "401 invalid api key" in response.error
