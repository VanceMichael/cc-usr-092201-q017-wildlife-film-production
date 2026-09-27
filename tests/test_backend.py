import unittest
from datetime import date
from pathlib import Path

from production_backend import (
    AdaptationLevel,
    PermitStatus,
    ProductionBackend,
    ReviewKind,
    ReviewStatus,
    Role,
    WorkflowState,
    load_production,
)

FIXTURE = Path("fixtures/production.json")


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.backend = load_production(FIXTURE)

    # --------------------------------------------------------------
    # 资料核实与剧本审签
    # --------------------------------------------------------------
    def test_unverified_source_blocks_scene_approval(self):
        with self.assertRaises(ValueError):
            self.backend.approve_scene("sc-03", approver="责编")
        self.backend.verify_source(
            "src-int-002", verifier="资料核实组", note="与救助站排班表核对一致"
        )
        scene = self.backend.approve_scene("sc-03", approver="责编")
        self.assertIs(scene.review, ReviewStatus.APPROVED)

    def test_script_change_reopens_review(self):
        ticket = self.backend.change_script(
            "sc-01", actor="编剧", summary="卓玛发现雪豹后先呼叫支援再守护。"
        )
        self.assertIs(ticket.kind, ReviewKind.SCRIPT)
        scene = self.backend.scenes["sc-01"]
        self.assertIs(scene.review, ReviewStatus.PENDING)
        self.assertEqual(scene.version, 2)
        self.assertIn("ft-001", ticket.reason)  # 已拍素材一并提示复核
        with self.assertRaises(RuntimeError):
            self.backend.advance_state(actor="制片人")
        self.backend.approve_scene("sc-01", approver="责编")
        self.assertFalse(self.backend.reviews.pending())

    def test_adapted_scene_requires_boundary_note(self):
        self.backend.change_script("sc-04", actor="编剧", adaptation_note="")
        with self.assertRaises(ValueError):
            self.backend.approve_scene("sc-04", approver="责编")

    # --------------------------------------------------------------
    # 人物授权
    # --------------------------------------------------------------
    def test_consent_withdrawal_flags_material_and_blocks_release(self):
        result = self.backend.withdraw_consent("consent-zhuoma", actor="法务")
        self.assertIn("sc-01", result["scenes"])
        self.assertIn("ft-001", result["footage"])
        self.assertIs(result["ticket"].kind, ReviewKind.CONSENT)

        report = self.backend.trace_scene("sc-01")
        self.assertTrue(any("已撤回" in i for i in report.issues))

        decision = self.backend.certify_release("rel-v1", manager="发布负责人")
        self.assertFalse(decision.approved)
        failed = [c.name for c in decision.checks if not c.passed]
        self.assertIn("人物授权有效", failed)
        self.assertIn("无未完成审签", failed)

    # --------------------------------------------------------------
    # 保护区临时关闭与每日通告
    # --------------------------------------------------------------
    def test_call_sheet_lists_allowed_areas_and_behaviors(self):
        sheet = self.backend.call_sheet(date(2026, 6, 15), requester="现场制片")
        entry = sheet.entry_for("area-core")
        self.assertTrue(entry.allowed)
        self.assertIn("徒步跟拍", entry.allowed_behaviors)
        self.assertIn("无人机航拍", entry.forbidden_behaviors)
        self.assertIn("不投喂野生动物", entry.eco_conditions)
        self.assertEqual(entry.location_precision, "县级")

    def test_call_sheet_denies_day_outside_permit_window(self):
        sheet = self.backend.call_sheet(date(2026, 11, 5), requester="现场制片")
        entry = sheet.entry_for("area-core")
        self.assertFalse(entry.allowed)
        self.assertTrue(any("无有效取景许可" in r for r in entry.reasons))

    def test_area_closure_suspends_permit_and_call_sheet(self):
        day = date(2026, 7, 5)
        self.backend.close_area(
            "area-core",
            start=date(2026, 7, 1),
            end=date(2026, 7, 15),
            reason="雪豹繁殖期临时管控",
            actor="保护区管理局",
        )
        self.assertIs(self.backend.permits["ECO-FILM-026"].status, PermitStatus.SUSPENDED)

        sheet = self.backend.call_sheet(day, requester="现场制片")
        entry = sheet.entry_for("area-core")
        self.assertFalse(entry.allowed)
        self.assertTrue(any("临时关闭" in r for r in entry.reasons))
        self.assertTrue(sheet.entry_for("area-buffer").allowed)  # 缓冲区不受影响

        self.backend.reopen_area("area-core", actor="保护区管理局")
        self.assertIs(self.backend.permits["ECO-FILM-026"].status, PermitStatus.ACTIVE)
        after = self.backend.call_sheet(day, requester="现场制片")
        self.assertTrue(after.entry_for("area-core").allowed)

    # --------------------------------------------------------------
    # 疑似素材泄露
    # --------------------------------------------------------------
    def test_suspected_leak_quarantines_footage_until_cleared(self):
        self.backend.report_leak("ft-001", actor="素材管理员", detail="发现外链截图")
        self.assertTrue(self.backend.footage["ft-001"].quarantined)
        decision = self.backend.certify_release("rel-v1", manager="发布负责人")
        self.assertFalse(decision.approved)

        self.backend.resolve_leak("ft-001", actor="安全负责人", confirmed=False)
        self.assertFalse(self.backend.footage["ft-001"].quarantined)
        self.assertFalse(self.backend.reviews.pending())

    def test_confirmed_leak_keeps_footage_quarantined(self):
        self.backend.report_leak("ft-002", actor="素材管理员")
        self.backend.resolve_leak("ft-002", actor="安全负责人", confirmed=True)
        self.assertTrue(self.backend.footage["ft-002"].quarantined)
        decision = self.backend.certify_release("rel-v1", manager="发布负责人")
        self.assertFalse(decision.approved)
        failed = [c.name for c in decision.checks if not c.passed]
        self.assertIn("素材未被隔离", failed)

    # --------------------------------------------------------------
    # 海外翻译变化
    # --------------------------------------------------------------
    def test_translation_change_blocks_release_until_reviewed(self):
        ticket = self.backend.change_translation(
            "rel-v1", locale="en", actor="海外发行", note="英文旁白改稿"
        )
        self.assertIs(ticket.kind, ReviewKind.TRANSLATION)
        decision = self.backend.certify_release("rel-v1", manager="发布负责人")
        self.assertFalse(decision.approved)

        self.backend.reviews.resolve(
            ticket.ticket_id, actor="翻译责编", resolution="英文版已复核"
        )
        decision = self.backend.certify_release("rel-v1", manager="发布负责人")
        self.assertTrue(decision.approved)

    # --------------------------------------------------------------
    # 敏感坐标访问
    # --------------------------------------------------------------
    def test_coordinate_access_limited_to_cleared_roles(self):
        with self.assertRaises(PermissionError):
            self.backend.vault.reveal("area-core", role=Role.EDITOR, actor="剪辑师")
        coords = self.backend.vault.reveal(
            "area-core", role=Role.LOCATION_MANAGER, actor="外联制片"
        )
        self.assertTrue(coords)
        actions = [e.action for e in self.backend.audit.entries()]
        self.assertIn("坐标访问被拒", actions)
        self.assertIn("坐标访问", actions)

    # --------------------------------------------------------------
    # 剪辑溯源
    # --------------------------------------------------------------
    def test_editor_traces_scene_basis(self):
        report = self.backend.trace_scene("sc-01")
        self.assertEqual(report.issues, [])
        self.assertTrue(all(s.verified for s in report.sources))

        fictional = self.backend.trace_scene("sc-04")
        self.assertIs(fictional.adaptation, AdaptationLevel.FICTIONALIZED)
        self.assertTrue(fictional.adaptation_note)

        pending = self.backend.trace_scene("sc-03")
        self.assertTrue(any("未核实" in i for i in pending.issues))

    # --------------------------------------------------------------
    # 发布审签
    # --------------------------------------------------------------
    def test_release_certification_happy_path(self):
        decision = self.backend.certify_release("rel-v1", manager="发布负责人")
        self.assertTrue(
            decision.approved, [c.detail for c in decision.checks if not c.passed]
        )
        self.assertTrue(self.backend.releases["rel-v1"].certified)

    def test_precise_location_blocks_release_until_scrubbed(self):
        self.backend.releases["rel-v1"].footage_ids.append("ft-003")
        decision = self.backend.certify_release("rel-v1", manager="发布负责人")
        self.assertFalse(decision.approved)
        failed = [c.name for c in decision.checks if not c.passed]
        self.assertIn("未暴露精确位置", failed)

        self.backend.scrub_location("ft-003", actor="素材管理员")
        decision = self.backend.certify_release("rel-v1", manager="发布负责人")
        self.assertTrue(decision.approved)

    def test_narration_with_coordinates_fails_location_check(self):
        self.backend.releases["rel-v1"].narration_text += "（坐标 35.4412, 96.8831）"
        decision = self.backend.certify_release("rel-v1", manager="发布负责人")
        self.assertFalse(decision.approved)
        failed = [c.name for c in decision.checks if not c.passed]
        self.assertIn("未暴露精确位置", failed)

    def test_release_dossier_collects_decision_and_audit(self):
        self.backend.certify_release("rel-v1", manager="发布负责人")
        dossier = self.backend.release_dossier("rel-v1")
        self.assertIsNotNone(dossier["decision"])
        self.assertTrue(any(e.action == "发布审签" for e in dossier["audit_trail"]))

    # --------------------------------------------------------------
    # 主流程
    # --------------------------------------------------------------
    def test_workflow_advances_to_published_after_certification(self):
        self.assertIs(self.backend.state, WorkflowState.EDITING)
        self.backend.certify_release("rel-v1", manager="发布负责人")
        self.backend.advance_state(actor="制片人")
        self.assertIs(self.backend.state, WorkflowState.PUBLISHED)
        with self.assertRaises(RuntimeError):
            self.backend.advance_state(actor="制片人")

    def test_publish_requires_certified_release(self):
        backend = ProductionBackend(state=WorkflowState.EDITING)
        with self.assertRaises(RuntimeError):
            backend.advance_state(actor="制片人")


if __name__ == "__main__":
    unittest.main()
