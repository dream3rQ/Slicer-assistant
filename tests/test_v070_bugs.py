# Bugs found in v0.7.0 (docs: ste-ai/qa-v0.7.0/bugs.md), kept as regression tests; no slicer needed.
# Run: python -m unittest discover -s tests  (from the repository root)
import json, os, shutil, socket, sys, tempfile, types, unittest
from unittest import mock

PLUGIN = os.environ.get("PLUGIN") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "AIAssistant")
sys.path.insert(0, os.path.dirname(PLUGIN))

# ---- stubs for the slicer (UM) and PyQt5, enough to import the modules
def stub(name, **attrs):
    m = types.ModuleType(name); m.__dict__.update(attrs); sys.modules[name] = m; return m
class _Logger:
    @staticmethod
    def log(*a, **k): pass
    @staticmethod
    def logException(*a, **k): pass
stub("UM"); stub("UM.Logger", Logger=_Logger)
class _App:
    @staticmethod
    def getInstance(): return None
stub("UM.Application", Application=_App)
class _QObject:
    def __init__(self, *a, **k): pass
def _sig(*a, **k):
    class S:
        def emit(self, *a): pass
        def connect(self, *a): pass
    return S()
def _deco(*a, **k): return lambda f: f
stub("PyQt5"); stub("PyQt5.QtCore", QObject=_QObject, pyqtSignal=_sig, pyqtSlot=_deco,
                    pyqtProperty=lambda *a, **k: (lambda f: property(f)))

_pkg = types.ModuleType("AIAssistant"); _pkg.__path__ = [os.path.abspath(PLUGIN)]; sys.modules["AIAssistant"] = _pkg
import AIAssistant.agent.memory as memory          # noqa: E402
import AIAssistant.memory_ui as memory_ui          # noqa: E402
import AIAssistant.agent.llm as llm                # noqa: E402

GEAR = {"part": {"name": "Шестерня", "mesh_hash": "3fa9c1d2e4b56a78", "class": "gear", "features": {"h_over_d": 0.2}},
        "material": "fiberpart_pa12_g12", "mode": "classic"}
LIMITS = {"wall_line_count": {"type": "int", "minimum_value": 0, "maximum_value": 999,
                              "minimum_value_warning": 1, "maximum_value_warning": 10},
          "infill_sparse_density": {"type": "float", "minimum_value": 0, "maximum_value": 100}}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.mem = memory.Memory(os.path.join(self.dir, "memory.json"))
        memory._instance["memory"] = self.mem
        memory.limits_provider = lambda key, ctx: LIMITS.get(key)

    def tearDown(self):
        memory.limits_provider = None
        memory._instance["memory"] = None
        shutil.rmtree(self.dir, ignore_errors=True)


class MemoryWindowText(Base):
    def test_rollback_line_in_journal(self):
        self.mem.set_role("expert")
        self.mem.record_edits(GEAR, {"settings.wall_line_count": {"proposed": 4, "chosen": 6}},
                              {"keys": ["settings.wall_line_count"], "scope": "material"})
        self.mem.rollback(0)
        row = [memory_ui.event_row(e) for e in self.mem.list_events() if e["action"] == "rollback"][0]
        print("\n  journal line for a rollback:", row["change"])
        self.assertNotIn("None", row["change"])

    def test_part_rule_scope_shows_part_name(self):
        self.mem.record_edits(GEAR, {"settings.infill_sparse_density": {"proposed": 20, "chosen": 40}},
                              {"keys": ["settings.infill_sparse_density"], "scope": "part"})
        rule = self.mem.list_rules()[0]
        text = memory_ui.rule_row(rule)["scope_text"]
        print("\n  scope of a part rule in the window:", text)
        self.assertIn("Шестерня", text)

    def test_deleted_rule_line_shows_value(self):
        self.mem.record_edits(GEAR, {"settings.infill_sparse_density": {"proposed": 20, "chosen": 40}},
                              {"keys": ["settings.infill_sparse_density"], "scope": "part"})
        self.mem.delete_rule(self.mem.list_rules()[0]["id"])
        row = [memory_ui.event_row(e) for e in self.mem.list_events() if e["action"] == "rule_delete"][0]
        print("\n  journal line for a deleted rule:", row["change"])
        self.assertIn("40", row["change"])


class CardMemory(Base):
    def test_card_check_for_mode_precedent(self):
        # PlanCard.qml asks manager.memory.validate("mode", ...) when "Запомнить" is ticked on the mode row
        reply = json.loads(memory_ui.MemoryBridge().validate("mode", json.dumps("cylindrical_full"),
                                                             json.dumps({"scope": "part"})))
        print("\n  card check for the mode row:", reply)
        self.assertTrue(reply["ok"], "the card shows «Не запомнится», yet record_edits saves the precedent")

    def test_mode_plaque_rule_lookup(self):
        # a precedent sets the mode; the card's plaque looks the "rule" up by applied[].rule_id (a case id)
        self.mem.record_edits(GEAR, {"mode": {"proposed": "classic", "chosen": "cylindrical_full"}})
        plan, applied = self.mem.apply_rules({"mode": "classic"}, GEAR)
        case_id = [a for a in applied if a["item_id"] == "mode"][0]["rule_id"]
        found = memory_ui.MemoryBridge().rule(case_id)
        print("\n  plaque lookup for the mode row:", found)
        self.assertNotEqual(found, "null", "the card says «Правило уже удалено»")

    def test_material_row_material_scope(self):
        self.mem.set_role("expert")
        bridge = json.loads(memory_ui.MemoryBridge().validate(
            "material_id", json.dumps("enduse_petg"), json.dumps({"scope": "material", "material": "enduse_petg"})))
        saved = self.mem.record_edits(GEAR, {"material_id": {"proposed": "fiberpart_pa12_g12", "chosen": "enduse_petg"}},
                                      {"keys": ["material_id"], "scope": "material"})
        print("\n  card check:", bridge, "| after apply:", saved["rules"]["material_id"])
        self.assertEqual(bridge["ok"], saved["rules"]["material_id"]["ok"])


class MemoryData(Base):
    def test_import_replace_also_replaces_cases(self):
        self.mem.set_role("expert")
        self.mem.record_edits(GEAR, {"mode": {"proposed": "classic", "chosen": "cylindrical_full"}})
        self.mem.import_({"rules": [], "cases": []}, merge=False)
        print("\n  cases after «заменить»:", len(self.mem.list_cases()))
        self.assertEqual(self.mem.list_cases(), [])

    def test_rollback_restores_replaced_case(self):
        self.mem.set_role("expert")
        self.mem.record_edits(GEAR, {"mode": {"proposed": "classic", "chosen": "cylindrical_full"}})
        t = max(e["ts"] for e in self.mem.list_events())
        import time; time.sleep(0.01)
        self.mem.record_edits(GEAR, {"mode": {"proposed": "cylindrical_full", "chosen": "classic"}})
        self.mem.rollback(t)
        case = self.mem.case_for(GEAR)
        print("\n  precedent after rollback:", case and case["mode"])
        self.assertIsNotNone(case)
        self.assertEqual(case["mode"], "cylindrical_full")


class Network(unittest.TestCase):
    def test_read_timeout_becomes_llm_error(self):
        with mock.patch.object(llm.config, "get", lambda k: {"llm_url": "https://x/v1", "ssl_verify": True}.get(k)), \
             mock.patch("urllib.request.urlopen", side_effect=socket.timeout("timed out")), \
             mock.patch("time.sleep"):
            try:
                llm.chat([{"role": "user", "content": "hi"}])
            except llm.LLMError:
                return
            except Exception as e:
                self.fail("raw %s, the chat shows «Внутренняя ошибка ассистента: %s» and does not retry"
                          % (type(e).__name__, e))


if __name__ == "__main__":
    unittest.main()


class _Msg:
    def __init__(self, *a, **k): pass
    def show(self): pass
stub("UM.Message", Message=_Msg)
import AIAssistant.agent.session as session_module   # noqa: E402
import AIAssistant.slicer_ui as slicer_ui            # noqa: E402


def _proposal():
    import threading
    return {"id": "p_1", "event": threading.Event(), "answer": None, "memory_context": {}}


class SessionAndCard(unittest.TestCase):
    def test_attachments_sent_while_card_open(self):
        s = session_module.Session()
        s.uploads["f_1"] = {"id": "f_1", "name": "ТЗ.docx", "kind": "document", "text": "ТЗ"}
        s.proposal = _proposal()
        s.post_user_message("учти ещё ТЗ", ["f_1"])
        user = [e for e in s.events if e["type"] == "user"][-1]["data"]
        print("\n  attachments in the message:", user["attachments"], "| answer:", s.proposal["answer"])
        self.assertTrue(user["attachments"], "slicer_chat.send() then clears the attachment chips: the file is lost")

    def test_card_closes_on_new_conversation(self):
        class Card:
            visible = True
            def setProperty(self, k, v):
                if k == "visible": self.visible = v
        old, new = session_module.Session(), session_module.Session()
        server = types.SimpleNamespace(session=old)
        ext = types.SimpleNamespace(server=lambda: server, panelVisible=lambda: True, panelItem=lambda: None)
        with mock.patch.object(slicer_ui.config, "get", lambda k: True):
            ui = slicer_ui.SlicerUI(ext, None)
            ui._card = Card()
            ui._onEvent(old, {"type": "proposal", "data": {"id": "p_1", "items": []}})
            server.session = new        # "Новый диалог": ChatServer.reset_session()
            ui._onEvent(old, {"type": "proposal_closed", "data": {"id": "p_1", "action": "cancel"}})
        print("\n  card visible after «Новый диалог»:", ui._card.visible)
        self.assertFalse(ui._card.visible)
