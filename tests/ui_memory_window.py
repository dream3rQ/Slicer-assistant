# The "Память агента" window clicked through on demo data: switch, edit, delete, sort,
# filters, journal, rollback, roles, close. Needs PyQt5 with QtQuick and a display:
#   QT_QUICK_BACKEND=software python tests/ui_memory_window.py    (xvfb-run on Linux)
# Screenshots go to tests/ui_out/. Prints ok/FAIL per check and RESULT n / m.
import sys, os, json, time, tempfile
os.environ["HOME"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.join(ROOT, "tests", "ui_out")
os.makedirs(HERE, exist_ok=True)
QML = os.path.join(ROOT, "plugin", "AIAssistant", "qml")
# the plugin package without its __init__ (that one needs the slicer), and a stub Logger
import types
_pkg = types.ModuleType("AIAssistant"); _pkg.__path__ = [os.path.join(ROOT, "plugin", "AIAssistant")]
sys.modules["AIAssistant"] = _pkg
if "UM" not in sys.modules:
    class _Logger:
        log = staticmethod(lambda *a, **k: None)
        logException = staticmethod(lambda *a, **k: None)
    for name, attrs in (("UM", {}), ("UM.Logger", {"Logger": _Logger})):
        m = types.ModuleType(name); m.__dict__.update(attrs); sys.modules[name] = m
from PyQt5.QtCore import QTimer, QUrl, QMetaObject, Qt, QPointF
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtQml import QQmlEngine, QQmlComponent, QQmlContext
from PyQt5.QtTest import QTest
from PyQt5.QtQuick import QQuickWindow, QQuickItem
qapp = QGuiApplication(sys.argv)
from AIAssistant.agent import memory
from AIAssistant import memory_ui
LIM = {"type": "float", "minimum_value": 0, "maximum_value": 999}
memory.limits_provider = lambda k, c: LIM
memory_ui._labels.update({"top_bottom_thickness": "Толщина верха и низа", "layer_height": "Высота слоя 5D",
    "speed_print": "Скорость печати", "material_print_temperature": "Температура сопла",
    "wall_line_count": "Количество линий стенки", "infill_sparse_density": "Плотность заполнения"})
memory_ui.UNITS.update({"top_bottom_thickness": "мм", "layer_height": "мм", "speed_print": "мм/с",
    "material_print_temperature": "°C", "infill_sparse_density": "%"})
mem = memory.get()
T = [time.mktime((2026, 10, d, h, m, 0, 0, 0, -1)) for d, h, m in
     ((5, 13, 28), (10, 8, 40), (14, 23, 4), (18, 6, 16), (22, 3, 52), (24, 1, 28))]
clock = {"t": T[0]}
memory._now = lambda: clock["t"]
GEAR = {"part": {"name": "Шестерня z24", "mesh_hash": "aa11"}, "material": "fiberpart_pa12_g12", "mode": "classic"}
def at_t(i): clock["t"] = T[i]
mem.set_role("novice")
at_t(0); mem.record_edits(GEAR, {"settings.infill_sparse_density": {"proposed": 20, "chosen": 40}},
                          {"keys": ["settings.infill_sparse_density"], "scope": "part"})
mem.set_role("expert")
at_t(1); mem.record_edits(GEAR, {"settings.wall_line_count": {"proposed": 4, "chosen": 5}},
                          {"keys": ["settings.wall_line_count"], "scope": "material"})
mem.set_role("novice")
PETG = dict(GEAR, material="enduse_petg", part={"name": "Кронштейн", "mesh_hash": "bb22"})
for k in range(2):
    clock["t"] = T[2] - 600 + k * 300
    mem.record_edits(PETG, {"settings.material_print_temperature": {"proposed": 235, "chosen": 245}})
mem.set_role("expert")
at_t(3); mem.record_edits(GEAR, {"settings.speed_print": {"proposed": 60, "chosen": 45}},
                          {"keys": ["settings.speed_print"], "scope": "global"})
r = [x for x in mem.list_rules() if x["key"] == "speed_print"][0]
mem.disable_rule(r["id"])
at_t(4); mem.record_edits(dict(GEAR, mode="cylindrical"), {"settings.layer_height": {"proposed": 0.2, "chosen": 0.15}},
                          {"keys": ["settings.layer_height"], "scope": "mode"})
at_t(5); mem.record_edits(GEAR, {"settings.top_bottom_thickness": {"proposed": 0.8, "chosen": 1.2}},
                          {"keys": ["settings.top_bottom_thickness"], "scope": "material"})
clock["t"] = time.time()
memory._now = time.time
print("rules", len(mem.list_rules()), "events", len(mem.list_events()))

engine = QQmlEngine()
engine.warnings.connect(lambda ws: [print("QMLWARN", w.toString()) for w in ws])
bridge = memory_ui.MemoryBridge()
ctx = QQmlContext(engine.rootContext())
comp = QQmlComponent(engine)
comp.setData(b'import QtQuick 2.7\nMemoryWindow { memory: bridge }', QUrl.fromLocalFile(os.path.join(QML, "x.qml")))
ctx.setContextProperty("bridge", bridge)
win = comp.create(ctx)
def P(name):
    v = win.property(name)
    return v.toVariant() if hasattr(v, "toVariant") else v
for e in comp.errors(): print("QMLERR", e.toString())
QMetaObject.invokeMethod(win, "open")
win_obj = win
win = [w for w in qapp.topLevelWindows() if w.title() == "Память агента"][0]
print("window type", type(win).__name__)
OK = []
def check(name, cond): OK.append((name, bool(cond))); print(("ok  " if cond else "FAIL") + " " + name)
def shot(name): win.grabWindow().save(os.path.join(HERE, name)); print("shot", name)
def rows(): return json.loads(json.dumps(P("ruleRows").toVariant() if hasattr(P("ruleRows"), "toVariant") else P("ruleRows")))
def find(text):
    from PyQt5.QtQuick import QQuickItem
    out = []
    def walk(it):
        for c in it.childItems():
            if c.property("text") == text and c.isVisible(): out.append(c)
            walk(c)
    walk(win.contentItem()); return out
def click(item, dx=None, dy=None):
    p = item.mapToScene(QPointF(item.width() / 2 if dx is None else dx, item.height() / 2 if dy is None else dy))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, p.toPoint()); qapp.processEvents()
def icons(name):
    out = []
    def walk(it):
        for c in it.childItems():
            if c.property("name") == name and c.isVisible() and c.property("source") is not None: out.append(c)
            walk(c)
    walk(win.contentItem()); return sorted(out, key=lambda i: i.mapToScene(QPointF(0, 0)).y())
STEPS = []
def step(f): STEPS.append(f); return f
@step
def s0():
    rr = P("ruleRows")
    check("6 rules, newest first", len(rr) == 6 and rr[0]["key"] == "top_bottom_thickness")
    check("tab counts 6 / events", win.property("ruleTotal") == 6 and win.property("eventTotal") >= 8)
    shot("mw_rules.png")
@step
def s1():  # toggle: active -> off
    toggles = [i for i in icons("pencil")]
    check("6 pencils", len(toggles) == 6)
    # switch sits 66 px left of the pencil
    pen = toggles[0]; p = pen.mapToScene(QPointF(-66 + 16, 8))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, p.toPoint()); qapp.processEvents()
    r = [x for x in mem.list_rules() if x["key"] == "top_bottom_thickness"][0]
    check("switch turns a rule off", r["status"] == "disabled")
    p = icons("pencil")[0].mapToScene(QPointF(-66 + 16, 8))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, p.toPoint()); qapp.processEvents()
    r = [x for x in mem.list_rules() if x["key"] == "top_bottom_thickness"][0]
    check("switch turns it back on", r["status"] == "active")
@step
def s2():  # edit value
    click(icons("pencil")[0])
    check("editor open", win.property("editing") != "")
    shot("mw_edit.png")
    from PyQt5.QtQuick import QQuickItem
    def walk(it, out):
        for c in it.childItems():
            if c.objectName() == "" and c.metaObject().className().startswith("QQuickTextInput") and c.isVisible(): out.append(c)
            walk(c, out)
        return out
    inputs = walk(win.contentItem(), [])
    inputs[0].setProperty("text", "1,6")
    click(find("Сохранить")[0])
    r = [x for x in mem.list_rules() if x["key"] == "top_bottom_thickness"][0]
    check("edit saves 1.6", r["value"] == 1.6 and win.property("editing") == "")
@step
def s3():  # delete: ask, no, then yes
    n = len(mem.list_rules())
    click(icons("trash")[-1])
    check("delete asks first", len(find("Удалить?")) == 1 and len(mem.list_rules()) == n)
    shot("mw_delete.png")
    click(find("Нет")[0])
    check("Нет keeps it", len(find("Удалить?")) == 0 and len(mem.list_rules()) == n)
    click(icons("trash")[-1]); click(find("Да")[0])
    check("Да deletes", len(mem.list_rules()) == n - 1)
@step
def s4():  # sort by Когда asc then Настройка
    click(find("КОГДА")[0])
    rr = P("ruleRows")
    check("Когда ascending", rr[0]["updated_ts"] <= rr[-1]["updated_ts"])
    click(sorted(find("НАСТРОЙКА"), key=lambda i: i.mapToScene(QPointF(0, 0)).y())[-1])
    rr = P("ruleRows")
    print("   keys:", [x["key"] for x in rr], P("sortRules"))
    check("sort by setting name", [x["key_label"].lower() for x in rr] == sorted(x["key_label"].lower() for x in rr))
    click(find("КОГДА")[0])
@step
def s5():  # filters through setFilter (ComboBox popups are native menus)
    QMetaObject.invokeMethod(win, "setFilter", Qt.DirectConnection, *[__import__("PyQt5.QtCore").QtCore.Q_ARG("QVariant", "material"), __import__("PyQt5.QtCore").QtCore.Q_ARG("QVariant", "fiberpart_pa12_g12")])
    rr = P("ruleRows")
    check("material filter", len(rr) >= 1 and all(x["scope"] == "material" and x["scope_value"] == "fiberpart_pa12_g12" for x in rr))
    QMetaObject.invokeMethod(win, "clearFilters")
    QMetaObject.invokeMethod(win, "setFilter", Qt.DirectConnection, __import__("PyQt5.QtCore").QtCore.Q_ARG("QVariant", "status"), __import__("PyQt5.QtCore").QtCore.Q_ARG("QVariant", "candidate"))
    rr = P("ruleRows")
    check("status filter: candidate", len(rr) == 1 and rr[0]["status"] == "candidate")
    shot("mw_filter.png")
    QMetaObject.invokeMethod(win, "clearFilters")
    QMetaObject.invokeMethod(win, "setFilter", Qt.DirectConnection, __import__("PyQt5.QtCore").QtCore.Q_ARG("QVariant", "period"), __import__("PyQt5.QtCore").QtCore.Q_ARG("QVariant", "day"))
    rr = P("ruleRows")
    check("period: day keeps today's edits only", all(x["updated_ts"] > time.time() - 86400 for x in rr))
    QMetaObject.invokeMethod(win, "clearFilters")
@step
def s6():  # journal + rollback
    click(find("Журнал правок")[0])
    er = P("eventRows")
    check("journal rows", len(er) == win.property("eventTotal") and len(er) > 8)
    shot("mw_journal.png")
    n_before = len(mem.list_rules())
    # roll back to the first line of the journal (oldest): last row after sort desc
    click(find("КОГДА")[0])   # oldest first
    lines = sorted(find("Откатить сюда"), key=lambda i: i.mapToScene(QPointF(0, 0)).y())
    click(lines[0])
    check("rollback asks", P("ask") is not None)
    shot("mw_rollback_ask.png")
    click(find("Откатить")[0])
    check("rollback leaves the novice part rule only", len(mem.list_rules()) == 1)
    er = P("eventRows")
    rb = [e for e in er if e["action"] == "rollback"]
    check("rollback line in journal", len(rb) == 1 and "None" not in rb[0]["change"])
    print("   rollback line:", rb and rb[0]["change"])
    click(find("КОГДА")[0])
    shot("mw_after_rollback.png")
@step
def s7():  # role, close
    click(find("Новичок")[0])
    check("role novice", bridge.role == "novice")
    # the novice cannot delete a material rule: the reason is shown, the rule stays
    mem.set_role("expert"); mem.import_({"rules": [{"key": "wall_line_count", "value": 5, "scope": "material",
        "scope_value": "fiberpart_pa12_g12", "status": "active"}]}, merge=True); mem.set_role("novice")
    QMetaObject.invokeMethod(win_obj, "reload"); qapp.processEvents()
    click(find("Правила")[0]); n = len(mem.list_rules())
    trash = icons("trash"); click(trash[0]); click(find("Да")[0])
    print("   toast:", P("toast"))
    check("novice delete refused with a reason", len(mem.list_rules()) == n and P("toast") != "")
    shot("mw_novice.png")
    click(find("Эксперт")[0])
    check("role expert", bridge.role == "expert")
    click(find("Правила")[0])
    click(find("Закрыть")[0])
    check("Закрыть hides", not win.isVisible())
def run(i=0):
    if i < len(STEPS):
        try: STEPS[i]()
        except Exception as e:
            import traceback; traceback.print_exc(); OK.append((STEPS[i].__name__, False))
        QTimer.singleShot(400, lambda: run(i + 1))
    else:
        print("RESULT", sum(1 for _, o in OK if o), "/", len(OK)); qapp.exit(0 if all(o for _, o in OK) else 1)
QTimer.singleShot(600, run)
qapp.exec_()
