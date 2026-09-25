from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import openai  # noqa: F401
except ModuleNotFoundError:
    openai_stub = types.ModuleType("openai")
    openai_stub.OpenAI = object
    sys.modules["openai"] = openai_stub

import lib_llm_ext  # noqa: E402


def test_openrouter_free_is_a_distinct_openrouter_provider():
    existing = lib_llm_ext._get_provider("OpenRouter")
    free = lib_llm_ext._get_provider("OpenRouterFree")

    assert isinstance(free, lib_llm_ext.OpenRouterProvider)
    assert free is not existing
    assert free._model_name == "openrouter/free"
    assert free._var_name == "OPENROUTER_API_KEY"
    assert free._base_url == existing._base_url == "https://openrouter.ai/api/v1"
    assert existing._model_name == "z-ai/glm-5.2"


def test_openrouter_free_uses_existing_openrouter_proxy_route(monkeypatch):
    free = lib_llm_ext._get_provider("OpenRouterFree")
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(lib_llm_ext.openai, "OpenAI", FakeClient)
    monkeypatch.setenv("GATEWAY_URL", "http://localhost:8080/")

    free._create_client()

    assert captured == {
        "api_key": "proxy",
        "base_url": "http://localhost:8080/openrouter/",
    }
