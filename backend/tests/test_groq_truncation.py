from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.groq_client import GroqGatewayError, call_groq


def make_response(content, finish_reason):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
        usage=SimpleNamespace(
            total_tokens=20,
            completion_tokens=10,
        ),
    )


@pytest.mark.asyncio
async def test_truncated_response_raises_gateway_error():
    response = make_response("export default function App() {", "length")
    mock_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=response)
            )
        )
    )
    mock_bucket = SimpleNamespace(
        reserve=AsyncMock(return_value=object()),
        adjust=AsyncMock(),
    )

    with (
        patch("core.groq_client.get_groq_client", return_value=mock_client),
        patch("core.groq_client._get_bucket", new=AsyncMock(return_value=mock_bucket)),
    ):
        with pytest.raises(GroqGatewayError, match="truncated"):
            await call_groq(
                prompt="Create a React component",
                system="Generate code",
                max_tokens=100,
            )


@pytest.mark.asyncio
async def test_complete_response_is_returned():
    expected = "export default function App() { return null; }"
    response = make_response(expected, "stop")
    mock_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=response)
            )
        )
    )
    mock_bucket = SimpleNamespace(
        reserve=AsyncMock(return_value=object()),
        adjust=AsyncMock(),
    )

    with (
        patch("core.groq_client.get_groq_client", return_value=mock_client),
        patch("core.groq_client._get_bucket", new=AsyncMock(return_value=mock_bucket)),
    ):
        result = await call_groq(
            prompt="Create a React component",
            system="Generate code",
            max_tokens=100,
        )

    assert result == expected


@pytest.mark.asyncio
async def test_truncated_response_is_retried_with_a_larger_limit():
    truncated = make_response('{"project_name": "Happy Paws", "features": [', "length")
    complete = make_response('{"project_name": "Happy Paws"}', "stop")
    create = AsyncMock(side_effect=[truncated, complete])
    mock_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    mock_bucket = SimpleNamespace(
        reserve=AsyncMock(return_value=object()),
        adjust=AsyncMock(),
    )

    with (
        patch("core.groq_client.get_groq_client", return_value=mock_client),
        patch("core.groq_client._get_bucket", new=AsyncMock(return_value=mock_bucket)),
    ):
        result = await call_groq(
            prompt="Plan a website",
            system="Plan",
            model="openai/gpt-oss-120b",
            max_tokens=4096,
        )

    assert result == '{"project_name": "Happy Paws"}'
    first, second = (call.kwargs for call in create.call_args_list)
    assert second["max_tokens"] > first["max_tokens"]
    assert second["extra_body"] == {"reasoning_effort": "low"}
    assert "extra_body" not in first