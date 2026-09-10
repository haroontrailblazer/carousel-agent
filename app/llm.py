"""Resolve workspace OpenAI model choices with an explicit saved credential.

Model IDs and API keys never fall back to environment variables.
"""

from __future__ import annotations

from typing import Any

from app.services import ai_config


OPENAI_REASONING_EFFORT = "high"


def resolve_model(model_id: str) -> Any:
    """Resolve a configured model id into what ``LlmAgent(model=...)`` expects.

    Args:
        model_id: The configured OpenAI model identifier, such as
            ``openai/gpt-5.6-sol``.

    Returns:
        A guarded LiteLLM model using the saved workspace credential.
    """
    if "/" in model_id:
        # Imported lazily: pulling in litellm is slow and only needed when a
        # LiteLLM-routed model is actually configured.
        from app.workspace_model import WorkspaceModel

        # gpt-5.6 reasoning models reject function tools on
        # /v1/chat/completions ("Function tools with reasoning_effort are not
        # supported ... use /v1/responses or set reasoning_effort to 'none'").
        # LiteLLM's Responses-API bridge keeps BOTH tools and reasoning
        # working, and structured output + token usage were verified through
        # it on 2026-08-21 (gpt-5.4-mini works fine on plain chat completions
        # and stays there).
        if model_id.startswith("openai/gpt-5.6"):
            model_id = "openai/responses/" + model_id.split("/", 1)[1]
        kwargs: dict[str, Any] = {"timeout": 180, "num_retries": 1}
        if model_id.startswith("openai/"):
            config = ai_config.current()
            if config.key_error:
                raise RuntimeError("The saved OpenAI key cannot be decrypted. Save it again in settings.")
            # Never let LiteLLM obtain a missing key from the environment.
            kwargs["api_key"] = config.api_key
        if model_id.startswith("openai/") and "/gpt-5" in model_id:
            # LiteLlm forwards this to Chat Completions or translates it for
            # the Responses bridge. Keep the quality setting centralized so
            # every GPT-5 agent (including structured-output agents) reasons
            # at the same requested level.
            kwargs["reasoning_effort"] = OPENAI_REASONING_EFFORT
        if not model_id.startswith("openai/"):
            raise ValueError("Choose an OpenAI model in Profile & settings > AI & models.")
        return WorkspaceModel(model=model_id, **kwargs)
    raise ValueError("Text model IDs must start with openai/.")


def resolve_role_model(role: str) -> Any:
    return resolve_model(getattr(ai_config.current(), f"{role}_model"))


__all__ = ["OPENAI_REASONING_EFFORT", "resolve_model", "resolve_role_model"]
