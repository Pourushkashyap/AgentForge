import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from groq import (
    AsyncGroq,
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

load_dotenv()

logger = logging.getLogger("agentforge.groq")

DEFAULT_TPM_LIMIT = int(os.getenv("GROQ_TPM_LIMIT", "12000"))
WINDOW_SECONDS = 60.0

# Temporary error retry delays
RETRY_DELAYS = (10, 15, 20, 20, 20)
MAX_RETRIES = 5
MAX_RETRY_WAIT_SECONDS = 20.0



# --------------------------------------------------------------------------
# JSON mode rescue
# --------------------------------------------------------------------------
# In JSON mode Groq rejects a reply that is not valid JSON with a 400
# ("json_validate_failed") and hands the rejected text back in
# error["failed_generation"]. The model usually got 99% right: markdown bold
# markers (**"key": "value",**) or a stray unquoted line (Loading: false,).
# That text can be repaired locally, which is faster and cheaper than failing
# the whole build.
_STRAY_LINE = re.compile(r"^\s*[A-Za-z_][\w ]*\s*:\s*\S")


def repair_json_text(text: str) -> str | None:
    """Return valid JSON text repaired from ``text``, or None if impossible."""
    candidate = (text or "").strip().replace("**", "")
    for _ in range(40):
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError as err:
            lines = candidate.splitlines()
            index = err.lineno - 1
            bad = lines[index] if 0 <= index < len(lines) else ""
            if _STRAY_LINE.match(bad):  # unquoted key such as: Loading: false,
                del lines[index]
                candidate = "\n".join(lines)
                continue
            without_trailing_commas = re.sub(r",(\s*[}\]])", r"\1", candidate)
            if without_trailing_commas != candidate:
                candidate = without_trailing_commas
                continue
            return None
    return None


def _failed_generation(exc: BadRequestError) -> str | None:
    """The text Groq rejected, if this 400 is a json_validate_failed error."""
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, dict) else {}
    if isinstance(error, dict) and error.get("code") == "json_validate_failed":
        failed = error.get("failed_generation")
        return failed if isinstance(failed, str) else ""
    return None


class GroqGatewayError(RuntimeError):
    """Raised when the Groq gateway cannot complete a request."""


@dataclass
class Reservation:
    timestamp: float
    tokens: int


class TokenBucket:
    """Process-local rolling 60-second token budget."""

    def __init__(self, limit: int):
        if limit <= 0:
            raise ValueError("Token limit must be greater than zero")

        self.limit = limit
        self._entries: deque[Reservation] = deque()
        self._lock = asyncio.Lock()

    def _remove_expired(self, now: float) -> None:
        while (
            self._entries
            and now - self._entries[0].timestamp >= WINDOW_SECONDS
        ):
            self._entries.popleft()

    async def reserve(self, tokens: int) -> Reservation:
        if tokens <= 0:
            raise ValueError("Reserved tokens must be greater than zero")

        if tokens > self.limit:
            raise GroqGatewayError(
                f"Estimated request size ({tokens}) exceeds the "
                f"configured TPM budget ({self.limit}). "
                "Reduce the prompt or max_tokens."
            )

        while True:
            async with self._lock:
                now = time.monotonic()
                self._remove_expired(now)

                used = sum(entry.tokens for entry in self._entries)

                if used + tokens <= self.limit:
                    ticket = Reservation(now, tokens)
                    self._entries.append(ticket)

                    logger.info(
                        "Token budget reserved: %s/%s",
                        used + tokens,
                        self.limit,
                    )
                    return ticket

                oldest_timestamp = self._entries[0].timestamp
                wait_seconds = max(
                    0.1,
                    WINDOW_SECONDS - (now - oldest_timestamp),
                )

            logger.info(
                "Token budget full. Waiting %.1f seconds.",
                wait_seconds,
            )
            await asyncio.sleep(wait_seconds)

    async def adjust(
        self,
        ticket: Reservation,
        actual_tokens: int,
    ) -> None:
        async with self._lock:
            if ticket in self._entries:
                ticket.tokens = max(0, actual_tokens)
    
    
    async def release(self, ticket: Reservation) -> None:
        """Release a reservation when a request fails or is cancelled."""
        async with self._lock:
            if ticket in self._entries:
                self._entries.remove(ticket)

    

_buckets: dict[str, TokenBucket] = {}
_buckets_lock = asyncio.Lock()


async def _get_bucket(model: str) -> TokenBucket:
    async with _buckets_lock:
        if model not in _buckets:
            _buckets[model] = TokenBucket(DEFAULT_TPM_LIMIT)

        return _buckets[model]


_client: AsyncGroq | None = None


def get_groq_client() -> AsyncGroq:
    global _client

    if _client is None:
        api_key = os.getenv("GROQ_API_KEY")

        if not api_key:
            raise GroqGatewayError(
                "GROQ_API_KEY is missing. Add it to your .env file."
            )

        _client = AsyncGroq(
            api_key=api_key,
            max_retries=0,
            timeout=120.0,
        )

    return _client


def estimate_tokens(text: str) -> int:
    """Rough estimate, not an exact tokenizer."""
    return max(1, (len(text) + 2) // 3)


def _get_retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}

    retry_after = headers.get("retry-after")

    if retry_after is not None:
        try:
            return max(0.0, float(retry_after))
        except (TypeError, ValueError):
            pass

    message = str(exc)

    # Examples: "try again in 12s" or "try again in 1.5 seconds"
    match = re.search(
        r"try again in\s+(\d+(?:\.\d+)?)\s*"
        r"(?:s|sec|seconds)\b",
        message,
        re.IGNORECASE,
    )

    if match:
        return float(match.group(1))

    # Examples: "try again in 2m" or "try again in 1.5 minutes"
    match = re.search(
        r"try again in\s+(\d+(?:\.\d+)?)\s*"
        r"(?:m|min|minutes)\b",
        message,
        re.IGNORECASE,
    )

    if match:
        return float(match.group(1)) * 60.0

    return None


def _rate_limit_kind(exc: Exception) -> str:
    """Distinguish daily limits from temporary rate limits."""

    message = str(exc).lower()

    # Daily usage limits will not normally recover after a short wait.
    daily_markers = (
        "tokens per day",
        "token per day",
        "tokens_per_day",
        "daily token limit",
        "requests per day",
        "requests_per_day",
        "tpd",
    )

    if any(marker in message for marker in daily_markers):
        return "daily"

    # Permanent account/billing errors.
    permanent_markers = (
        "insufficient_quota",
        "billing_hard_limit_reached",
        "credit balance is too low",
        "billing limit reached",
        "account quota exhausted",
    )

    if any(marker in message for marker in permanent_markers):
        return "permanent"

    # Temporary TPM/RPM limits.
    temporary_markers = (
        "tokens per minute",
        "requests per minute",
        "rate limit reached",
        "try again in",
        "rate_limit_exceeded",
    )

    if any(marker in message for marker in temporary_markers):
        return "temporary"

    return "unknown"


async def call_groq(
    prompt: str,
    system: str = "You are a helpful AI assistant.",
    model: str | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.2,
    response_format: dict[str, Any] | None = None,
) -> str:
    """Send an async request through the shared Groq gateway."""

    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty")

    if max_tokens <= 0:
        raise ValueError("max_tokens must be greater than zero")

    model = model or os.getenv(
        "GROQ_MODEL",
        "openai/gpt-oss-120b",
    )

    client = get_groq_client()
    bucket = await _get_bucket(model)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]

    estimated_input = (
        estimate_tokens(system) + estimate_tokens(prompt)
    )
    estimated_total = estimated_input + max_tokens

    attempt = 0
    length_retries = 0
    json_retries = 0
    reasoning_low = False

    while True:
        ticket = await bucket.reserve(estimated_total)

        try:
            request_args: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }

            if response_format is not None:
                request_args["response_format"] = response_format

            # gpt-oss models spend part of max_tokens on hidden reasoning.
            # After a truncation, ask for less reasoning on the retry.
            if reasoning_low and "gpt-oss" in model:
                request_args["extra_body"] = {"reasoning_effort": "low"}

            response = await client.chat.completions.create(
                **request_args
            )

            if not response.choices:
                await bucket.adjust(ticket, 0)
                raise GroqGatewayError(
                    "Groq returned no response choices."
                )

            choice = response.choices[0]
            content = choice.message.content or ""
            finish_reason = choice.finish_reason

            usage = getattr(response, "usage", None)
            actual_total = (
                getattr(usage, "total_tokens", None)
                if usage is not None
                else None
            )

            await bucket.adjust(
                ticket,
                actual_total
                if actual_total is not None
                else estimated_total,
            )

            logger.info(
                "Groq response: model=%s, finish_reason=%s, "
                "completion_tokens=%s, total_tokens=%s",
                model,
                finish_reason,
                getattr(usage, "completion_tokens", None)
                if usage is not None
                else None,
                actual_total,
            )

            if not content.strip():
                raise GroqGatewayError(
                    f"Groq returned empty content. "
                    f"finish_reason={finish_reason!r}"
                )

            
          
            if finish_reason == "length":
                # Leave room in the per-minute token budget for the prompt.
                headroom = max(DEFAULT_TPM_LIMIT - estimated_input - 300, 0)
                escalated = min(int(max_tokens * 1.75), headroom)

                if length_retries < 2 and escalated > max_tokens:
                    length_retries += 1
                    logger.warning(
                        "Groq output reached its token limit: model=%s, "
                        "max_tokens=%s. Retrying with max_tokens=%s.",
                        model,
                        max_tokens,
                        escalated,
                    )
                    print(
                        f"Groq output reached its token limit ({max_tokens}); "
                        f"retrying with {escalated} and less reasoning."
                    )
                    max_tokens = escalated
                    estimated_total = estimated_input + max_tokens
                    reasoning_low = True
                    continue

                logger.warning(
                    "Groq output reached its token limit: "
                    "model=%s, max_tokens=%s",
                    model,
                    max_tokens,
                )
                raise GroqGatewayError(
                    f"Groq output was truncated because it reached "
                    f"the token limit. model={model}, "
                    f"max_tokens={max_tokens}. "
                    "Reduce the requested output size or split the task "
                    "into smaller parts."
                )

            return content
   
       
            


        except RateLimitError as exc:
            await bucket.adjust(ticket, 0)

            limit_kind = _rate_limit_kind(exc)

            if limit_kind == "daily":
                raise GroqGatewayError(
                    "Groq daily token limit (TPD) has been reached. "
                    "Short retries will not fix this. Wait for the daily "
                    "limit to reset or check your Groq plan and usage. "
                    f"Original error: {exc}"
                ) from exc

            if limit_kind == "permanent":
                raise GroqGatewayError(
                    "Groq billing/account quota is exhausted. "
                    "Check your Groq billing and usage limits. "
                    f"Original error: {exc}"
                ) from exc

            if attempt >= MAX_RETRIES:
                raise GroqGatewayError(
                    f"Groq rate limit persisted after "
                    f"{MAX_RETRIES} retries. Last error: {exc}"
                ) from exc

            retry_after = _get_retry_after_seconds(exc)

            fallback = RETRY_DELAYS[
                min(attempt, len(RETRY_DELAYS) - 1)
            ]

            # Never wait more than 20 seconds for a temporary 429.
            wait_seconds = min(
                max(float(fallback), retry_after or 0.0),
                MAX_RETRY_WAIT_SECONDS,
            )

            attempt += 1

            logger.warning(
                "Groq temporary rate limit. Retry %s/%s in %.1f seconds.",
                attempt,
                MAX_RETRIES,
                wait_seconds,
            )

            await asyncio.sleep(wait_seconds)

        except (
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
        ) as exc:
            await bucket.adjust(ticket, 0)

            if attempt >= MAX_RETRIES:
                raise GroqGatewayError(
                    f"Groq/network error persisted after "
                    f"{MAX_RETRIES} retries: {exc}"
                ) from exc

            wait_seconds = min(
                float(
                    RETRY_DELAYS[
                        min(attempt, len(RETRY_DELAYS) - 1)
                    ]
                ),
                MAX_RETRY_WAIT_SECONDS,
            )

            attempt += 1

            logger.warning(
                "Temporary Groq network/server error. "
                "Retry %s/%s in %.1f seconds: %s",
                attempt,
                MAX_RETRIES,
                wait_seconds,
                exc,
            )

            await asyncio.sleep(wait_seconds)

        except BadRequestError as exc:
            await bucket.adjust(ticket, 0)
            failed = _failed_generation(exc)
            if failed is None:
                raise  # a real request error: nothing to rescue
            repaired = repair_json_text(failed)
            if repaired is not None:
                logger.warning(
                    "Groq rejected the JSON output of %s; repaired it locally.",
                    model,
                )
                return repaired
            if json_retries < 2:
                json_retries += 1
                logger.warning(
                    "Groq returned invalid JSON from %s and it could not be "
                    "repaired. Retrying (%s/2).",
                    model,
                    json_retries,
                )
                continue
            raise

        except Exception:
            raise


async def close_groq_client() -> None:
    """Close the shared client during application shutdown."""

    global _client

    if _client is not None:
        await _client.close()
        _client = None