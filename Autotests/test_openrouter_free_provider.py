from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "providers"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

try:
    import openai  # noqa: F401
except ModuleNotFoundError:
    openai_stub = types.ModuleType("openai")
    openai_stub.OpenAI = object
    sys.modules["openai"] = openai_stub

import openrouter  # noqa: E402


def test_openrouter_free_has_a_distinct_default_model():
    existing = openrouter.OpenRouterProvider()
    free = openrouter.OpenRouterProvider("OpenRouterFree", "openrouter/free")

    assert free is not existing
    assert free.name == "OpenRouterFree"
    assert free.default_model == "openrouter/free"
    assert existing.default_model == "z-ai/glm-5.2"


def test_openrouter_free_uses_existing_openrouter_proxy_route(monkeypatch):
    free = openrouter.OpenRouterProviderImpl(
        "OpenRouterFree", "OPENROUTER_API_KEY", "openrouter/free",
        "https://openrouter.ai/api/v1",
    )
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(openrouter.llm.openai, "OpenAI", FakeClient)
    monkeypatch.setenv("GATEWAY_URL", "http://localhost:8080/")

    free._create_client()

    assert captured == {
        "api_key": "proxy",
        "base_url": "http://localhost:8080/openrouter/",
    }
