"""Review downloads preserve the selected cover and every finished slide."""

import copy
from io import BytesIO
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zipfile import ZipFile

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from web_api import routes_runs as routes
from web_api.auth import Identity
from web_api.deps import current_identity


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.state = {"phase": "review", "bundle": {
            "cover": {"video_artifact": "cover.mp4", "poster_artifact": "cover.png"},
            "slides": [{"index": 1, "artifact": "body-1.png"},
                       {"index": 2, "artifact": "body-2.png"}],
            "cta": {"artifact": "cta.png"},
            "ordered_artifacts": ["cover.mp4", "body-1.png", "body-2.png", "cta.png"],
        }}
        self.original = copy.deepcopy(self.state)
        self.files = {name: f"original bytes of {name}".encode() for name in
                      ("cover.mp4", "cover.png", "body-1.png", "body-2.png", "cta.png")}

        async def load(**kwargs):
            data = self.files.get(kwargs["filename"])
            return SimpleNamespace(inline_data=SimpleNamespace(data=data)) if data else None

        self.service = SimpleNamespace(
            latest_versions_async=AsyncMock(return_value={name: 3 for name in self.files}),
            load_artifact=AsyncMock(side_effect=load),
        )
        self.app = FastAPI()
        self.app.include_router(routes.router, prefix="/api")
        self.app.dependency_overrides[current_identity] = lambda: Identity(email="user@example.com", subject="user")
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        for p in (
            patch.object(routes, "_session_state", AsyncMock(return_value=self.state)),
            patch.object(routes.runtime, "artifact_service", return_value=self.service),
        ):
            p.start()
            self.addCleanup(p.stop)

    def download(self, choice):
        return self.client.get(f"/api/runs/run-test/download?cover={choice}")

    def test_selected_image_plus_all_slides_and_cta_in_order(self):
        response = self.download("image")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertIn('attachment; filename="carousel-run-test.zip"', response.headers["content-disposition"])
        self.assertEqual(int(response.headers["content-length"]), len(response.content))
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertEqual(archive.namelist(), ["01-cover.png", "02-slide-01.png", "03-slide-02.png", "04-cta.png"])
            for member, filename in zip(archive.namelist(), ["cover.png", "body-1.png", "body-2.png", "cta.png"]):
                self.assertEqual(archive.read(member), self.files[filename])
        self.assertNotIn("cover.mp4", [c.kwargs["filename"] for c in self.service.load_artifact.call_args_list])

    def test_selected_video_excludes_the_image_cover(self):
        response = self.download("video")
        self.assertEqual(response.status_code, 200)
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertEqual(archive.namelist()[0], "01-cover.mp4")
            self.assertEqual(archive.read("01-cover.mp4"), self.files["cover.mp4"])
            self.assertEqual(len(archive.namelist()), 4)
        self.assertEqual(self.state, self.original)  # no approval or publish mutation
        for call in self.service.load_artifact.call_args_list:
            self.assertEqual(call.kwargs["session_id"], "run-test")
            self.assertEqual(call.kwargs["version"], 3)

    def test_explicit_cover_choice_is_required(self):
        self.assertEqual(self.client.get("/api/runs/run-test/download").status_code, 422)
        self.assertEqual(self.download("other").status_code, 422)
        self.service.load_artifact.assert_not_awaited()

    def test_unavailable_cover_never_falls_back_to_other_cover(self):
        self.state["bundle"]["cover"]["video_artifact"] = ""
        response = self.download("video")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "cover_unavailable")
        self.service.load_artifact.assert_not_awaited()

    def test_incomplete_bundle_cannot_download(self):
        self.state["bundle"]["cta"]["artifact"] = ""
        self.assertEqual(self.download("image").status_code, 409)

    def test_missing_storage_file_returns_error_instead_of_partial_zip(self):
        del self.files["body-2.png"]
        response = self.download("image")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "missing_artifact")
        self.assertNotIn("attachment", response.headers.get("content-disposition", ""))

    def test_rework_cannot_export_the_previous_bundle(self):
        self.state["phase"] = "rework"
        self.assertEqual(self.download("image").status_code, 409)
        self.service.load_artifact.assert_not_awaited()

    def test_unassembled_run_cannot_download(self):
        self.state.clear()
        self.assertEqual(self.download("image").status_code, 404)

    def test_authentication_is_required(self):
        def unauthenticated():
            raise HTTPException(401, "Sign in")
        self.app.dependency_overrides[current_identity] = unauthenticated
        self.assertEqual(self.download("image").status_code, 401)
        self.service.load_artifact.assert_not_awaited()
