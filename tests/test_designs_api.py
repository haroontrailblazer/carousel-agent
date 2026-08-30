"""Cross-device carousel design persistence and ownership boundaries."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.schemas import CarouselDesign
from app.runs.service import StartedRun
from web_api.auth import Identity
from web_api.deps import current_identity
from web_api.routes_designs import router
from web_api import routes_runs

IDENTITY = Identity(email="designer@example.com", subject="designer@example.com")


def _app() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[current_identity] = lambda: IDENTITY
    return TestClient(app)


def _design(design_id: str = "editorial-signal") -> dict:
    return CarouselDesign(
        id=design_id,
        name="Exact brand system",
        cover={
            "text_color": "#fefefe",
            "highlight_text_color": "#12ab34",
            "accent_color": "#445566",
        },
    ).model_dump(mode="json")


class CoverMediaContractTests(unittest.TestCase):
    def test_legacy_small_cover_image_box_is_migrated_to_locked_full_bleed(self) -> None:
        design = CarouselDesign(
            cover={
                "image_position": "bottom-center",
                "image_scale": 42,
                "image_transform": {
                    "x": 18,
                    "y": 68,
                    "width": 64,
                    "height": 24,
                    "locked": False,
                },
            },
        )

        self.assertEqual(design.cover.image_position, "middle-center")
        self.assertEqual(design.cover.image_scale, 100)
        self.assertEqual(
            design.cover.image_transform.model_dump(),
            {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0, "locked": True},
        )


class DesignLibraryRouteTests(unittest.TestCase):
    def test_list_returns_only_the_signed_in_users_validated_contracts(self) -> None:
        with patch(
            "web_api.routes_designs.db.list_carousel_designs",
            AsyncMock(return_value=[_design()]),
        ) as listed:
            response = _app().get("/api/designs")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["cover"]["text_color"], "#fefefe")
        self.assertEqual(
            response.json()["items"][0]["cover"]["highlight_text_color"],
            "#12ab34",
        )
        listed.assert_awaited_once_with(IDENTITY.email)

    def test_replace_saves_the_complete_ordered_library_for_that_user(self) -> None:
        items = [_design("one"), _design("two")]
        with patch(
            "web_api.routes_designs.db.replace_carousel_designs",
            AsyncMock(),
        ) as replaced:
            response = _app().put("/api/designs", json={"items": items})

        self.assertEqual(response.status_code, 200)
        saved_owner, saved_items = replaced.await_args.args
        self.assertEqual(saved_owner, IDENTITY.email)
        self.assertEqual([item["id"] for item in saved_items], ["one", "two"])

    def test_duplicate_design_ids_are_rejected_before_the_database(self) -> None:
        response = _app().put(
            "/api/designs",
            json={"items": [_design("same"), _design("same")]},
        )
        self.assertEqual(response.status_code, 422)

    def test_delete_cannot_see_another_users_design(self) -> None:
        with patch(
            "web_api.routes_designs.db.get_carousel_design",
            AsyncMock(return_value=None),
        ):
            response = _app().delete("/api/designs/not-mine")
        self.assertEqual(response.status_code, 404)


class RunDesignSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_saves_then_freezes_the_exact_selected_contract(self) -> None:
        design = CarouselDesign.model_validate(_design("selected"))
        with (
            patch.object(
                routes_runs.db,
                "upsert_carousel_design",
                AsyncMock(),
            ) as saved,
            patch.object(
                routes_runs,
                "start_run",
                AsyncMock(return_value=StartedRun("run-1", "news-1", "A topic")),
            ) as started,
        ):
            response = await routes_runs.create_run(
                routes_runs.StartRunRequest(
                    source="topic",
                    topic="A topic worth posting",
                    design_id="selected",
                    design=design,
                ),
                IDENTITY,
            )

        self.assertEqual(response["run_id"], "run-1")
        saved.assert_awaited_once()
        stored_contract = saved.await_args.args[1]
        frozen_contract = started.await_args.kwargs["design"]
        self.assertEqual(stored_contract, frozen_contract)
        self.assertEqual(
            frozen_contract["cover"]["highlight_text_color"],
            "#12ab34",
        )


if __name__ == "__main__":
    unittest.main()
