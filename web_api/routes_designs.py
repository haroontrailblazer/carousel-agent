"""Authenticated CRUD for reusable carousel design contracts."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, model_validator

from app.schemas import CarouselDesign
from app.services import db
from web_api.auth import Identity
from web_api.deps import current_identity

router = APIRouter()


class DesignLibraryRequest(BaseModel):
    items: list[CarouselDesign] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_ids(self) -> "DesignLibraryRequest":
        ids = [design.id for design in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("design ids must be unique")
        return self


@router.get("/designs")
async def list_designs(identity: Identity = Depends(current_identity)) -> dict:
    """Load the signed-in user's cross-device design library."""
    items = await db.list_carousel_designs(identity.email)
    return {
        "items": [
            CarouselDesign.model_validate(item).model_dump(mode="json")
            for item in items
        ]
    }


@router.put("/designs")
async def replace_designs(
    payload: DesignLibraryRequest,
    identity: Identity = Depends(current_identity),
) -> dict:
    """Atomically save the signed-in user's complete ordered library."""
    items = [design.model_dump(mode="json") for design in payload.items]
    await db.replace_carousel_designs(identity.email, items)
    return {"items": items}


@router.delete("/designs/{design_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_design(
    design_id: str,
    identity: Identity = Depends(current_identity),
) -> Response:
    """Delete one owned design without touching another user's row."""
    existing = await db.get_carousel_design(identity.email, design_id)
    if existing is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            {"code": "design_not_found", "message": "That design was not found."},
        )
    remaining = [
        item
        for item in await db.list_carousel_designs(identity.email)
        if item.get("id") != design_id
    ]
    if not remaining:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "last_design", "message": "Keep at least one design."},
        )
    await db.replace_carousel_designs(identity.email, remaining)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
