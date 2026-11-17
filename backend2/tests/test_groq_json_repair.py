"""Groq JSON mode rescue (the 'CozyGlow Candles' failure in frontend_plan)."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from groq import BadRequestError

from core.groq_client import call_groq, repair_json_text

# Text copied from a real failed_generation: markdown bold markers around
# lines and a stray unquoted `Loading: false,` line.
REJECTED = """{
  "business_name": "CozyGlow Candles",
  **"project_name": "Handmade Candle Online Store",**
  "framework": "React",
  "styling": "Tailwind CSS",
  "design_system": {
    "description": "Warm beige/brown theme with clean components",
    "colors": {"primary": "#D2B48C", "secondary": "#8B4513", "accent": "#F5DEB3"},
    "typography": {"heading": "Playfair Display", "body": "Open Sans"},
    "component_library": "Custom Tailwind UI"
  },
  "pages": [
    {
      "name": "Home",
      "route": "/",
      "purpose": "Show hero banner and featured candles",
      "components": ["HeroBanner", "FeaturedProducts", "Footer"],
      "requires_auth": false
    },
    {
      "name": "ProductDetails",
      "route": "/product/:id",
      "purpose": "Display candle details and add\u2011to\u2011cart",
      "components": ["ProductDetails", "AddToCartButton", "Footer"],
      Loading: false,
      "requires_auth": false
    }
  ],
  "api_integrations": [
    {
      **"name": "GetProductById",**
      "endpoint": "/api/products/{id}",
      "method": "GET",
      "response_fields": ["id", "name", "price"]
    }
  ],
  "tasks": [
    {
      "task_id": "FE5",
      "title": **"Create Shop page",**
      "description": "Build product grid",
      "dependencies": ["FE2", "FE3"]
    },
    {
      "task_id": "FE8",
      "title": "Add Contact page",
      **"description": "Create inquiry form with validation",**
      "dependencies": ["FE3"]
    }
  ]
}"""


def bad_request(failed_generation, code="json_validate_failed"):
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(400, request=request)
    body = {"error": {"message": "Failed to generate JSON.", "type": "invalid_request_error",
                      "code": code, "failed_generation": failed_generation}}
    return BadRequestError("Error code: 400", response=response, body=body)


def ok_response(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(total_tokens=10, prompt_tokens=5, completion_tokens=5),
    )


def patched(create):
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    bucket = SimpleNamespace(reserve=AsyncMock(return_value=object()), adjust=AsyncMock())
    return (
        patch("core.groq_client.get_groq_client", return_value=client),
        patch("core.groq_client._get_bucket", new=AsyncMock(return_value=bucket)),
    )


def test_repairs_bold_markers_and_stray_lines():
    fixed = repair_json_text(REJECTED)
    data = json.loads(fixed)
    assert data["project_name"] == "Handmade Candle Online Store"
    assert data["business_name"] == "CozyGlow Candles"
    assert [p["name"] for p in data["pages"]] == ["Home", "ProductDetails"]
    assert data["pages"][1]["requires_auth"] is False and "Loading" not in data["pages"][1]
    assert data["api_integrations"][0]["name"] == "GetProductById"
    assert [t["title"] for t in data["tasks"]] == ["Create Shop page", "Add Contact page"]
    assert data["tasks"][1]["description"] == "Create inquiry form with validation"


def test_fixes_trailing_commas():
    assert json.loads(repair_json_text('{"a": [1, 2,], "b": {"c": 1,},}')) == {"a": [1, 2], "b": {"c": 1}}


def test_valid_json_is_returned_unchanged():
    assert repair_json_text('{"a": 1}') == '{"a": 1}'


@pytest.mark.parametrize("text", ["", "not json at all", '{"a": ', "[1, 2"])
def test_unrepairable_text_returns_none(text):
    assert repair_json_text(text) is None


@pytest.mark.asyncio
async def test_call_groq_returns_the_repaired_json_instead_of_failing():
    create = AsyncMock(side_effect=bad_request(REJECTED))
    a, b = patched(create)
    with a, b:
        result = await call_groq(prompt="plan", system="s", model="openai/gpt-oss-120b",
                                 max_tokens=4096, response_format={"type": "json_object"})
    assert json.loads(result)["project_name"] == "Handmade Candle Online Store"
    assert create.await_count == 1  # no wasted second request


@pytest.mark.asyncio
async def test_call_groq_retries_when_the_text_cannot_be_repaired():
    create = AsyncMock(side_effect=[bad_request('{"a": '), ok_response('{"ok": true}')])
    a, b = patched(create)
    with a, b:
        result = await call_groq(prompt="plan", system="s", model="openai/gpt-oss-120b",
                                 max_tokens=4096, response_format={"type": "json_object"})
    assert json.loads(result) == {"ok": True}
    assert create.await_count == 2


@pytest.mark.asyncio
async def test_call_groq_gives_up_after_two_retries():
    create = AsyncMock(side_effect=bad_request('{"a": '))
    a, b = patched(create)
    with a, b, pytest.raises(BadRequestError):
        await call_groq(prompt="plan", system="s", model="openai/gpt-oss-120b",
                        max_tokens=4096, response_format={"type": "json_object"})
    assert create.await_count == 3  # first try + 2 retries


@pytest.mark.asyncio
async def test_other_bad_requests_are_not_swallowed():
    create = AsyncMock(side_effect=bad_request("", code="context_length_exceeded"))
    a, b = patched(create)
    with a, b, pytest.raises(BadRequestError):
        await call_groq(prompt="plan", system="s", model="openai/gpt-oss-120b", max_tokens=4096)
    assert create.await_count == 1