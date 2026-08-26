"""The pipeline's vocabulary, pinned across all four places that hold it.

The agent and phase names live in four files that must agree, and every
disagreement fails silently rather than loudly:

* ``app/state.py`` - the constants the orchestrator routes on;
* ``app/agent.py`` - the tree actually built, whose children the orchestrator
  looks up BY NAME at runtime (``_child`` raises only when that phase runs, so
  a missing child is a crash twenty minutes into a paid run, not at startup);
* ``GET /api/meta`` - what the console is told;
* ``frontend/src/lib/pipeline.ts`` - the labels, colours and phase rail. A name
  the frontend does not know renders as a blank row or an ``undefined`` chip.

``assertNoDrift`` in pipeline.ts compares the last two, but only in a dev build
and only as a ``console.error`` nobody is watching. These tests are the part
that can actually fail a build.

Building the tree also exercises every agent builder, which is the cheapest
possible check that no agent is misconfigured - notably the ADK rule that an
agent with an ``output_schema`` may not also declare tools.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

# Keep the suite hermetic. Building the tree initialises tracing, and with
# real LANGFUSE_* keys in the environment that ships spans to a remote
# collector and blocks the run on its export timeout.
os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
os.environ.pop("LANGFUSE_SECRET_KEY", None)

from app.agent import build_root_agent  # noqa: E402
from app.orchestrator import GENERATE_ORDER, ORCHESTRATOR_NAME
from app.state import (
    AGENT_FEEDBACK_ROUTER,
    AGENT_LEARNER,
    AGENT_PUBLISHER,
    AGENT_REVIEW_DISPATCHER,
    AGENT_STITCH_VERIFY,
    PHASE_DONE,
    PHASE_GENERATE,
    PHASE_PUBLISH,
    PHASE_QA,
    PHASE_REVIEW,
    PHASE_REWORK,
    REWORKABLE_AGENTS,
)

REPO = Path(__file__).resolve().parent.parent
PIPELINE_TS = REPO / "frontend" / "src" / "lib" / "pipeline.ts"

#: Every agent the orchestrator asks for by name at some point in a run.
REQUIRED_CHILDREN = set(GENERATE_ORDER) | {
    AGENT_STITCH_VERIFY,
    AGENT_REVIEW_DISPATCHER,
    AGENT_FEEDBACK_ROUTER,
    AGENT_PUBLISHER,
    AGENT_LEARNER,
}

ALL_PHASES = {
    PHASE_GENERATE,
    PHASE_QA,
    PHASE_REVIEW,
    PHASE_REWORK,
    PHASE_PUBLISH,
    PHASE_DONE,
}


def _ts_string_array(source: str, name: str) -> list[str]:
    """Pull a ``const NAME[: T] = [ "a", "b" ]`` array out of the TypeScript."""
    match = re.search(rf"const {name}\s*(?::[^=]+)?=\s*\[(.*?)\]", source, re.DOTALL)
    if match is None:
        raise AssertionError(f"{name} not found in {PIPELINE_TS}")
    return re.findall(r'"([^"]+)"', match.group(1))


def _ts_record_keys(source: str, name: str) -> set[str]:
    """Pull the keys of a ``const NAME: Record<...> = { a: ..., }`` literal."""
    match = re.search(rf"const {name}[^=]*=\s*\{{(.*?)\n\}}", source, re.DOTALL)
    if match is None:
        raise AssertionError(f"{name} not found in {PIPELINE_TS}")
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", match.group(1), re.M))


class AgentTreeTests(unittest.TestCase):
    """The tree the orchestrator drives must contain everything it looks up."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = build_root_agent()
        cls.children = {child.name: child for child in cls.root.sub_agents}

    def test_the_root_is_the_orchestrator(self) -> None:
        self.assertEqual(self.root.name, ORCHESTRATOR_NAME)

    def test_every_agent_the_orchestrator_drives_is_in_the_tree(self) -> None:
        missing = sorted(REQUIRED_CHILDREN - set(self.children))
        self.assertEqual(
            missing,
            [],
            f"CarouselOrchestrator._child({missing!r}) raises ValueError only "
            "when that phase is reached, so a missing child surfaces as a "
            "crash part-way through a paid run rather than at startup.",
        )

    def test_every_reworkable_agent_can_actually_be_re_run(self) -> None:
        missing = sorted(set(REWORKABLE_AGENTS) - set(self.children))
        self.assertEqual(
            missing,
            [],
            "feedback_router may target these names, and the orchestrator "
            "then looks each one up in the tree.",
        )

    def test_no_agent_declares_both_an_output_schema_and_tools(self) -> None:
        conflicts = [
            name
            for name, child in self.children.items()
            if getattr(child, "output_schema", None) is not None
            and (getattr(child, "tools", None) or [])
        ]
        self.assertEqual(
            conflicts,
            [],
            "ADK refuses to call tools on an agent with an output_schema; "
            "such an agent silently produces no tool effects at all.",
        )

    def test_pipeline_nodes_never_transfer_control(self) -> None:
        """The orchestrator drives the order; a child must not re-route it.

        google-adk 2.7 ``LlmAgent._llm_flow`` returns ``SingleFlow()`` ONLY
        when both transfer flags are set and the agent has no sub_agents;
        otherwise it returns ``AutoFlow()``, which hands the model a
        ``transfer_to_agent`` tool naming every peer in the tree.

        Nine of the eleven pipeline agents set both flags. Any that does not
        can hand its turn to another agent mid-phase, and
        ``CarouselOrchestrator._drive`` only re-yields whatever events come
        back - it never checks the author - so the phase loop records the
        stage as done and moves on with the state key that stage was supposed
        to write left unwritten or stale.
        """
        loose = sorted(
            name
            for name, child in self.children.items()
            if not getattr(child, "disallow_transfer_to_parent", False)
            or not getattr(child, "disallow_transfer_to_peers", False)
        )
        self.assertEqual(
            loose,
            [],
            f"{loose} run on AutoFlow and can transfer control to a peer, "
            "silently skipping or repeating a pipeline stage. Set "
            "disallow_transfer_to_parent=True and "
            "disallow_transfer_to_peers=True, as every other node does.",
        )


class FrontendVocabularyTests(unittest.TestCase):
    """What the console can render must cover what the pipeline can emit."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PIPELINE_TS.read_text(encoding="utf-8")
        cls.root = build_root_agent()

    def test_the_frontend_knows_every_agent_in_the_tree(self) -> None:
        ui_agents = set(_ts_string_array(self.source, "AGENTS"))
        backend = {child.name for child in self.root.sub_agents}
        self.assertEqual(
            sorted(backend - ui_agents),
            [],
            "an agent the UI does not know groups the trace under a raw "
            "snake_case name with no label and no blurb",
        )
        self.assertEqual(
            sorted(ui_agents - backend),
            [],
            "the UI lists an agent the pipeline no longer builds",
        )

    def test_every_agent_has_a_label_and_a_blurb(self) -> None:
        labels = _ts_record_keys(self.source, "AGENT_LABELS")
        blurbs = _ts_record_keys(self.source, "AGENT_BLURBS")
        backend = {child.name for child in self.root.sub_agents}
        self.assertEqual(sorted(backend - labels), [], "missing AGENT_LABELS entry")
        self.assertEqual(sorted(backend - blurbs), [], "missing AGENT_BLURBS entry")

    def test_the_frontend_knows_every_phase(self) -> None:
        ui_phases = set(_ts_string_array(self.source, "PHASES"))
        self.assertEqual(
            sorted(ALL_PHASES - ui_phases),
            [],
            "a phase the UI does not know renders an empty chip and puts the "
            "phase rail on index -1, so no step lights up at all",
        )

    def test_every_status_the_backend_can_record_has_a_label(self) -> None:
        from app.services import db

        statuses = {
            db.RUN_STATUS_RUNNING,
            db.RUN_STATUS_AWAITING_REVIEW,
            db.RUN_STATUS_DONE,
            db.RUN_STATUS_INTERRUPTED,
            db.RUN_STATUS_FAILED,
            db.RUN_STATUS_CANCELLED,
        }
        labels = _ts_record_keys(self.source, "STATUS_LABELS")
        tokens = _ts_record_keys(self.source, "STATUS_TOKEN")
        self.assertEqual(
            sorted(statuses - labels),
            [],
            "STATUS_LABELS[status] would be undefined, rendering an empty chip",
        )
        self.assertEqual(sorted(statuses - tokens), [], "missing STATUS_TOKEN entry")

    def test_the_reject_categories_only_name_reworkable_agents(self) -> None:
        """The reviewer's categories are a promise about what will re-run."""
        match = re.search(
            r"const REJECT_CATEGORIES\s*=\s*\[(.*?)\]\s*as const",
            self.source,
            re.DOTALL,
        )
        assert match is not None, "REJECT_CATEGORIES not found"
        named = set(re.findall(r"targets:\s*\[([^\]]*)\]", match.group(1)))
        targets = {
            t.strip().strip('"')
            for group in named
            for t in group.split(",")
            if t.strip()
        }
        unknown = sorted(targets - set(REWORKABLE_AGENTS))
        self.assertEqual(
            unknown,
            [],
            "the reject card predicts these agents will re-run, but "
            "_expand_rework_targets drops anything outside REWORKABLE_AGENTS, "
            f"so {unknown} would be shown to the reviewer and then ignored",
        )

    def test_both_run_screens_keep_polling_while_a_decision_is_pending(self) -> None:
        """A tab left open on /new must notice the publish.

        React Query re-evaluates ``refetchInterval`` only after a fetch
        settles, so returning ``false`` at ``awaiting_review`` stops the
        interval permanently - there is no further fetch to re-evaluate it.
        The same status sets ``isLive = false``, which closes the EventSource
        and disables the 3s trace poll. The page then has no polling, no
        stream and no push channel at exactly the moment the reviewer
        approves from Telegram and the pipeline does its remaining work.

        ``run-detail.tsx`` already handles this; ``new-run.tsx`` does not.
        """
        new_run = (REPO / "frontend" / "src" / "routes" / "new-run.tsx").read_text(
            encoding="utf-8"
        )
        block = re.search(
            r"refetchInterval:\s*\(query\)\s*=>(.*?)\n\s*refetchIntervalInBackground",
            new_run,
            re.DOTALL,
        )
        assert block is not None, "the run query's refetchInterval was not found"
        self.assertIn(
            "awaiting_review",
            block.group(1),
            "new-run.tsx polls only while status is 'running' and returns "
            "false otherwise, so once the task reaches review the New "
            "carousel screen freezes on 'Your carousel is ready for review' "
            "forever - through the approval, the rework rounds and the "
            "publish - until the user reloads or re-focuses the window.",
        )

    def test_the_rework_dependency_map_matches_the_orchestrator(self) -> None:
        """The card's prediction must match what the orchestrator will do."""
        from app.orchestrator import _REWORK_DEPENDENTS

        match = re.search(
            r"const REWORK_DEPENDENTS: Record<string, string\[\]> = \{(.*?)\n\}",
            self.source,
            re.DOTALL,
        )
        assert match is not None, "REWORK_DEPENDENTS not found"
        ui_map = {
            key: re.findall(r'"([^"]+)"', value)
            for key, value in re.findall(
                r"^\s*([a-z_]+):\s*\[([^\]]*)\]", match.group(1), re.M
            )
        }
        backend_map = {k: list(v) for k, v in _REWORK_DEPENDENTS.items()}
        self.assertEqual(
            ui_map,
            backend_map,
            "predictRework() tells the reviewer which agents a rejection will "
            "re-run. When this map drifts from _REWORK_DEPENDENTS the console "
            "makes a promise the pipeline does not keep.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
