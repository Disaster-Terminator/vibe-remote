from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.scenario_harness.codex_attach import CodexAttachScenarioHarness
from tests.scenario_harness.core import ScenarioExpect, ScenarioRunner, ScenarioStep


class CodexAttachScenarioTests(unittest.IsolatedAsyncioTestCase):
    async def test_external_resume_scenario_keeps_the_same_codex_thread(self):
        """Scenario: CODEX-ATTACH-001"""
        harness = CodexAttachScenarioHarness(
            candidate_thread_id="external-thread-1",
            requested_action="resume",
            resolved_thread_id="external-thread-1",
        )
        runner = ScenarioRunner(harness)

        await runner.run(
            ScenarioStep("discover_candidate", lambda h: h.discover_candidates()),
            ScenarioStep("inspect_metadata", lambda h: h.inspect_candidate()),
            ScenarioStep("choose_resume", lambda h: h.choose_attach_action()),
            ScenarioStep("send_next_turn", lambda h: h.send_next_turn()),
        )

        ScenarioExpect.step_history(runner, ["discover_candidate", "inspect_metadata", "choose_resume", "send_next_turn"])
        self.assertEqual([item.native_session_id for item in harness.discovered_sessions], ["external-thread-1"])
        self.assertIsNotNone(harness.inspected_session)
        inspected_session = harness.inspected_session
        assert inspected_session is not None
        self.assertEqual(inspected_session.locator["codex_thread_id"], "external-thread-1")
        self.assertEqual(inspected_session.locator["allowed_actions"], ["resume", "fork"])
        harness.attach_service.resume_thread.assert_awaited_once_with("/Users/cyh/vibe-remote", "external-thread-1")
        harness.attach_service.fork_thread.assert_not_awaited()
        self.assertEqual(harness.next_turn_thread_id, "external-thread-1")
        self.assertEqual(harness.runtime.turn_thread_ids, ["external-thread-1"])
        attachment = harness.attachment_record()
        self.assertIsNotNone(attachment)
        assert attachment is not None
        self.assertEqual(attachment.attach_mode, "resume")
        self.assertEqual(attachment.codex_thread_id, "external-thread-1")
        self.assertEqual(attachment.validation_status, "valid")
        ScenarioExpect.text_contains(harness, "external-thread-1")
        ScenarioExpect.text_contains(harness, "external Codex thread")

    async def test_ambiguous_duplicate_resume_scenario_forks_to_a_new_codex_thread(self):
        """Scenario: CODEX-ATTACH-002"""
        harness = CodexAttachScenarioHarness(
            candidate_thread_id="external-thread-dup",
            requested_action="resume",
            resolved_thread_id="forked-thread-dup",
            seed_duplicate_binding=True,
        )
        runner = ScenarioRunner(harness)

        await runner.run(
            ScenarioStep("discover_candidate", lambda h: h.discover_candidates()),
            ScenarioStep("inspect_metadata", lambda h: h.inspect_candidate()),
            ScenarioStep("choose_resume_but_fork_on_ambiguity", lambda h: h.choose_attach_action()),
            ScenarioStep("send_next_turn", lambda h: h.send_next_turn()),
        )

        ScenarioExpect.step_history(
            runner,
            ["discover_candidate", "inspect_metadata", "choose_resume_but_fork_on_ambiguity", "send_next_turn"],
        )
        self.assertEqual([item.native_session_id for item in harness.discovered_sessions], ["external-thread-dup"])
        self.assertIsNotNone(harness.inspected_session)
        inspected_session = harness.inspected_session
        assert inspected_session is not None
        self.assertEqual(inspected_session.locator["codex_thread_id"], "external-thread-dup")
        self.assertEqual(inspected_session.locator["allowed_actions"], ["resume", "fork"])
        harness.attach_service.resume_thread.assert_not_awaited()
        harness.attach_service.fork_thread.assert_awaited_once_with("/Users/cyh/vibe-remote", "external-thread-dup")
        self.assertEqual(harness.next_turn_thread_id, "forked-thread-dup")
        self.assertEqual(harness.runtime.turn_thread_ids, ["forked-thread-dup"])
        attachment = harness.attachment_record()
        self.assertIsNotNone(attachment)
        assert attachment is not None
        self.assertEqual(attachment.attach_mode, "fork")
        self.assertEqual(attachment.codex_thread_id, "forked-thread-dup")
        self.assertEqual(attachment.forked_from_thread_id, "external-thread-dup")
        ScenarioExpect.text_contains(harness, "forked-thread-dup")
        ScenarioExpect.text_contains(harness, "external-thread-dup")


if __name__ == "__main__":
    unittest.main()
