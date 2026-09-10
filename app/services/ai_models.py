"""Discover key-visible models without generating content or persisting a key."""

import re

from openai import AsyncOpenAI, AuthenticationError, PermissionDeniedError, RateLimitError


class ModelDiscoveryError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def group_models(ids: list[str]) -> dict[str, list[str]]:
    # /models exposes IDs, not modality/tool capabilities. Exclude specialized
    # audio, video, embedding and legacy completion families from text choices.
    # The renderer uses GPT Image's edit + generate API, not DALL-E's contract.
    text, images = set(), set()
    for model_id in ids:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", model_id):
            continue
        base = model_id.split(":")[1] if model_id.startswith("ft:") else model_id
        if base.startswith("gpt-image-") or base == "chatgpt-image-latest":
            images.add(model_id)
        elif re.match(r"^(gpt-[3-9]|o[1-9])", base) and not any(
            part in base for part in ("audio", "realtime", "transcribe", "image", "instruct", "search", "codex")
        ):
            text.add("openai/" + model_id)
    return {"text_models": sorted(text), "image_models": sorted(images)}


async def discover(api_key: str) -> dict[str, list[str]]:
    if not api_key:
        raise ModelDiscoveryError("Add an OpenAI API key to load available models.", 409)
    try:
        async with AsyncOpenAI(api_key=api_key, timeout=15.0, max_retries=0) as client:
            page = await client.models.list()
            ids = [model.id async for model in page]
        return group_models(ids)
    except AuthenticationError:
        raise ModelDiscoveryError("OpenAI rejected this API key. Check the key and try again.", 422) from None
    except PermissionDeniedError:
        raise ModelDiscoveryError("This key cannot list models. Enable model-list access in its OpenAI permissions.", 403) from None
    except RateLimitError:
        raise ModelDiscoveryError("OpenAI is rate limiting model discovery. Try again shortly.", 429) from None
    except Exception:
        # Provider errors can contain credential fragments. Never echo/log them.
        raise ModelDiscoveryError("Could not load models from OpenAI. Try again.") from None
