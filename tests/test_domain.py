import json
import unittest
from pathlib import Path
from domain_context.loader import load_domain

FIXTURE = Path("fixtures/domain.json")
SCHEMA = Path("contracts/domain.schema.json")

class DomainFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.value = load_domain(FIXTURE)

    def test_fixture_is_complete(self):
        self.assertEqual(self.value["domain"], "wildlife-film-production")
        self.assertGreaterEqual(len(self.value["facts"]), 2)

    def test_fixture_matches_declared_contract(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertTrue(set(schema["required"]).issubset(self.value))
        self.assertTrue(set(self.value).issubset(schema["properties"]))

    def test_records_cover_production_backend(self):
        for record in ["救助档案", "生态影响评估", "拍摄许可", "拍摄通告", "审签记录"]:
            self.assertIn(record, self.value["record_types"])
        self.assertIn("变更复核", self.value["workflow_states"])

    def test_coordinate_access_is_need_to_know(self):
        levels = self.value["coordinate_levels"]
        self.assertEqual(levels[0], "县级")
        self.assertEqual(levels[-1], "精确坐标")
        precise = [r["id"] for r in self.value["roles"] if r["coordinate_access"] == "精确坐标"]
        self.assertTrue(precise)
        self.assertNotIn("剪辑师", precise)
        self.assertNotIn("发布负责人", precise)
        for role in self.value["roles"]:
            self.assertIn(role["coordinate_access"], levels)

    def test_change_triggers_cover_required_events(self):
        events = {t["event"] for t in self.value["change_triggers"]}
        for event in ["剧本修改", "当事人撤回授权", "保护区临时关闭", "疑似素材泄露", "海外翻译变化"]:
            self.assertIn(event, events)

    def test_release_checklist_covers_position_process_and_usage(self):
        checklist = "".join(self.value["release_checklist"])
        self.assertIn("位置", checklist)
        self.assertIn("救助过程", checklist)
        self.assertIn("授权", checklist)

if __name__ == "__main__":
    unittest.main()
