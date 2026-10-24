# Unit tests for plugin/AIAssistant/agent/memory.py, no slicer needed:
#   python -m unittest discover -s tests
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "AIAssistant"))

from agent import memory  # noqa: E402

GEAR = {"part": {"name": "Шестерня", "mesh_hash": "aaa", "class": "gear",
                 "features": {"h_over_d": 0.2, "axial_hole": True, "teeth": True}},
        "material": "fiberpart_pa12_g12", "mode": "classic"}
GEAR_COPY = dict(GEAR, part=dict(GEAR["part"], name="Шестерня 2", mesh_hash="bbb"))
WHEEL = {"part": {"name": "Колесо", "mesh_hash": "ccc", "class": "wheel",
                  "features": {"h_over_d": 0.4, "axial_hole": True, "teeth": False}},
         "material": "fiberpart_pa12_g12", "mode": "cylindrical_full"}
PETG_PART = dict(GEAR_COPY, material="enduse_petg")

LIMITS = {
    "wall_line_count": {"type": "int", "minimum_value": 0, "maximum_value": 999,
                        "minimum_value_warning": 1, "maximum_value_warning": 10},
    "cool_fan_speed": {"type": "float", "minimum_value": 0, "maximum_value": 100},
    "adhesion_type": {"type": "enum", "options": {"skirt": "Skirt", "brim": "Brim", "raft": "Raft", "none": "None"}},
    "layer_height": {"type": "float", "minimum_value": 0.001, "maximum_value_warning": 0.32},
    "material_print_temperature": {"type": "float", "minimum_value": -273.15, "maximum_value_warning": 365},
}


def edit(key, proposed, chosen):
    return {memory.item_id_for(key): {"proposed": proposed, "chosen": chosen}}


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "ai_assistant", "memory.json")
        self.mem = memory.Memory(self.path)
        memory.limits_provider = lambda key, context: LIMITS.get(key)

    def tearDown(self):
        memory.limits_provider = None
        shutil.rmtree(self.dir, ignore_errors=True)

    def reload(self):
        return memory.Memory(self.path)

    # ---- journal and storage

    def test_edit_without_remember_is_journal_only(self):
        out = self.mem.record_edits(GEAR, edit("wall_line_count", 4, 6))
        self.assertEqual(len(out["events"]), 1)
        self.assertEqual(self.mem.list_rules(), [])
        events = self.reload().list_events()
        self.assertEqual(events[0]["key"], "wall_line_count")
        self.assertEqual(events[0]["chosen"], 6)
        self.assertEqual(events[0]["role"], "novice")
        self.assertEqual(events[0]["part"]["mesh_hash"], "aaa")

    def test_broken_file_is_backed_up(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write("{not json")
        mem = memory.Memory(self.path)
        self.assertEqual(mem.list_rules(), [])
        self.assertIn("повреждён", mem.load_note)
        mem.record_edits(GEAR, edit("wall_line_count", 4, 6))
        backups = [n for n in os.listdir(os.path.dirname(self.path)) if ".broken-" in n]
        self.assertEqual(len(backups), 1)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["version"], 1)

    def test_save_is_atomic(self):
        self.mem.record_edits(GEAR, edit("wall_line_count", 4, 6))
        leftovers = [n for n in os.listdir(os.path.dirname(self.path)) if ".tmp-" in n]
        self.assertEqual(leftovers, [])

    # ---- roles and scopes

    def test_novice_part_rule(self):
        out = self.mem.record_edits(GEAR, edit("wall_line_count", 4, 6),
                                    {"keys": ["settings.wall_line_count"], "scope": "part"})
        self.assertTrue(out["rules"]["wall_line_count"]["ok"])
        rules = self.mem.rules_for(GEAR)
        self.assertEqual([(r["key"], r["value"], r["scope"], r["status"]) for r in rules],
                         [("wall_line_count", 6, "part", "active")])
        self.assertEqual(self.mem.rules_for(GEAR_COPY), [])   # another part does not get it

    def test_novice_cannot_remember_for_material(self):
        out = self.mem.record_edits(GEAR, edit("wall_line_count", 4, 6),
                                    {"keys": ["wall_line_count"], "scope": "material"})
        self.assertFalse(out["rules"]["wall_line_count"]["ok"])
        self.assertIn("Новичок", out["rules"]["wall_line_count"]["reason"])
        self.assertEqual(self.mem.list_rules(), [])

    def test_expert_material_rule_applies_to_next_part(self):
        self.mem.set_role("expert")
        self.mem.record_edits(GEAR, edit("wall_line_count", 4, 6), {"keys": ["wall_line_count"], "scope": "material"})
        plan, applied = self.mem.apply_rules({"material_id": "fiberpart_pa12_g12", "settings": {"wall_line_count": 3}},
                                             GEAR_COPY)
        self.assertEqual(plan["settings"]["wall_line_count"], 6)
        self.assertEqual(applied[0]["item_id"], "settings.wall_line_count")
        self.assertEqual((applied[0]["from"], applied[0]["to"]), (3, 6))
        # Another material: not applied.
        plan, applied = self.mem.apply_rules({"material_id": "enduse_petg", "settings": {"wall_line_count": 3}},
                                             PETG_PART)
        self.assertEqual(plan["settings"]["wall_line_count"], 3)
        self.assertEqual(applied, [])

    def test_rule_adds_setting_missing_from_plan(self):
        self.mem.set_role("expert")
        self.mem.record_edits(GEAR, edit("wall_line_count", 4, 6), {"keys": ["wall_line_count"], "scope": "global"})
        plan, applied = self.mem.apply_rules({"mode": "classic"}, PETG_PART)
        self.assertEqual(plan["settings"], {"wall_line_count": 6})
        self.assertEqual(applied[0]["from"], None)

    def test_part_rule_beats_material_rule(self):
        self.mem.set_role("expert")
        self.mem.record_edits(GEAR, edit("wall_line_count", 4, 5), {"keys": ["wall_line_count"], "scope": "material"})
        self.mem.record_edits(GEAR, edit("wall_line_count", 5, 8), {"keys": ["wall_line_count"], "scope": "part"})
        plan, _ = self.mem.apply_rules({"settings": {"wall_line_count": 4}}, GEAR)
        self.assertEqual(plan["settings"]["wall_line_count"], 8)
        plan, _ = self.mem.apply_rules({"settings": {"wall_line_count": 4}}, GEAR_COPY)
        self.assertEqual(plan["settings"]["wall_line_count"], 5)

    def test_two_same_edits_make_a_candidate(self):
        self.mem.record_edits(GEAR, edit("wall_line_count", 4, 6))
        self.assertEqual(self.mem.list_rules(), [])
        out = self.mem.record_edits(GEAR_COPY, edit("wall_line_count", 4, 6))
        self.assertEqual(out["rules"]["wall_line_count"]["status"], "candidate")
        rule = self.mem.list_rules()[0]
        self.assertEqual((rule["scope"], rule["scope_value"], rule["status"]),
                         ("material", "fiberpart_pa12_g12", "candidate"))
        self.assertEqual(len(rule["source_event_ids"]), 2)
        # A candidate is a hint only: not applied, but in the prompt block.
        plan, applied = self.mem.apply_rules({"settings": {"wall_line_count": 4}}, WHEEL)
        self.assertEqual(applied, [])
        self.assertIn("Кандидат", self.mem.prompt_block(WHEEL))
        # The expert turns it on.
        with self.assertRaises(memory.MemoryRefused):
            self.mem.update_rule(rule["id"], status="active")
        self.mem.set_role("expert")
        self.mem.update_rule(rule["id"], status="active")
        plan, applied = self.mem.apply_rules({"settings": {"wall_line_count": 4}}, WHEEL)
        self.assertEqual(plan["settings"]["wall_line_count"], 6)

    def test_different_values_do_not_make_a_candidate(self):
        self.mem.record_edits(GEAR, edit("wall_line_count", 4, 6))
        self.mem.record_edits(GEAR_COPY, edit("wall_line_count", 4, 5))
        self.assertEqual(self.mem.list_rules(), [])

    # ---- validation

    def test_validate_slicer_limits(self):
        ok, reason = self.mem.validate("wall_line_count", -1, GEAR)
        self.assertFalse(ok)
        self.assertIn("минимума", reason)
        ok, reason = self.mem.validate("wall_line_count", 12, GEAR)          # above the warning: novice no
        self.assertFalse(ok)
        ok, reason = self.mem.validate("wall_line_count", 12, GEAR, role="expert")
        self.assertTrue(ok)
        self.assertIn("рекомендуемого", reason)
        ok, reason = self.mem.validate("no_such_setting", 1, GEAR)
        self.assertFalse(ok)
        ok, reason = self.mem.validate("adhesion_type", "glue", GEAR)
        self.assertFalse(ok)

    def test_validate_knowledge(self):
        ok, reason = self.mem.validate("cool_fan_speed", 60, GEAR)
        self.assertFalse(ok)
        self.assertIn("базе знаний", reason)
        self.assertTrue(self.mem.validate("cool_fan_speed", 15, GEAR)[0])
        self.assertTrue(self.mem.validate("cool_fan_speed", 60, PETG_PART)[0])
        self.assertFalse(self.mem.validate("adhesion_type", "raft", WHEEL)[0])
        self.assertTrue(self.mem.validate("adhesion_type", "raft", GEAR)[0])
        self.assertFalse(self.mem.validate("material_print_temperature", 120, GEAR)[0])   # PA12 G12 softens at 140
        self.assertTrue(self.mem.validate("material_print_temperature", 260, GEAR)[0])
        self.assertFalse(self.mem.validate("material_id", "no_such_material", GEAR)[0])
        self.assertFalse(self.mem.validate("mode", "classic", GEAR)[0])

    def test_invalid_value_is_not_remembered(self):
        self.mem.set_role("expert")
        out = self.mem.record_edits(GEAR, edit("cool_fan_speed", 10, 80),
                                    {"keys": ["cool_fan_speed"], "scope": "material"})
        self.assertFalse(out["rules"]["cool_fan_speed"]["ok"])
        self.assertIn("базе знаний", out["rules"]["cool_fan_speed"]["reason"])
        self.assertEqual(self.mem.list_rules(), [])
        self.assertIn("не запомнено", self.mem.list_events()[0]["note"])

    def test_update_rule_is_validated(self):
        self.mem.record_edits(GEAR, edit("wall_line_count", 4, 6), {"keys": ["wall_line_count"], "scope": "part"})
        rule = self.mem.list_rules()[0]
        with self.assertRaises(memory.MemoryRefused):
            self.mem.update_rule(rule["id"], value=-3)
        with self.assertRaises(memory.MemoryRefused):   # a novice cannot widen the scope
            self.mem.update_rule(rule["id"], scope="global")
        self.assertEqual(self.mem.update_rule(rule["id"], value=7)["value"], 7)

    # ---- print mode precedents

    def test_mode_edit_is_a_case_not_a_rule(self):
        out = self.mem.record_edits(WHEEL, edit("mode", "classic", "cylindrical_full"),
                                    {"keys": ["mode"], "scope": "material"})
        self.assertEqual(out["rules"]["mode"]["status"], "case")
        self.assertEqual(self.mem.list_rules(), [])
        self.assertEqual(len(self.mem.list_cases()), 1)
        # Same mesh: applied hard.
        plan, applied = self.mem.apply_rules({"mode": "classic"}, WHEEL)
        self.assertEqual(plan["mode"], "cylindrical_full")
        self.assertEqual(applied[0]["kind"], "case")
        # A similar part: an example in the prompt, not applied.
        similar = dict(WHEEL, part=dict(WHEEL["part"], mesh_hash="ddd", name="Колесо 2"))
        plan, applied = self.mem.apply_rules({"mode": "classic"}, similar)
        self.assertEqual(plan["mode"], "classic")
        self.assertIn("Похожая деталь", self.mem.prompt_block(similar))
        self.assertEqual(self.mem.similar_cases(similar["part"]["features"])[0]["mode"], "cylindrical_full")

    def test_prompt_block_is_short(self):
        self.mem.set_role("expert")
        for i in range(30):
            key = "setting_%d" % i
            memory.limits_provider = lambda k, c: {"type": "int"}
            self.mem.record_edits(GEAR, edit(key, 1, 2), {"keys": [key], "scope": "global"})
        block = self.mem.prompt_block(GEAR)
        self.assertLessEqual(len(block.splitlines()), memory.PROMPT_LINES)

    # ---- lists, rollback, export / import

    def test_filters_and_sort(self):
        self.mem.set_role("expert")
        self.mem.record_edits(GEAR, edit("wall_line_count", 4, 6), {"keys": ["wall_line_count"], "scope": "material"})
        self.mem.record_edits(PETG_PART, edit("cool_fan_speed", 50, 40), {"keys": ["cool_fan_speed"], "scope": "material"})
        self.assertEqual([r["key"] for r in self.mem.list_rules({"material": "enduse_petg"})], ["cool_fan_speed"])
        self.assertEqual([r["key"] for r in self.mem.list_rules(sort="key")], ["cool_fan_speed", "wall_line_count"])
        self.assertEqual(len(self.mem.list_events({"action": "edit", "material": "fiberpart_pa12_g12"})), 1)
        self.assertEqual(len(self.mem.list_events({"part": "Шестерня"})), 2)   # edit + rule_add
        self.assertEqual(len(self.mem.list_events({"key": "settings.cool_fan_speed", "action": "edit"})), 1)
        self.assertEqual(len(self.mem.list_events({"from_ts": time.time() + 10})), 0)

    def test_rollback(self):
        self.mem.set_role("expert")
        self.mem.record_edits(GEAR, edit("wall_line_count", 4, 6), {"keys": ["wall_line_count"], "scope": "material"})
        moment = self.mem.list_events()[0]["ts"]
        time.sleep(0.01)
        rule = self.mem.list_rules()[0]
        self.mem.update_rule(rule["id"], value=8)
        self.mem.record_edits(GEAR, edit("cool_fan_speed", 10, 15), {"keys": ["cool_fan_speed"], "scope": "global"})
        self.mem.record_edits(WHEEL, edit("mode", "classic", "cylindrical_full"))
        self.mem.delete_rule(rule["id"])
        self.assertEqual([r["key"] for r in self.mem.list_rules()], ["cool_fan_speed"])
        self.mem.rollback(moment)
        rules = self.reload().list_rules()
        self.assertEqual([(r["key"], r["value"]) for r in rules], [("wall_line_count", 6)])
        self.assertEqual(self.mem.list_cases(), [])
        # The journal kept everything; the rollback itself can be rolled back.
        before_rollback = [e for e in self.mem.list_events() if e["action"] == "rule_delete"][0]["ts"]
        self.mem.rollback(before_rollback)
        self.assertEqual([r["key"] for r in self.mem.list_rules()], ["cool_fan_speed"])

    def test_novice_cannot_rollback(self):
        with self.assertRaises(memory.MemoryRefused):
            self.mem.rollback(time.time())

    def test_export_import(self):
        self.mem.set_role("expert")
        self.mem.record_edits(GEAR, edit("wall_line_count", 4, 6), {"keys": ["wall_line_count"], "scope": "material"})
        self.mem.record_edits(WHEEL, edit("mode", "classic", "cylindrical_full"))
        data = json.loads(json.dumps(self.mem.export()))
        data["rules"].append({"key": "cool_fan_speed", "value": 90, "scope": "material",
                              "scope_value": "fiberpart_pa12_g12", "status": "active"})
        other = memory.Memory(os.path.join(self.dir, "other", "memory.json"))
        other.set_role("expert")
        report = other.import_(data)
        self.assertEqual(report["added"], 1)
        self.assertEqual(report["cases"], 1)
        self.assertIn("базе знаний", report["skipped"][0]["reason"])   # fan 90 % for PA12 rejected here too
        plan, applied = other.apply_rules({"settings": {"wall_line_count": 3}}, GEAR_COPY)
        self.assertEqual(plan["settings"]["wall_line_count"], 6)
        self.assertEqual(other.import_(data)["added"], 0)               # merging twice adds nothing


if __name__ == "__main__":
    unittest.main()
