# The "База знаний" window clicked through on a copy of knowledge/: select, edit, save,
# validation, new, duplicate, delete, search, the rule files tab, unsaved-changes prompt,
# close. Needs PyQt5 with QtQuick and a display:
#   QT_QUICK_BACKEND=software python tests/ui_knowledge_window.py    (xvfb-run on Linux)
# Screenshots go to tests/ui_out/. Prints ok/FAIL per check and RESULT n / m.
import sys, os, json, shutil, tempfile, types
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.join(ROOT, "tests", "ui_out")
os.makedirs(HERE, exist_ok=True)
PLUGIN = os.path.join(ROOT, "plugin", "AIAssistant")
QML = os.path.join(PLUGIN, "qml")
_pkg = types.ModuleType("AIAssistant"); _pkg.__path__ = [PLUGIN]
sys.modules["AIAssistant"] = _pkg
from PyQt5.QtCore import QTimer, QUrl, QMetaObject, Qt, QPointF
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtQml import QQmlEngine, QQmlComponent, QQmlContext
from PyQt5.QtTest import QTest
from PyQt5.QtQuick import QQuickWindow  # noqa: F401  (topLevelWindows() then gives QQuickWindow)
qapp = QGuiApplication(sys.argv)
qapp.setQuitOnLastWindowClosed(False)   # the steps close and reopen the window
from AIAssistant import knowledge_ui
from AIAssistant.agent import materials

FOLDER = os.path.join(tempfile.mkdtemp(), "knowledge")
shutil.copytree(os.path.join(PLUGIN, "knowledge"), FOLDER)
CSV = os.path.join(FOLDER, "materials.csv")
materials.CSV_PATH = CSV
materials._cache["mtime"] = None

engine = QQmlEngine()
engine.warnings.connect(lambda ws: [print("QMLWARN", w.toString()) for w in ws])
bridge = knowledge_ui.KnowledgeBridge(folder=FOLDER)
ctx = QQmlContext(engine.rootContext())
ctx.setContextProperty("bridge", bridge)
comp = QQmlComponent(engine)
comp.setData(b'import QtQuick 2.7\nKnowledgeWindow { knowledge: bridge }', QUrl.fromLocalFile(os.path.join(QML, "x.qml")))
win_obj = comp.create(ctx)
for e in comp.errors(): print("QMLERR", e.toString())
QMetaObject.invokeMethod(win_obj, "open")
win = [w for w in qapp.topLevelWindows() if w.title() == "База знаний"][0]

def P(name):
    v = win_obj.property(name)
    return v.toVariant() if hasattr(v, "toVariant") else v
OK = []
def check(name, cond): OK.append((name, bool(cond))); print(("ok  " if cond else "FAIL") + " " + name)
def shot(name): win.grabWindow().save(os.path.join(HERE, name)); print("shot", name)
def walk(pred):
    out = []
    def go(it):
        for c in it.childItems():
            if c.isVisible():
                if pred(c): out.append(c)
                go(c)
    go(win.contentItem())
    return sorted(out, key=lambda i: (i.mapToScene(QPointF(0, 0)).y(), i.mapToScene(QPointF(0, 0)).x()))
def find(text): return walk(lambda c: c.property("text") == text)
def icons(name): return walk(lambda c: c.property("name") == name and c.property("source") is not None)
def field(key): return walk(lambda c: c.property("col") is not None and (c.property("col").toVariant() if hasattr(c.property("col"), "toVariant") else c.property("col") or {}).get("key") == key)[0]
def reveal(item):  # scroll the form so that the item is inside it (clicks outside the clip miss)
    f = item.parentItem()
    while f is not None and f.property("contentY") is None: f = f.parentItem()
    if f is None: return
    top = item.mapToItem(f.property("contentItem"), QPointF(0, 0)).y()
    if top < f.property("contentY") or top + item.height() > f.property("contentY") + f.height():
        f.setProperty("contentY", max(0, min(top - 20, f.property("contentHeight") - f.height())))
        qapp.processEvents()
def click(item, dx=None, dy=None):
    QTest.qWait(30)   # a frame: Row and Flow place items on polish, after the change that showed them
    reveal(item)
    p = item.mapToScene(QPointF(item.width() / 2 if dx is None else dx, item.height() / 2 if dy is None else dy))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, p.toPoint()); qapp.processEvents()
def inputs_in(item): return [c for c in walk(lambda c: c.property("cursorPosition") is not None and c.property("selectByMouse") is not None)
                             if item.isAncestorOf(c)]
def type_into(item, text, replace=True):
    inp = inputs_in(item)[0]
    click(inp)
    if replace:
        QTest.keyClick(win, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(win, Qt.Key_Backspace)
    keys(text)
def keys(text):  # QTest.keyClicks wants a QWidget; a QQuickWindow takes key events directly
    from PyQt5.QtGui import QKeyEvent
    from PyQt5.QtCore import QEvent
    for ch in text:
        for kind in (QEvent.KeyPress, QEvent.KeyRelease):
            qapp.sendEvent(win, QKeyEvent(kind, 0, Qt.NoModifier, ch))
    qapp.processEvents()
def row(mid): return [r for r in materials.load() if r["material_id"] == mid]
def raw(): return open(CSV, "rb").read()

STEPS = []
def step(f): STEPS.append(f); return f
@step
def s0():
    check("list has the table's materials", len(P("items")) == len(materials.load()) and len(P("items")) > 5)
    check("nothing selected yet", P("groups") == [] or len(P("groups")) == 0)
    shot("kw_empty.png")
@step
def s1():  # open a material with a click on its row
    click(find("Proto PLA")[0])
    check("form opened for proto_pla", P("selected") == "proto_pla" and len(P("groups")) >= 5)
    check("not dirty after opening", not P("formDirty"))
    shot("kw_form.png")
@step
def s2():  # edit a number with a comma, a scale and a switch, then save
    before = raw()
    type_into(field("heat_c"), "57,5")
    check("dirty after typing", P("formDirty"))
    seg = [t for t in find("2") if field("uv").isAncestorOf(t)]
    click(seg[0])
    sw = field("food_contact")
    click(sw, 16, sw.height() - 15)
    shot("kw_edited.png")
    click(find("Сохранить")[0])
    r = row("proto_pla")[0]
    check("saved heat 57.5, uv 2, food contact 1", r["heat_c"] == 57.5 and r["uv"] == 2 and r["food_contact"] in (1, True))
    check("not dirty after save", not P("formDirty"))
    check(".bak kept", open(CSV + ".bak", "rb").read() == before)
    check("BOM and ; kept", raw().startswith(b"\xef\xbb\xbfmaterial_id;name;"))
    print("   toast:", P("toast"))
@step
def s3():  # invalid values: refused, marked red, nothing written
    before = raw()
    type_into(field("density"), "плотный")
    type_into(field("material_id"), "Proto PLA!")
    click(find("Сохранить")[0])
    errs = P("errors")
    check("errors on density and code", set(errs) >= {"density", "material_id"})
    check("file untouched", raw() == before)
    check("error text shown", len(find(errs.get("density", "-"))) == 1)
    shot("kw_errors.png")
    type_into(field("density"), "1,25")
    check("typing clears that field's error", "density" not in P("errors"))
@step
def s4():  # leaving with unsaved edits asks; "Не сохранять" drops them
    click(find("Proto PVA")[0])
    check("switching material asks", P("ask") is not None and P("selected") == "proto_pla")
    shot("kw_ask.png")
    click(find("Не сохранять")[0])
    check("now on proto_pva", P("selected") == "proto_pva" and not P("formDirty"))
    check("proto_pla code unchanged in file", len(row("proto_pla")) == 1)
@step
def s5():  # new material
    click(find("+ Материал")[0])
    check("new form", P("isNew") and P("vals")["material_id"] == "new_material")
    type_into(field("name"), "Тестовый ABS")
    type_into(field("base"), "ABS")
    type_into(field("heat_c"), "85")
    click([t for t in find("жёсткий") if field("flex").isAncestorOf(t)][0])
    click(find("Сохранить")[0])
    r = row("new_material")
    check("new material in the table", r and r[0]["name"] == "Тестовый ABS" and r[0]["flex"] == "rigid")
    check("list shows it", any(i["material_id"] == "new_material" for i in P("items")))
@step
def s6():  # duplicate
    click(find("Дублировать")[0])
    check("copy form", P("isNew") and P("vals")["material_id"] == "new_material_copy")
    click(find("Сохранить")[0])
    check("copy saved", len(row("new_material_copy")) == 1)
@step
def s7():  # search, then delete with the bin and confirm
    type_into(walk(lambda c: c.property("text") == "Название, основа, применение")[0].parent(), "Тестовый", replace=False)
    check("search narrows to 2, the tab still counts all", len(P("items")) == 2 and P("total") == len(materials.load()))
    shot("kw_search.png")
    bins = icons("trash")
    click(bins[0]);
    check("asks before delete", P("confirmDelete") != "")
    shot("kw_delete_ask.png")
    gone = P("confirmDelete")
    click(find("Да")[0])
    check("deleted from file", len(row(gone)) == 0 and len(P("items")) == 1)
    click(icons("trash")[0]); click(find("Нет")[0])
    check("Нет keeps it", len(P("items")) == 1 and P("confirmDelete") == "")
@step
def s8():  # the rules tab: edit, revert, edit, save
    click(find("Правила")[0])
    check("rules tab with 3 files", P("tab") == "rules" and len(P("files")) == 3 and P("file") == "10_print_modes.md")
    shot("kw_rules.png")
    ta = [c for c in walk(lambda c: c.property("textFormat") is not None and c.property("wrapMode") is not None and c.property("cursorPosition") is not None)]
    ta = ta[0]
    path = os.path.join(FOLDER, "10_print_modes.md"); before = open(path, "rb").read()
    click(ta, 40, 20); QTest.keyClick(win, Qt.Key_End, Qt.ControlModifier); keys("- правка из теста")
    check("dirty", P("textDirty"))
    click(find("Отменить изменения")[0])
    check("revert", not P("textDirty") and open(path, "rb").read() == before)
    click(ta, 40, 20); QTest.keyClick(win, Qt.Key_End, Qt.ControlModifier); QTest.keyClick(win, Qt.Key_Return); keys("- правило из теста")
    shot("kw_rules_edit.png")
    click(find("Сохранить")[0])
    check("saved to file", "правило из теста" in open(path, encoding="utf-8-sig").read() and not P("textDirty"))
    check("rule .bak kept", open(path + ".bak", "rb").read() == before)
@step
def s9():  # unsaved text: switching file asks, "Отмена" stays
    ta = walk(lambda c: c.property("textFormat") is not None and c.property("cursorPosition") is not None)[0]
    click(ta, 40, 20); keys("x")
    click(find("20_settings.md")[0])
    check("switching file asks", P("ask") is not None and P("file") == "10_print_modes.md")
    click(find("Отмена")[0])
    check("Отмена stays with edits", P("ask") is None and P("textDirty"))
    win.close(); qapp.processEvents()
    check("close with edits asks", win.isVisible() and P("ask") is not None)
    click(find("Не сохранять")[0])
    check("then hides", not win.isVisible())
@step
def s10():  # reopen and close
    QMetaObject.invokeMethod(win_obj, "open"); qapp.processEvents()
    check("reopens", win.isVisible())
    click(find("Закрыть")[0])
    check("Закрыть hides", not win.isVisible())

def run(i=0):
    if i < len(STEPS):
        try: STEPS[i]()
        except Exception:
            import traceback; traceback.print_exc(); OK.append((STEPS[i].__name__, False))
        QTimer.singleShot(300, lambda: run(i + 1))
    else:
        print("RESULT", sum(1 for _, o in OK if o), "/", len(OK)); qapp.exit(0 if all(o for _, o in OK) else 1)
QTimer.singleShot(600, run)
qapp.exec_()
