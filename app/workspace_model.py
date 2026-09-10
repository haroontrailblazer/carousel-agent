"""Block LiteLLM's automatic environment-key fallback, including ADK dev calls."""

from google.adk.models.lite_llm import LiteLlm

from app.services.ai_config import AISettingsNotConfigured


class WorkspaceModel(LiteLlm):
    async def generate_content_async(self, llm_request, stream=False):
        if not self._additional_args.get("api_key"):
            raise AISettingsNotConfigured(
                "Save your OpenAI API key in Profile & settings > AI & models, then start a new run."
            )
        async for response in super().generate_content_async(llm_request, stream=stream):
            yield response
