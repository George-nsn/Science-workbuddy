from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from science_buddy.config import Settings
from science_buddy.domain.providers import StructuredGenerationRequest, TextGenerationRequest
from science_buddy.services.models import (
    AnthropicProvider,
    GitHubCopilotProvider,
    ModelConfigurationError,
    ModelResponseError,
    OpenAICompatibleProvider,
    _parse_json_content,
    build_model_provider,
    drain_model_usage,
    validate_model_base_url,
)

REQUEST = StructuredGenerationRequest(
    system_instruction="Return evidence JSON.",
    user_content="Question",
    response_schema={"type": "object"},
)

CACHED_REQUEST = StructuredGenerationRequest(
    system_instruction="Stable rules.",
    context_instruction="Long-term memory block.",
    user_content="Dynamic turn data.",
    response_schema={"type": "object", "properties": {"claims": {"type": "array"}}},
)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            "```yaml\nclaims:\n  - statement: supported\n"
            "    evidence_ids: [ev1.x]\n```",
            {
                "claims": [
                    {"statement": "supported", "evidence_ids": ["ev1.x"]}
                ]
            },
        ),
        ("{'claims': [], 'gaps': ['missing']}", {"claims": [], "gaps": ["missing"]}),
        ('claims = []\nanswer = "ok"', {"claims": [], "answer": "ok"}),
        (
            "<response><answer>ok</answer><claims></claims></response>",
            {"answer": "ok", "claims": ""},
        ),
    ],
)
def test_structured_parser_accepts_supported_non_json_formats(
    content: str,
    expected: dict[str, object],
) -> None:
    assert _parse_json_content(content) == expected


def test_structured_parser_preserves_top_level_array_without_extracting_first_item() -> None:
    assert _parse_json_content('[{"direction_id":"single-direction"}]') == {
        "__root_array__": [{"direction_id": "single-direction"}]
    }


def test_model_base_url_only_allows_plain_http_on_loopback() -> None:
    assert (
        validate_model_base_url("openai_compatible", "http://127.0.0.1:11434/v1")
        == "http://127.0.0.1:11434/v1"
    )
    with pytest.raises(ModelConfigurationError, match="only allowed on localhost"):
        validate_model_base_url("openai_compatible", "http://models.example.test/v1")


@pytest.mark.asyncio
async def test_openai_provider_extracts_complete_json_from_wrapped_text() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "说明文字\n{\"claims\": []}\n完成"
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            client,
            api_key="secret",
            model="test-model",
            base_url="https://example.test/v1",
        )

        result = await provider.generate_structured(REQUEST)

    assert result == {"claims": []}


@pytest.mark.asyncio
async def test_openai_provider_reports_truncated_json_metadata() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": '{"claims": ['},
                        "finish_reason": "length",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            client,
            api_key="secret",
            model="test-model",
            base_url="https://example.test/v1",
        )

        with pytest.raises(
            ModelResponseError,
            match=r"characters=12, finish_reason=length",
        ):
            await provider.generate_structured(REQUEST)


@pytest.mark.asyncio
async def test_openai_compatible_provider_parses_structured_json() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"claims": []}'}}],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 20,
                    "total_tokens": 140,
                    "prompt_tokens_details": {"cached_tokens": 40},
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            client,
            api_key="secret",
            model="test-model",
            base_url="https://example.test/v1",
        )
        result = await provider.generate_structured(REQUEST)

    assert result == {"claims": []}
    usage = drain_model_usage(provider)
    assert usage[0].total_tokens == 140
    assert usage[0].cached_tokens == 40
    assert usage[0].token_count_estimated is False
    assert usage[0].cost_usd is None


@pytest.mark.asyncio
async def test_openai_prompt_orders_static_memory_then_dynamic_content() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        messages = body["messages"]
        assert [message["role"] for message in messages] == ["system", "system", "user"]
        assert "Stable rules." in messages[0]["content"]
        assert "Required JSON Schema" in messages[0]["content"]
        assert "Configured processing depth" in messages[1]["content"]
        assert "Long-term memory block." in messages[1]["content"]
        assert messages[2]["content"] == "Dynamic turn data."
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"claims": []}'}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "total_tokens": 110,
                    "prompt_cache_hit_tokens": 75,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            client,
            api_key="secret",
            model="test-model",
            base_url="https://example.test/v1",
        )
        await provider.generate_structured(CACHED_REQUEST)

    assert drain_model_usage(provider)[0].cached_tokens == 75


@pytest.mark.asyncio
async def test_openai_reasoning_and_context_budget_are_capability_gated() -> None:
    observed: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        observed.update(body)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"claims": []}'}}]},
        )

    request = StructuredGenerationRequest(
        system_instruction="Stable rules.",
        context_instruction="M" * 40000,
        user_content="D" * 120000,
        response_schema={"type": "object"},
        depth="deep",
        max_context_tokens=32768,
        max_output_tokens=4096,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            client,
            api_key="secret",
            model="o4-mini",
            base_url="https://example.test/v1",
            provider_name="openai",
        )
        await provider.generate_structured(request)

    assert observed["reasoning_effort"] == "high"
    assert observed["max_completion_tokens"] == 4096
    assert "max_tokens" not in observed
    prompt = "".join(message["content"] for message in observed["messages"])
    assert "content_omitted_due_to_context_budget" in prompt


@pytest.mark.asyncio
async def test_anthropic_provider_parses_fenced_json() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "secret"
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "```json\n{\"claims\": []}\n```"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AnthropicProvider(
            client,
            api_key="secret",
            model="test-model",
            base_url="https://example.test/v1",
        )
        result = await provider.generate_structured(REQUEST)

    assert result == {"claims": []}


@pytest.mark.asyncio
async def test_anthropic_marks_static_and_memory_blocks_ephemeral() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        assert len(body["system"]) == 2
        assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert body["system"][1]["type"] == "text"
        assert "Configured processing depth" in body["system"][1]["text"]
        assert "Long-term memory block." in body["system"][1]["text"]
        assert body["system"][1]["cache_control"] == {"type": "ephemeral"}
        assert body["messages"][0]["content"] == "Dynamic turn data."
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": '{"claims": []}'}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AnthropicProvider(
            client,
            api_key="secret",
            model="test-model",
            base_url="https://example.test/v1",
        )
        result = await provider.generate_structured(CACHED_REQUEST)

    assert result == {"claims": []}


@pytest.mark.asyncio
async def test_deepseek_preset_uses_correct_endpoint_and_reports_upstream_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret"
        assert request.read()
        assert b'"model":"deepseek-v4-pro"' in request.content
        return httpx.Response(
            400,
            json={"error": {"message": "Model Not Exist"}},
        )

    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_model="deepseek-v4-pro",
        llm_api_key=SecretStr("test-secret"),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = build_model_provider(client, settings)
        with pytest.raises(
            ModelResponseError,
            match=r"DeepSeek.*HTTP 400.*Model Not Exist.*精确模型 ID",
        ) as captured:
            await provider.generate_structured(REQUEST)

    assert "test-secret" not in str(captured.value)


@pytest.mark.asyncio
async def test_deepseek_uses_official_max_thinking_and_one_million_context() -> None:
    observed: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.update(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "reasoning_content": "private reasoning",
                            "content": '{"claims": []}',
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_model="deepseek-v4-flash",
        llm_api_key=SecretStr("test-secret"),
    )
    request = StructuredGenerationRequest(
        system_instruction="Return JSON.",
        user_content="Question",
        response_schema={"type": "object"},
        depth="max",
        max_context_tokens=1000000,
        max_output_tokens=32768,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await build_model_provider(client, settings).generate_structured(request)

    assert result == {"claims": []}
    assert observed["model"] == "deepseek-v4-flash"
    assert observed["thinking"] == {"type": "enabled"}
    assert observed["reasoning_effort"] == "max"
    assert observed["max_tokens"] == 32768
    assert observed["response_format"] == {"type": "json_object"}
    assert "temperature" not in observed


@pytest.mark.asyncio
async def test_deepseek_text_generation_is_not_wrapped_in_json_schema() -> None:
    observed: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.update(__import__("json").loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "# 自由草稿\n完整实验推演。"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_model="deepseek-v4-flash",
        llm_api_key=SecretStr("test-secret"),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = build_model_provider(client, settings)
        result = await provider.generate_text(
            TextGenerationRequest(
                system_instruction="自由设计完整方案。",
                user_content="研究问题",
                depth="max",
                max_context_tokens=1000000,
                max_output_tokens=49152,
            )
        )

    assert result.startswith("# 自由草稿")
    assert observed["thinking"] == {"type": "enabled"}
    assert observed["reasoning_effort"] == "max"
    assert observed["max_tokens"] == 49152
    assert "response_format" not in observed
    assert "temperature" not in observed
    messages = observed["messages"]
    assert isinstance(messages, list)
    assert "Required JSON Schema" not in "".join(str(item) for item in messages)


@pytest.mark.asyncio
async def test_ollama_preset_does_not_require_or_send_api_key() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://127.0.0.1:11434/v1/chat/completions"
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"claims": []}'}}]},
        )

    settings = Settings(
        _env_file=None,
        llm_provider="ollama",
        llm_model="qwen3:8b",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = build_model_provider(client, settings)
        result = await provider.generate_structured(REQUEST)

    assert provider.name == "ollama"
    assert result == {"claims": []}


@pytest.mark.asyncio
async def test_github_copilot_sdk_runs_without_tools_and_parses_json(
    tmp_path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    observed: dict[str, object] = {}

    class FakeSession:
        async def send_and_wait(self, prompt: str):  # type: ignore[no-untyped-def]
            observed["prompt"] = prompt
            return SimpleNamespace(
                data=SimpleNamespace(content='```json\n{"claims": []}\n```')
            )

        async def disconnect(self) -> None:
            observed["disconnected"] = True

    class FakeClient:
        def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
            observed["client"] = kwargs

        async def start(self) -> None:
            observed["started"] = True

        async def get_auth_status(self):  # type: ignore[no-untyped-def]
            return SimpleNamespace(isAuthenticated=True)

        async def create_session(self, **kwargs):  # type: ignore[no-untyped-def]
            observed["session"] = kwargs
            return FakeSession()

        async def stop(self) -> None:
            observed["stopped"] = True

        async def force_stop(self) -> None:
            observed["force_stopped"] = True

    monkeypatch.setattr(
        GitHubCopilotProvider,
        "_client_class",
        staticmethod(lambda: FakeClient),
    )
    provider = GitHubCopilotProvider(
        model="gpt-5.4",
        github_token=None,
        base_directory=str(tmp_path / "copilot"),
    )
    result = await provider.generate_structured(REQUEST)

    assert result == {"claims": []}
    assert observed["client"] == {
        "github_token": None,
        "use_logged_in_user": True,
        "mode": "empty",
        "base_directory": str(tmp_path / "copilot"),
        "log_level": "error",
    }
    session_options = observed["session"]
    assert isinstance(session_options, dict)
    assert session_options["model"] == "gpt-5.4"
    assert session_options["available_tools"] == []
    assert session_options["enable_session_telemetry"] is False
    assert session_options["enable_file_hooks"] is False
    assert session_options["enable_host_git_operations"] is False
    assert session_options["enable_session_store"] is False
    assert observed["disconnected"] is True
    assert observed["stopped"] is True
