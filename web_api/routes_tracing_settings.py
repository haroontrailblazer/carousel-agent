"""Write-only Langfuse credentials, following workspace admin permissions."""
from dataclasses import replace
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, SecretStr

from app.services import tracing_config, secret_box
from web_api.auth import Identity
from web_api.deps import current_identity, require_admin

router = APIRouter()


class TracingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    base_url: str
    public_key: SecretStr = SecretStr("")
    secret_key: SecretStr = SecretStr("")


def status(config, identity, response):
    response.headers["Cache-Control"] = "no-store"
    return tracing_config.public_status(config) | {"can_edit": identity.is_admin}


@router.get("/settings/tracing")
async def get_tracing(response: Response, identity: Identity = Depends(current_identity)):
    try:
        config = await tracing_config.load()
    except Exception:
        raise HTTPException(503, {"message": "Tracing settings could not be loaded. Try again."}) from None
    return status(config, identity, response)


@router.post("/settings/tracing")
async def save_tracing(request: Request, response: Response, identity: Identity = Depends(require_admin)):
    # Avoid FastAPI/Pydantic validation responses echoing submitted credentials.
    try:
        payload = TracingRequest.model_validate(await request.json())
        base_url = tracing_config.validate_base_url(payload.base_url)
        public = payload.public_key.get_secret_value().strip()
        secret = payload.secret_key.get_secret_value().strip()
        for key, prefix in ((public, "pk-lf-"), (secret, "sk-lf-")):
            if key and not re.fullmatch(prefix + r"[A-Za-z0-9_-]{8,200}", key):
                raise ValueError
    except ValueError:
        raise HTTPException(422, {"message": "Enter a valid HTTPS server URL and Langfuse project keys (pk-lf- and sk-lf-)."}) from None
    try:
        previous = await tracing_config.load()
    except Exception:
        raise HTTPException(503, {"message": "Tracing settings could not be loaded. Try again."}) from None
    # A new project or host must not receive a secret retained from another one.
    changed_project = base_url != previous.base_url or bool(public and public != previous.public_key)
    if changed_project and not (public and secret):
        raise HTTPException(422, {"message": "Enter both project keys when changing the Langfuse server or project."})
    config = replace(previous, enabled=payload.enabled, base_url=base_url,
                     public_key=public or previous.public_key, secret_key=secret or previous.secret_key,
                     key_error=False if public and secret else previous.key_error)
    if config.enabled and not config.configured:
        raise HTTPException(422, {"message": "Add both Langfuse project keys before enabling tracing."})
    if config.enabled or public or secret:
        try:
            await tracing_config.verify(config)
        except ValueError as exc:
            raise HTTPException(422, {"message": str(exc)}) from None
    try:
        saved = await tracing_config.save(config, replace_credentials=bool(public or secret))
    except secret_box.SecretsNotConfigured:
        raise HTTPException(503, {"message": "Credential encryption is not configured on the server."}) from None
    except Exception:
        raise HTTPException(503, {"message": "Tracing settings could not be saved. Try again."}) from None
    return status(saved, identity, response)


@router.delete("/settings/tracing")
async def disconnect_tracing(response: Response, identity: Identity = Depends(require_admin)):
    try:
        config = await tracing_config.clear()
    except Exception:
        raise HTTPException(503, {"message": "Langfuse could not be disconnected. Try again."}) from None
    return status(config, identity, response)
