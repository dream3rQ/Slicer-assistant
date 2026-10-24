# The bridge between the agent's memory (agent/memory.py, the API in the v0.7.0
# plan) and QML: the "Запомнить" line of the plan card (qml/PlanCard.qml) and
# the "Память агента" window (qml/MemoryWindow.qml, opened from the panel).
# QML gets `manager.memory`; every call returns JSON with the rows already
# worded for people (dates, setting names, scopes), so the QML stays thin.
# Until agent/memory.py exists, agent/memory_mock.py stands in with demo data.
import json
import time

from PyQt5.QtCore import QObject, pyqtProperty, pyqtSignal, pyqtSlot

try:
    from .agent import memory as backend
    IS_MOCK = False
except ImportError:
    from .agent import memory_mock as backend
    IS_MOCK = True

MODE_LABELS = {"classic": "3D", "cylindrical": "5D Spiral", "cylindrical_full": "5D"}
STATUS_LABELS = {"candidate": "кандидат", "active": "действует", "disabled": "выключено", "rejected": "отклонено"}
ROLE_LABELS = {"novice": "Новичок", "expert": "Эксперт"}
ACTION_LABELS = {"edit": "правка", "rule_add": "новое правило", "rule_edit": "правило изменено",
                 "rule_off": "правило выключено", "rule_delete": "правило удалено", "rollback": "откат"}
# Names for keys the slicer may not know (and for tests without the slicer).
KEY_LABELS = {"mode": "Режим печати", "material_id": "Материал", "quality_type": "Профиль",
              "wall_line_count": "Количество линий стенки", "infill_sparse_density": "Плотность заполнения",
              "layer_height": "Высота слоя", "cylindrical_layer_height": "Высота слоя 5D",
              "material_print_temperature": "Температура сопла", "material_bed_temperature": "Температура стола",
              "speed_print": "Скорость печати", "top_bottom_thickness": "Толщина верха и низа",
              "support_enable": "Поддержки", "adhesion_type": "Тип прилипания"}
UNITS = {"infill_sparse_density": "%", "layer_height": "мм", "cylindrical_layer_height": "мм",
         "material_print_temperature": "°C", "material_bed_temperature": "°C", "speed_print": "мм/с",
         "top_bottom_thickness": "мм"}
PERIODS = {"day": 86400, "week": 7 * 86400, "month": 30 * 86400}


def _stack_property(key, prop):
    try:
        from UM.Application import Application
        stack = Application.getInstance().getGlobalContainerStack()
        return stack.getProperty(key, prop) if stack is not None else None
    except Exception:
        return None


_labels = {}


def key_label(key):
    if not key:
        return ""
    if key not in _labels:
        label = KEY_LABELS.get(key)
        if label is None:
            label = _stack_property(key, "label")
            try:
                from .agent.tools import _tr
                label = _tr("%s label" % key, label)
            except Exception:
                pass
        _labels[key] = label or key
    return _labels[key]


_materials = {}


def material_label(material_id):
    if not material_id:
        return ""
    if not _materials:
        try:
            from .agent import materials
            _materials.update({row["material_id"]: row.get("name") or row["material_id"] for row in materials.load()})
        except Exception:
            pass
    return _materials.get(material_id, material_id)


def value_text(key, value):
    if value is None or value == "":
        return "—"
    if key == "mode":
        return MODE_LABELS.get(value, str(value))
    if key == "material_id":
        return material_label(value)
    if value is True:
        return "да"
    if value is False:
        return "нет"
    if isinstance(value, float):
        text = ("%d" % value) if value == int(value) else ("%.3f" % value).rstrip("0")
    else:
        text = str(value)
    unit = UNITS.get(key) or _stack_property(key, "unit") or ""
    return text.replace(".", ",") + (" " + unit if unit and isinstance(value, (int, float)) else "")


def when(ts):
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts)) if ts else ""


def scope_text(rule):
    scope, value = rule.get("scope"), rule.get("scope_value")
    if scope == "part":
        return "деталь: " + (rule.get("part_name") or value or "?")
    if scope == "material":
        return "материал: " + material_label(value)
    if scope == "mode":
        return "режим: " + MODE_LABELS.get(value, value or "?")
    return "всегда"


def rule_row(rule):
    row = dict(rule)
    row.update({"key_label": key_label(rule.get("key")), "value_text": value_text(rule.get("key"), rule.get("value")),
                "scope_text": scope_text(rule), "status_text": STATUS_LABELS.get(rule.get("status"), rule.get("status")),
                "role_text": ROLE_LABELS.get(rule.get("role"), rule.get("role") or ""),
                "when": when(rule.get("updated_ts") or rule.get("created_ts")),
                "created": when(rule.get("created_ts")),
                "value_json": json.dumps(rule.get("value"), ensure_ascii=False)})
    return row


def event_row(event):
    row = dict(event)
    key, action = event.get("key"), event.get("action")
    part = event.get("part") if isinstance(event.get("part"), dict) else {}
    if action == "rollback":
        change = "правил было %s, стало %s" % (event.get("proposed"), event.get("chosen"))
    elif action == "rule_delete":
        change = value_text(key, event.get("proposed")) + " → удалено"
    elif action in ("rule_add", "rule_edit", "rule_off") and event.get("proposed") is None:
        change = value_text(key, event.get("chosen"))
    else:
        change = value_text(key, event.get("proposed")) + " → " + value_text(key, event.get("chosen"))
    row.update({"when": when(event.get("ts")), "part_text": part.get("name") or "",
                "material_text": material_label(event.get("material")),
                "mode_text": MODE_LABELS.get(event.get("mode"), event.get("mode") or ""),
                "key_label": key_label(key) if key else (event.get("reason") or ""),
                "change": change, "role_text": ROLE_LABELS.get(event.get("role"), event.get("role") or ""),
                "action_text": ACTION_LABELS.get(action, action or "")})
    row.pop("part", None)
    return row


def _filter(flt_json):
    flt = json.loads(flt_json or "{}")
    period = flt.pop("period", "")
    if period in PERIODS:
        flt["from_ts"] = time.time() - PERIODS[period]
    return {k: v for k, v in flt.items() if v not in (None, "")}


class MemoryBridge(QObject):
    changed = pyqtSignal()          # rules or the journal changed: the window reloads
    roleChanged = pyqtSignal()
    windowRequested = pyqtSignal()  # the panel opens qml/MemoryWindow.qml
    notice = pyqtSignal(str, bool, arguments=["text", "ok"])

    def __init__(self, open_panel=None, parent=None):
        super().__init__(parent)
        self._open_panel = open_panel  # shows the panel, the window lives in it

    @pyqtProperty(bool, constant=True)
    def isMock(self):
        return IS_MOCK

    @pyqtProperty(str, notify=roleChanged)
    def role(self):
        try:
            return backend.role()
        except Exception:
            return "novice"

    @pyqtSlot(str)
    def setRole(self, value):
        try:
            backend.set_role(value)
        except Exception as e:
            self.notice.emit(str(e), False)
        self.roleChanged.emit()

    @pyqtSlot()
    def open(self):
        if self._open_panel is not None and not self._open_panel():
            return
        self.windowRequested.emit()

    # ---- reading
    @pyqtSlot(str, str, result=str)
    def rules(self, flt_json, sort_json):
        rows = backend.list_rules(_filter(flt_json), json.loads(sort_json or "{}"))
        return json.dumps([rule_row(r) for r in rows], ensure_ascii=False)

    @pyqtSlot(str, str, result=str)
    def events(self, flt_json, sort_json):
        rows = backend.list_events(_filter(flt_json), json.loads(sort_json or "{}"))
        return json.dumps([event_row(e) for e in rows], ensure_ascii=False)

    @pyqtSlot(str, result=str)
    def rule(self, rule_id):
        for rule in backend.list_rules({}, {}):
            if rule.get("id") == rule_id:
                return json.dumps(rule_row(rule), ensure_ascii=False)
        return "null"

    @pyqtSlot(result=str)
    def facets(self):
        """Choices for the window's filters: what is in the memory now."""
        materials, modes, keys = set(), set(), set()
        for e in backend.list_events({}, {}):
            if e.get("material"):
                materials.add(e["material"])
            if e.get("mode"):
                modes.add(e["mode"])
            if e.get("key"):
                keys.add(e["key"])
        for r in backend.list_rules({}, {}):
            keys.add(r["key"])
            if r.get("scope") == "material" and r.get("scope_value"):
                materials.add(r["scope_value"])
            if r.get("scope") == "mode" and r.get("scope_value"):
                modes.add(r["scope_value"])
        out = {"materials": sorted(({"value": m, "label": material_label(m)} for m in materials), key=lambda o: o["label"]),
               "modes": [{"value": m, "label": MODE_LABELS.get(m, m)} for m in ("classic", "cylindrical", "cylindrical_full")
                         if m in modes] + [{"value": m, "label": m} for m in sorted(modes - set(MODE_LABELS))],
               "keys": sorted(({"value": k, "label": key_label(k)} for k in keys), key=lambda o: o["label"].lower())}
        return json.dumps(out, ensure_ascii=False)

    @pyqtSlot(str, str, str, result=str)
    def validate(self, key, value_json, context_json):
        """The card asks before "Запомнить": {"ok", "reason"}."""
        try:
            ok, reason = backend.validate(key, json.loads(value_json), json.loads(context_json or "{}"))
        except Exception as e:
            ok, reason = False, str(e)
        return json.dumps({"ok": bool(ok), "reason": reason or ""}, ensure_ascii=False)

    # ---- changing (the window)
    @pyqtSlot(str, str, result=str)
    def updateRule(self, rule_id, fields_json):
        """"" when saved, otherwise why not (shown in red in the edit row)."""
        return self._change(lambda: backend.update_rule(rule_id, **json.loads(fields_json)))

    @pyqtSlot(str, bool, result=str)
    def setRuleEnabled(self, rule_id, enabled):
        if enabled:
            return self._change(lambda: backend.update_rule(rule_id, status="active"))
        return self._change(lambda: backend.disable_rule(rule_id))

    @pyqtSlot(str, result=str)
    def deleteRule(self, rule_id):
        return self._change(lambda: backend.delete_rule(rule_id))

    @pyqtSlot(float, result=str)
    def rollback(self, ts):
        error = self._change(lambda: backend.rollback(ts))
        if not error:
            self.notice.emit("Правила возвращены к состоянию на %s. Откат записан в журнал." % when(ts), True)
        return error

    def _change(self, action):
        try:
            action()
        except Exception as e:
            return str(e) or e.__class__.__name__
        self.changed.emit()
        return ""

    @pyqtSlot()
    def exportFile(self):
        from PyQt5.QtWidgets import QFileDialog
        name = "stereo_agent_memory_%s.json" % time.strftime("%Y-%m-%d")
        path, _ = QFileDialog.getSaveFileName(None, "Экспорт памяти агента", name, "Память агента (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(backend.export(), f, ensure_ascii=False, indent=1)
        except Exception as e:
            self.notice.emit("Не удалось сохранить: %s" % e, False)
            return
        self.notice.emit("Память сохранена в %s" % path, True)

    @pyqtSlot(bool)
    def importFile(self, merge):
        from PyQt5.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(None, "Импорт памяти агента", "", "Память агента (*.json);;Все файлы (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                count = backend.import_(json.load(f), merge=merge)
        except Exception as e:
            self.notice.emit("Не удалось импортировать: %s" % e, False)
            return
        self.changed.emit()
        self.notice.emit("Импортировано правил: %s" % count, True)

    # ---- the plan card
    def record_from_card(self, proposal, answer):
        """Only for the stand-in: the real agent/memory.py is called by the
        session after apply_plan (Session._confirm_plan) with the full context."""
        if not IS_MOCK or answer.get("action") != "apply":
            return
        items = {i["id"]: i for i in proposal.get("items") or []}
        overrides = answer.get("overrides") or {}

        def chosen(item_id):
            if item_id in overrides:
                return overrides[item_id]
            return (items.get(item_id) or {}).get("proposed")
        changed = {k: {"proposed": (items.get(k) or {}).get("proposed"), "chosen": v} for k, v in overrides.items()
                   if str(v) != str((items.get(k) or {}).get("proposed"))}
        if not changed:
            return
        context = {"part": _scene_part(), "material": chosen("material_id"), "mode": chosen("mode")}
        result = backend.record_edits(context, changed, answer.get("remember") or None)
        self.changed.emit()
        if result.get("rules"):
            self.notice.emit("Запомнено правил: %d" % len(result["rules"]), True)
        for bad in result.get("rejected") or []:
            self.notice.emit("%s не запомнено: %s" % (key_label(bad["key"]), bad["reason"]), False)


def _scene_part():
    """The first printable object on the build plate, for the stand-in's journal."""
    try:
        from UM.Application import Application
        from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator
        root = Application.getInstance().getController().getScene().getRoot()
        for node in DepthFirstIterator(root):
            if node.callDecoration("isSliceable") and node.getMeshData() is not None:
                return {"name": node.getName(), "mesh_hash": node.getName()}
    except Exception:
        pass
    return {"name": "деталь", "mesh_hash": None}
