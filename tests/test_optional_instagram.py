"""Optional Instagram: generation, complete delivery, and the publishing gate."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from app import orchestrator as orch
from app.runs import service
from app.services import db, telegram_delivery
from app.state import K_ACCOUNT_ID, K_PHASE, K_PUBLISH_RESULT, K_QA_REPORT, K_VERDICT
from app.tools import brand_identity, brand_layout, telegram_tools as tg


class StartTests(unittest.IsolatedAsyncioTestCase):
    async def start(self, account_id=None, resolved=None):
        session = AsyncMock()
        self.resolve = Mock(return_value=resolved)
        self.binding = AsyncMock()
        with patch.object(service.instagram_accounts, "resolve", self.resolve), \
             patch.object(service, "_check_limits", AsyncMock()), \
             patch("app.agent.build_configured_runner", return_value=SimpleNamespace(
                 session_service=SimpleNamespace(create_session=session))), \
             patch.object(service, "spawn_run"), \
             patch.object(db, "create_run", AsyncMock()), \
             patch.object(db, "set_run_meta", AsyncMock()), \
             patch.object(db, "set_run_status", AsyncMock()), \
             patch.object(db, "set_run_account", self.binding):
            await service.start_run(source="topic", topic="A test topic", account_id=account_id)
        return session.call_args.kwargs["state"]

    async def test_no_connected_account_can_start(self):
        state = await self.start()
        self.assertEqual(state[K_ACCOUNT_ID], "")
        self.binding.assert_not_awaited()

    async def test_telegram_choice_never_uses_the_default(self):
        state = await self.start(account_id="", resolved=SimpleNamespace(id="other"))
        self.assertEqual(state[K_ACCOUNT_ID], "")
        self.resolve.assert_not_called()

    async def test_connected_default_is_bound_to_run(self):
        state = await self.start(resolved=SimpleNamespace(id="account-a", needs_reconnect=False))
        self.assertEqual(state[K_ACCOUNT_ID], "account-a")

    async def test_missing_explicit_account_does_not_fall_back(self):
        with self.assertRaises(service.RunRefused):
            await self.start(account_id="deleted")

    async def test_expired_explicit_account_does_not_start(self):
        with self.assertRaises(service.RunRefused):
            await self.start(account_id="expired", resolved=SimpleNamespace(
                id="expired", handle="@expired", needs_reconnect=True))


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def run_phase(self, account_id, *, phase="qa", passed=True, error=None, verdict=None):
        self.state = {K_ACCOUNT_ID: account_id, K_PHASE: phase, K_VERDICT: verdict,
                      K_QA_REPORT: {"passed": passed, "issues": []}}
        ctx = SimpleNamespace(session=SimpleNamespace(state=self.state), invocation_id="test", branch=None)
        holder = {"paused": False}
        self.delivery = AsyncMock(return_value={"status": "delivered", "files_sent": 3}, side_effect=error)

        self.children = []
        async def drive(_agent, child, *args):
            self.children.append(child)
            if child == orch.AGENT_STITCH_VERIFY:
                self.state[K_QA_REPORT] = {"passed": passed, "issues": []}
            if False:
                yield

        agent = orch.CarouselOrchestrator(name="test")
        with patch.object(orch.CarouselOrchestrator, "_child", side_effect=lambda name: name), \
             patch.object(orch.CarouselOrchestrator, "_drive", drive), \
             patch.object(orch.CarouselOrchestrator, "_record_phase_quietly", AsyncMock()), \
             patch.object(orch, "deliver_carousel", self.delivery), \
             patch.object(orch, "ToolContext", return_value=SimpleNamespace(state=self.state)):
            async for event in getattr(agent, f"_phase_{phase}")(ctx, self.state, holder):
                self.state.update(event.actions.state_delta)

    async def test_unconnected_run_requires_review(self):
        await self.run_phase("")
        self.assertEqual(self.state[K_PHASE], "review")
        self.assertNotIn(K_PUBLISH_RESULT, self.state)
        self.delivery.assert_not_awaited()

    async def test_connected_run_requires_review_before_publishing(self):
        await self.run_phase("account-a")
        self.assertEqual(self.state[K_PHASE], "review")
        self.delivery.assert_not_awaited()

    async def test_failed_qa_is_reworked_before_delivery(self):
        await self.run_phase("", passed=False)
        self.assertEqual(self.state[K_PHASE], "rework")
        self.delivery.assert_not_awaited()

    async def test_download_run_does_not_depend_on_telegram(self):
        await self.run_phase("", phase="publish", verdict={"status": "approved"}, error=RuntimeError("Telegram unavailable"))
        self.assertEqual(self.state[K_PHASE], "review")
        self.assertNotIn(K_PUBLISH_RESULT, self.state)
        self.delivery.assert_not_awaited()

    async def test_review_without_account_waits_for_a_verdict(self):
        await self.run_phase("", phase="review")
        self.assertEqual(self.state[K_PHASE], "review")
        self.assertNotIn(orch.AGENT_REVIEW_DISPATCHER, self.children)
        self.delivery.assert_not_awaited()

    async def test_rejection_reworks_for_both_destinations(self):
        for account in ("", "account-a"):
            await self.run_phase(account, phase="review", verdict={"status": "rejected", "feedback": "Fix title"})
            self.assertEqual(self.state[K_PHASE], "rework" if account else "review")
            self.delivery.assert_not_awaited()

    async def test_approval_routes_to_publish_phase(self):
        for account in ("", "account-a"):
            await self.run_phase(account, phase="review", verdict={"status": "approved"})
            self.assertEqual(self.state[K_PHASE], "publish" if account else "review")
            self.delivery.assert_not_awaited()

    async def test_publish_resume_without_approval_returns_to_review(self):
        for account in ("", "account-a"):
            for verdict in (None, {"status": "rejected", "feedback": "Fix title"}):
                await self.run_phase(account, phase="publish", verdict=verdict)
                self.assertEqual(self.state[K_PHASE], "review")
                self.assertEqual(self.children, [])
                self.delivery.assert_not_awaited()

    async def test_approval_cannot_send_a_download_only_run(self):
        await self.run_phase("", phase="publish", verdict={"status": "approved"})
        self.assertEqual(self.state[K_PHASE], "review")
        self.assertIsNone(self.state[K_VERDICT])
        self.delivery.assert_not_awaited()
        self.assertNotIn(orch.AGENT_PUBLISHER, self.children)

    async def test_approved_connected_run_invokes_publisher(self):
        await self.run_phase("account-a", phase="publish", verdict={"status": "approved"})
        self.assertIn(orch.AGENT_PUBLISHER, self.children)
        self.delivery.assert_not_awaited()


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        return SimpleNamespace(state={K_VERDICT: {"status": "approved"}, "run_id": "test", "bundle": {
            "cover": {"title": "A carousel"}, "cta": {"cta_type": "follow"},
            "ordered_artifacts": ["cover.mp4", "body.png", "cta.png"],
            "caption": "Full caption"}})

    async def test_unapproved_delivery_sends_nothing(self):
        ctx = self.context()
        ctx.state[K_VERDICT] = None
        with patch.object(tg, "send_completed_carousel") as send:
            with self.assertRaisesRegex(ValueError, "Human approval"):
                await telegram_delivery.deliver_carousel(ctx)
            send.assert_not_called()

    async def test_missing_file_prevents_any_send(self):
        with patch.object(telegram_delivery, "_materialize_artifact", AsyncMock(return_value="")), \
             patch.object(tg, "send_completed_carousel") as send:
            with self.assertRaises(ValueError):
                await telegram_delivery.deliver_carousel(self.context())
            send.assert_not_called()

    async def test_every_artifact_is_delivered_in_order(self):
        with patch.object(telegram_delivery, "_materialize_artifact", AsyncMock(
            side_effect=["/cover.mp4", "/body.png", "/cta.png"])), \
             patch.object(tg, "send_completed_carousel", return_value={"files_sent": 3}) as send:
            result = await telegram_delivery.deliver_carousel(self.context())
        self.assertEqual(send.call_args.args[2], "Full caption")
        self.assertEqual(send.call_args.args[3], ["/cover.mp4", "/body.png", "/cta.png"])
        self.assertEqual(result["status"], "delivered")

    async def test_completed_receipt_prevents_duplicate_delivery(self):
        ctx = self.context()
        ctx.state[K_PUBLISH_RESULT] = {"status": "delivered"}
        with patch.object(tg, "send_completed_carousel") as send:
            await telegram_delivery.deliver_carousel(ctx)
            send.assert_not_called()


class TelegramFilesTests(unittest.TestCase):
    def test_more_than_ten_files_and_full_caption_without_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = []
            for i in range(12):
                path = Path(folder) / f"{i}.png"
                path.write_bytes(b"original image bytes")
                paths.append(str(path))
            caption = "A long caption " * 500
            with patch.object(tg.telegram_config, "all_credentials", return_value=[{"bot_id": "test"}]), \
                 patch.object(tg, "_chat_id", return_value="chat"), \
                 patch.object(tg, "_api_base", return_value="https://example.com"), \
                 patch.object(tg.time, "sleep") as sleep, \
                 patch.object(tg, "_request", return_value={"message_id": 1}) as request:
                result = tg.send_completed_carousel("run", "Title", caption, paths)
            self.assertEqual(result["files_sent"], 12)
            self.assertEqual(sleep.call_count, request.call_count - 1)
            documents = [call for call in request.call_args_list if call.args[1] == "sendDocument"]
            self.assertEqual(len(documents), 12)
            text = "".join(call.kwargs["data"]["text"] for call in request.call_args_list
                           if call.args[1] == "sendMessage")
            self.assertIn(caption, text)
            self.assertNotIn("approve", text.lower())
            for call in request.call_args_list:
                self.assertNotIn("reply_markup", call.kwargs["data"])

    def test_cancelled_delivery_sends_nothing(self):
        with tempfile.NamedTemporaryFile() as file, \
             patch.object(tg.telegram_config, "all_credentials", return_value=[{"bot_id": "test"}]), \
             patch.object(tg, "_chat_id", return_value="chat"), \
             patch.object(tg, "_api_base", return_value="https://example.com"), \
             patch.object(tg, "_request") as request:
            with self.assertRaises(RuntimeError):
                tg.send_completed_carousel("run", "Title", "caption", [file.name], should_continue=lambda: False)
            request.assert_not_called()


class UnbrandedRenderingTests(unittest.TestCase):
    def test_body_and_cta_render_without_account_marks(self):
        with brand_identity.use(brand_identity.BrandIdentity(handle="", favicon_png=b"")), \
             patch.object(brand_layout, "_favicon_from_source") as favicon, \
             patch.object(brand_layout, "_draw_handle") as handle:
            image = Image.new("RGB", (1080, 1350), "white")
            self.assertEqual(brand_layout.apply_body_brand_rail(image, "", 1).size, image.size)
            self.assertEqual(brand_layout.apply_cta_brand_rail(image, "").size, image.size)
            favicon.assert_not_called()
            handle.assert_not_called()
