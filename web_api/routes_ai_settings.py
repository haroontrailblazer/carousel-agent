"""Admin-managed workspace models and a write-only OpenAI credential."""

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from app.services import ai_config, ai_models, secret_box
from web_api.auth import Identity
from web_api.deps import current_identity, require_admin

router = APIRouter()


class AISettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr = SecretStr("")
    planner_model: str
    utility_model: str
    phrasing_model: str
    image_model: str


class ModelListRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr = SecretStr("")


def _validate_key(key: str):
    if key and (len(key) > 512 or not re.fullmatch(r"sk-[A-Za-z0-9_-]{10,}", key)):
        raise HTTPException(422, {"message": "Enter a valid OpenAI API key beginning with sk-."})


async def _catalog(key: str = ""):
    if not key:
        try:
            config = await ai_config.load()
            config.require_key()
            key = config.api_key
        except ai_config.AISettingsNotConfigured as exc:
            raise HTTPException(409, {"message": str(exc)}) from None
        except Exception:
            raise HTTPException(503, {"message": "AI settings could not be loaded. Try again."}) from None
    try:
        return await ai_models.discover(key)
    except ai_models.ModelDiscoveryError as exc:
        raise HTTPException(exc.status, {"message": str(exc)}) from None


@router.get("/settings/ai/models")
async def list_saved_key_models(response: Response, _identity: Identity = Depends(current_identity)):
    response.headers["Cache-Control"] = "no-store"
    return await _catalog()


@router.post("/settings/ai/models")
async def preview_key_models(request: Request, response: Response, _identity: Identity = Depends(require_admin)):
    try:
        payload = ModelListRequest.model_validate(await request.json())
    except ValueError:
        raise HTTPException(422, {"message": "Enter a valid OpenAI API key."}) from None
    key = payload.api_key.get_secret_value().strip()
    _validate_key(key)
    response.headers["Cache-Control"] = "no-store"
    return await _catalog(key)


def _status(config, identity, response):
    response.headers["Cache-Control"] = "no-store"
    return ai_config.public_status(config) | {"can_edit": identity.is_admin}


@router.get("/settings/ai")
async def get_ai_settings(response: Response, identity: Identity = Depends(current_identity)):
    try:
        config = await ai_config.load()
    except Exception:
        raise HTTPException(503, {"message": "AI settings could not be loaded. Try again."}) from None
    return _status(config, identity, response)


@router.post("/settings/ai")
async def save_ai_settings(request: Request, response: Response, identity: Identity = Depends(require_admin)):
    # Parse here so validation errors can never echo a submitted credential.
    try:
        payload = AISettingsRequest.model_validate(await request.json())
    except (ValueError, ValidationError):
        raise HTTPException(422, {"message": "Enter an API key and valid model settings."}) from None
    key = payload.api_key.get_secret_value().strip()
    _validate_key(key)
    models = {name: getattr(payload, name).strip() for name in ai_config.MODEL_FIELDS}
    for name, model in models.items():
        pattern = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}" if name == "image_model" else r"openai/[A-Za-z0-9][A-Za-z0-9._:-]{0,255}"
        if not re.fullmatch(pattern, model):
            raise HTTPException(422, {"message": "Use openai/model-name for text models and a bare model name for images."})
    catalog = await _catalog(key)
    if any(model not in catalog["image_models" if name == "image_model" else "text_models"] for name, model in models.items()):
        raise HTTPException(422, {"message": "One or more selected models are unavailable for this key. Refresh the model list and choose again."})
    try:
        config = await ai_config.save(models=models, api_key=key)
    except secret_box.SecretsNotConfigured:
        raise HTTPException(503, {"message": "Credential encryption is not configured on the server."}) from None
    except Exception:
        raise HTTPException(503, {"message": "AI settings could not be saved. Try again."}) from None
    return _status(config, identity, response)
