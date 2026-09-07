"""Shared Claude client for the scientific pipeline.

One place decides the model, the effort level and what counts as an unusable
response, so every scientific caller fails the same way when the API is not
reachable instead of inventing a result.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_EFFORT = "high"
# Thinking tokens count against max_tokens, so a hard chemistry question can spend
# several thousand before it starts writing the answer. Every call streams, so a
# generous ceiling costs nothing when the answer is short.
DEFAULT_MAX_TOKENS = 32000


class LLMUnavailable(RuntimeError):
    """No usable answer from the model: no credentials, transport failure, or a refusal."""


def model_name() -> str:
    return os.getenv("BIOLOGIX_LLM_MODEL", "").strip() or DEFAULT_MODEL


def _effort() -> str:
    return os.getenv("BIOLOGIX_LLM_EFFORT", "").strip() or DEFAULT_EFFORT


def _timeout_s() -> float:
    try:
        return float(os.getenv("BIOLOGIX_LLM_TIMEOUT", "300"))
    except ValueError:
        return 300.0


def _max_retries() -> int:
    try:
        return int(os.getenv("BIOLOGIX_LLM_MAX_RETRIES", "3"))
    except ValueError:
        return 3


def _api_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


def llm_available() -> bool:
    """True when a client can be built. Credentials may still be rejected at call time."""
    if not _api_key() and not os.getenv("ANTHROPIC_AUTH_TOKEN", "").strip():
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _client():
    try:
        import anthropic
    except ImportError as exc:
        raise LLMUnavailable(
            "The anthropic package is not installed in this environment"
        ) from exc
    if not llm_available():
        raise LLMUnavailable(
            "No Anthropic credentials are configured; set ANTHROPIC_API_KEY in the worker environment"
        )
    return anthropic.Anthropic(timeout=_timeout_s(), max_retries=_max_retries())


def complete_json(
    *,
    system: str,
    user: str,
    schema: Dict[str, Any],
    max_tokens: int = DEFAULT_MAX_TOKENS,
    effort: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Ask Claude for one JSON object matching *schema*.

    Returns the parsed object. Raises LLMUnavailable for anything that is not a
    usable answer, so callers never have to distinguish an empty result from a
    failed call.
    """
    import anthropic

    client = _client()
    used_model = model or model_name()
    try:
        # Streamed rather than a single blocking request: a synthesis plan can run to
        # thousands of tokens, and a non-streaming call that long sits on an open socket
        # with no progress until it trips a transport timeout and is retried whole.
        with client.messages.stream(
            model=used_model,
            max_tokens=max_tokens,
            system=system,
            output_config={
                "effort": effort or _effort(),
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": user}],
        ) as stream:
            response = stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise LLMUnavailable(f"Anthropic credentials were rejected: {exc}") from exc
    except anthropic.PermissionDeniedError as exc:
        raise LLMUnavailable(f"Anthropic credentials lack access to {used_model}: {exc}") from exc
    except anthropic.NotFoundError as exc:
        raise LLMUnavailable(f"Model {used_model!r} is not available: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise LLMUnavailable(f"Anthropic rate limit reached: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise LLMUnavailable(f"Anthropic API error {exc.status_code}: {exc}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMUnavailable(f"Could not reach the Anthropic API: {exc}") from exc

    if response.stop_reason == "refusal":
        detail = getattr(response, "stop_details", None)
        category = getattr(detail, "category", None) if detail else None
        raise LLMUnavailable(f"The model declined this request (category: {category})")
    if response.stop_reason == "max_tokens":
        raise LLMUnavailable(
            f"The model hit the {max_tokens} token output limit before finishing its answer"
        )

    text = next((block.text for block in response.content if block.type == "text"), "")
    if not text.strip():
        raise LLMUnavailable("The model returned an empty response")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"The model returned text that is not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMUnavailable("The model returned JSON that is not an object")
    logger.info(
        "llm call model=%s effort=%s in=%d out=%d",
        used_model,
        effort or _effort(),
        response.usage.input_tokens,
        response.usage.output_tokens,
    )
    parsed["_usage"] = {
        "model": used_model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return parsed
