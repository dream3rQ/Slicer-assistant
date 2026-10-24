# Unit tests for plugin/AIAssistant/knowledge_ui.py (the "База знаний" window's back end),
# on a copy of knowledge/, no slicer needed:  python -m unittest discover -s tests
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugin", "AIAssistant")
if "AIAssistant" not in sys.modules:   # the package without its __init__ (that one needs the slicer)
    _pkg = types.ModuleType("AIAssistant")
    _pkg.__path__ = [PLUGIN]
    sys.modules["AIAssistant"] = _pkg
sys.path.insert(0, os.path.join(ROOT, "plugin"))
try:
    import PyQt5.QtCore  # noqa: F401
except ImportError:      # the bridge only needs QObject and the decorators
    class _QObject:
        def __init__(self, *a, **k): pass
    _core = types.ModuleType("PyQt5.QtCore")
    _core.QObject, _core.QUrl = _QObject, object
    _core.pyqtSignal = lambda *a, **k: None
    _core.pyqtSlot = lambda *a, **k: (lambda f: f)
    sys.modules["PyQt5"] = types.ModuleType("PyQt5")
    sys.modules["PyQt5.QtCore"] = _core

from AIAssistant import knowledge_ui  # noqa: E402
from AIAssistant.agent import materials  # noqa: E402


class KnowledgeBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.folder = os.path.join(self.dir, "knowledge")
        shutil.copytree(os.path.join(PLUGIN, "knowledge"), self.folder)
        self.csv = os.path.join(self.folder, "materials.csv")
        self._csv_path = materials.CSV_PATH
        materials.CSV_PATH = self.csv
        materials._cache["mtime"] = None
        self.bridge = knowledge_ui.KnowledgeBridge(folder=self.folder)

    def tearDown(self):
        materials.CSV_PATH = self._csv_path
        materials._cache["mtime"] = None
        shutil.rmtree(self.dir, ignore_errors=True)

    def call(self, name, *args):
        return json.loads(getattr(self.bridge, name)(*args))

    def read_bytes(self, path):
        with open(path, "rb") as f:
            return f.read()

    def test_save_unchanged_is_byte_for_byte(self):
        before = self.read_bytes(self.csv)
        for item in self.call("materials", ""):
            values = self.call("material", item["material_id"])["values"]
            self.assertTrue(self.call("saveMaterial", item["material_id"], json.dumps(values))["ok"])
        self.assertEqual(self.read_bytes(self.csv), before)
        self.assertTrue(before.startswith(b"\xef\xbb\xbf"))

    def test_edit_reaches_the_agent(self):
        values = self.call("material", "proto_pla")["values"]
        self.assertEqual(materials.load()[0]["heat_c"], 55.0)
        values["heat_c"] = "57,5"
        self.assertTrue(self.call("saveMaterial", "proto_pla", json.dumps(values))["ok"])
        row = [r for r in materials.load() if r["material_id"] == "proto_pla"][0]
        self.assertEqual(row["heat_c"], 57.5)
        self.assertTrue(os.path.exists(self.csv + ".bak"))

    def test_invalid_values_are_refused(self):
        values = self.call("material", "proto_pla")["values"]
        before = self.read_bytes(self.csv)
        bad = dict(values, material_id="Proto PLA!", heat_c="горячо", uv="3", special="да", flex="soft",
                   density="-1", name=" ")
        reply = self.call("saveMaterial", "proto_pla", json.dumps(bad))
        self.assertFalse(reply["ok"])
        self.assertEqual(set(reply["errors"]), {"material_id", "heat_c", "uv", "special", "flex", "density", "name"})
        self.assertEqual(self.read_bytes(self.csv), before)

    def test_duplicate_code_is_refused(self):
        values = self.call("material", "proto_pla")["values"]
        reply = self.call("saveMaterial", "proto_pla", json.dumps(dict(values, material_id="proto_pva")))
        self.assertEqual(list(reply["errors"]), ["material_id"])

    def test_add_duplicate_delete_and_recommend(self):
        copy = self.call("duplicateMaterial", "enduse_petg")["values"]
        self.assertEqual(copy["material_id"], "enduse_petg_copy")
        self.assertTrue(self.call("saveMaterial", "", json.dumps(copy))["ok"])
        new = self.call("newMaterial")["values"]
        new.update(name="Тест", base="PETG", heat_c="70")
        self.assertTrue(self.call("saveMaterial", "", json.dumps(new))["ok"])
        ids = [m["material_id"] for m in self.call("materials", "")]
        self.assertIn("enduse_petg_copy", ids)
        self.assertIn("new_material", ids)
        self.assertTrue(self.call("deleteMaterial", "new_material")["ok"])
        self.assertNotIn("new_material", [r["material_id"] for r in materials.load()])
        result = materials.recommend({"min_heat_c": 60}) if hasattr(materials, "recommend") else None
        if result is not None:
            self.assertTrue(result)

    def test_search(self):
        found = self.call("materials", "tpu")
        self.assertTrue(found)
        self.assertTrue(all("tpu" in json.dumps(m, ensure_ascii=False).lower() for m in found))

    def test_rule_files(self):
        files = self.call("files")
        self.assertEqual([f["name"] for f in files], ["10_print_modes.md", "20_settings.md", "30_materials.md"])
        name = files[0]["name"]
        path = os.path.join(self.folder, name)
        before = self.read_bytes(path)
        text = self.call("readFile", name)["text"]
        self.assertTrue(self.call("saveFile", name, text)["ok"])
        self.assertEqual(self.read_bytes(path), before)
        self.assertTrue(self.call("saveFile", name, text + "\n- новое правило\n")["ok"])
        self.assertIn("новое правило", self.call("readFile", name)["text"])
        self.assertTrue(os.path.exists(path + ".bak"))

    def test_paths_outside_are_refused(self):
        for name in ("../x.md", "..\\x.md", "/etc/passwd", "materials.csv", "x.md", "sub/10_print_modes.md"):
            self.assertIn("error", self.call("readFile", name), name)
            self.assertIn("error", self.call("saveFile", name, "x"), name)


if __name__ == "__main__":
    unittest.main()
