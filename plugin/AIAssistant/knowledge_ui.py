# The bridge between the knowledge base (knowledge/materials.csv and knowledge/*.md)
# and QML: the "База знаний" window (qml/KnowledgeWindow.qml, opened from the panel).
# Same shape as memory_ui.MemoryBridge: every slot returns JSON, already worded for
# people, so the QML stays thin.
#
# The agent reads both by itself: agent/materials.py and agent/memory.py re-read
# materials.csv when its mtime changes, so a saved material counts from the next
# tool call; the .md files go into the system prompt, so a saved rule counts from
# the next conversation (the prompt of a running one stays as it was, for the cache).
import csv
import io
import json
import os
import re
import shutil

from PyQt5.QtCore import QObject, QUrl, pyqtSignal, pyqtSlot

KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge")
CSV_NAME = "materials.csv"

# column -> (label, kind, unit or options, group). kind: id | text | num | scale (0..2) | bool | choice.
# A column the table has and this list lacks is shown as text in "Прочее".
COLUMNS = [
    ("material_id", "Код материала", "id", None, "Основное"),
    ("name", "Название", "text", None, "Основное"),
    ("base", "Основа (полимер)", "text", None, "Основное"),
    ("density", "Плотность", "num", "г/см³", "Основное"),
    ("special", "Специальный (поддержки и т. п., не для деталей)", "bool", None, "Основное"),
    ("water_24h_pct", "Водопоглощение за 24 ч", "num", "%", "Стойкость"),
    ("heat_c", "Рабочая температура без размягчения", "num", "°C", "Стойкость"),
    ("tg_c", "Температура стеклования Tg", "num", "°C", "Стойкость"),
    ("uv", "Стойкость к УФ", "scale", None, "Стойкость"),
    ("ozone", "Стойкость к озону", "scale", None, "Стойкость"),
    ("oil_fuel", "Масла и топливо", "scale", None, "Стойкость"),
    ("alkali", "Щёлочи", "scale", None, "Стойкость"),
    ("acid", "Кислоты", "scale", None, "Стойкость"),
    ("alcohol", "Спирты", "scale", None, "Стойкость"),
    ("modulus_mpa", "Модуль упругости", "num", "МПа", "Механика"),
    ("elongation_pct", "Удлинение при разрыве", "num", "%", "Механика"),
    ("toughness", "Ударная вязкость", "scale", None, "Механика"),
    ("wear", "Износостойкость", "scale", None, "Механика"),
    ("flex", "Жёсткость", "choice", [["rigid", "жёсткий"], ["semi", "полужёсткий"], ["flexible", "гибкий"]], "Механика"),
    ("friction", "Трение", "choice", [["low", "низкое"], ["medium", "среднее"], ["high", "высокое"]], "Механика"),
    ("print_difficulty", "Сложность печати", "scale", None, "Печать"),
    ("conductive_filler", "Токопроводящий наполнитель", "bool", None, "Печать"),
    ("smooth_surface", "Гладкая поверхность", "bool", None, "Печать"),
    ("food_contact", "Допуск к пищевому контакту", "bool", None, "Печать"),
    ("good_for", "Для чего подходит", "text", None, "Примечания"),
    ("caveats", "Ограничения", "text", None, "Примечания"),
]
GROUPS = ("Основное", "Стойкость", "Механика", "Печать", "Примечания", "Прочее")
_BY_KEY = {c[0]: c for c in COLUMNS}
_ID = re.compile(r"^[A-Za-z0-9_]+$")   # the table has sealant_tpu_A95
_MD = re.compile(r"^[\w.-]+\.md$", re.UNICODE)


class KnowledgeError(Exception):
    pass


# ---------------------------------------------------------------- materials.csv

def _read_csv(path):
    """(header, rows, layout): rows are lists of the raw strings; layout keeps what a
    rewrite must reproduce (BOM, delimiter, line ending, trailing newline)."""
    with io.open(path, "rb") as f:
        raw = f.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in text else "\n"
    delimiter = ";" if text.count(";") >= text.count(",") else ","
    rows = [r for r in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)]
    header, body = (rows[0], rows[1:]) if rows else ([c[0] for c in COLUMNS], [])
    layout = {"bom": bom, "delimiter": delimiter, "newline": newline, "final": text.endswith(("\n", "\r"))}
    return header, body, layout


def _write_atomic(path, data):
    """tmp next to the file, the old one kept as <name>.bak, then os.replace."""
    tmp = path + ".tmp"
    with io.open(tmp, "wb") as f:
        f.write(data)
    try:
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise KnowledgeError("Не удалось записать %s: %s. Файл не открыт в Excel?" % (os.path.basename(path), e))


def _write_csv(path, header, rows, layout):
    out = io.StringIO(newline="")
    writer = csv.writer(out, delimiter=layout["delimiter"], lineterminator=layout["newline"])
    writer.writerow(header)
    writer.writerows(rows)
    text = out.getvalue()
    if not layout["final"]:
        text = text[:-len(layout["newline"])]
    _write_atomic(path, (b"\xef\xbb\xbf" if layout["bom"] else b"") + text.encode("utf-8"))
    _forget_caches()


def _forget_caches():
    # both readers cache by mtime; two saves within the clock's resolution would look unchanged
    try:
        from .agent import materials
        materials._cache["mtime"] = None
    except Exception:
        pass
    try:
        from .agent import memory
        memory._materials_cache["mtime"] = None
    except Exception:
        pass


def column(key):
    label, kind, extra, group = (_BY_KEY[key][1:] if key in _BY_KEY else (key, "text", None, "Прочее"))
    out = {"key": key, "label": label, "kind": kind, "group": group}
    if kind == "num":
        out["unit"] = extra or ""
    elif kind == "choice":
        out["options"] = [{"value": v, "label": l} for v, l in extra]
    return out


def _num(text):
    return float(text.replace(",", ".").replace(" ", ""))


def validate(fields, header, rows, original_id):
    """{column: message} for what cannot be saved; empty when it all fits."""
    errors = {}
    mid = (fields.get("material_id") or "").strip()
    if not mid:
        errors["material_id"] = "Нужен код материала"
    elif not _ID.match(mid):
        errors["material_id"] = "Только латинские буквы, цифры и _"
    else:
        index = header.index("material_id") if "material_id" in header else 0
        if any(r[index] == mid for r in rows if len(r) > index and r[index] != original_id):
            errors["material_id"] = "Такой код уже есть"
    if not (fields.get("name") or "").strip():
        errors["name"] = "Нужно название"
    for key in header:
        value = (fields.get(key) or "").strip()
        info = column(key)
        if not value or key in errors:
            continue
        if info["kind"] == "num":
            try:
                # temperatures go below zero (Tg of TPU is -30 °C); the rest cannot
                if _num(value) < 0 and info.get("unit") != "°C":
                    errors[key] = "Не меньше 0"
            except ValueError:
                errors[key] = "Нужно число"
        elif info["kind"] == "scale" and value not in ("0", "1", "2"):
            errors[key] = "0, 1 или 2"
        elif info["kind"] == "bool" and value not in ("0", "1"):
            errors[key] = "0 или 1"
        elif info["kind"] == "choice" and value not in [o["value"] for o in info["options"]]:
            errors[key] = "Одно из: " + ", ".join(o["label"] for o in info["options"])
    return errors


def _clean(key, value):
    value = " ".join((value or "").split())  # one line: a newline would break the table for Excel users
    if column(key)["kind"] == "num" and value:
        value = value.replace(",", ".").replace(" ", "")
    return value


class Materials:
    def __init__(self, path=None):
        self.path = path or os.path.join(KNOWLEDGE_DIR, CSV_NAME)

    def _load(self):
        return _read_csv(self.path)

    def list(self, query=""):
        header, rows, _ = self._load()
        q = (query or "").strip().lower()
        out = []
        for r in rows:
            row = dict(zip(header, r))
            if not row.get("material_id"):
                continue
            item = {k: row.get(k, "") for k in ("material_id", "name", "base", "good_for")}
            if q and not any(q in (v or "").lower() for v in item.values()):
                continue
            out.append(item)
        return out

    def get(self, material_id):
        header, rows, _ = self._load()
        row = next((dict(zip(header, r)) for r in rows if dict(zip(header, r)).get("material_id") == material_id), None)
        if row is None:
            raise KnowledgeError("Нет материала %s" % material_id)
        return {"values": {k: row.get(k, "") for k in header}, "columns": [column(k) for k in header]}

    def blank(self, copy_of=None):
        """A new row for the form, not saved yet: empty, or a copy with a new code."""
        header, rows, _ = self._load()
        values = {k: "" for k in header}
        if copy_of:
            values.update(self.get(copy_of)["values"])
            values["name"] = (values.get("name") or "") + " (копия)"
        ids = {dict(zip(header, r)).get("material_id") for r in rows}
        stem = (copy_of + "_copy") if copy_of else "new_material"
        mid, n = stem, 2
        while mid in ids:
            mid, n = "%s_%d" % (stem, n), n + 1
        values["material_id"] = mid
        return {"values": values, "columns": [column(k) for k in header]}

    def save(self, original_id, fields):
        """original_id "" adds a row; otherwise that row is replaced in place."""
        header, rows, layout = self._load()
        errors = validate(fields, header, rows, original_id)
        if errors:
            return {"ok": False, "errors": errors}
        new = [_clean(k, fields.get(k, "")) for k in header]
        index = header.index("material_id")
        for i, r in enumerate(rows):
            if original_id and len(r) > index and r[index] == original_id:
                rows[i] = new
                break
        else:
            if original_id:
                raise KnowledgeError("Нет материала %s" % original_id)
            rows.append(new)
        _write_csv(self.path, header, rows, layout)
        return {"ok": True, "errors": {}, "material_id": new[index]}

    def delete(self, material_id):
        header, rows, layout = self._load()
        index = header.index("material_id")
        kept = [r for r in rows if not (len(r) > index and r[index] == material_id)]
        if len(kept) == len(rows):
            raise KnowledgeError("Нет материала %s" % material_id)
        _write_csv(self.path, header, kept, layout)


# ---------------------------------------------------------------- knowledge/*.md

class RuleFiles:
    def __init__(self, folder=None):
        self.folder = folder or KNOWLEDGE_DIR

    def list(self):
        out = []
        for name in sorted(os.listdir(self.folder)):
            if not name.endswith(".md") or not _MD.match(name):
                continue
            title = name
            with io.open(os.path.join(self.folder, name), encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#"):
                        title = line.lstrip("#").strip() or name
                        break
            out.append({"name": name, "title": title})
        return out

    def _path(self, name):
        # only a file the list shows: no folders, no "..", no other extensions
        if not _MD.match(name or "") or name not in [f["name"] for f in self.list()]:
            raise KnowledgeError("Нет такого файла правил: %s" % name)
        return os.path.join(self.folder, name)

    def read(self, name):
        with io.open(self._path(name), "rb") as f:
            return f.read().decode("utf-8-sig").replace("\r\n", "\n")

    def save(self, name, text):
        path = self._path(name)
        with io.open(path, "rb") as f:
            old = f.read()
        text = (text or "").replace("\r\n", "\n")
        if b"\r\n" in old:
            text = text.replace("\n", "\r\n")
        _write_atomic(path, (b"\xef\xbb\xbf" if old.startswith(b"\xef\xbb\xbf") else b"") + text.encode("utf-8"))


# ---------------------------------------------------------------- QML

class KnowledgeBridge(QObject):
    windowRequested = pyqtSignal()   # the panel opens qml/KnowledgeWindow.qml

    def __init__(self, open_panel=None, folder=None, parent=None):
        super().__init__(parent)
        self._open_panel = open_panel
        self._folder = folder or KNOWLEDGE_DIR
        self._materials = Materials(os.path.join(self._folder, CSV_NAME))
        self._files = RuleFiles(self._folder)

    @pyqtSlot()
    def open(self):
        if self._open_panel is not None and not self._open_panel():
            return
        self.windowRequested.emit()

    @staticmethod
    def _reply(work):
        try:
            return json.dumps(work(), ensure_ascii=False)
        except (KnowledgeError, OSError, ValueError) as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    # ---- materials
    @pyqtSlot(str, result=str)
    def materials(self, query):
        return self._reply(lambda: self._materials.list(query))

    @pyqtSlot(str, result=str)
    def material(self, material_id):
        return self._reply(lambda: self._materials.get(material_id))

    @pyqtSlot(result=str)
    def newMaterial(self):
        return self._reply(lambda: self._materials.blank())

    @pyqtSlot(str, result=str)
    def duplicateMaterial(self, material_id):
        return self._reply(lambda: self._materials.blank(material_id))

    @pyqtSlot(str, str, result=str)
    def saveMaterial(self, original_id, fields_json):
        """{"ok", "errors": {column: message}, "material_id"} or {"error"}."""
        return self._reply(lambda: self._materials.save(original_id, json.loads(fields_json)))

    @pyqtSlot(str, result=str)
    def deleteMaterial(self, material_id):
        return self._reply(lambda: (self._materials.delete(material_id), {"ok": True})[1])

    # ---- rule files
    @pyqtSlot(result=str)
    def files(self):
        return self._reply(self._files.list)

    @pyqtSlot(str, result=str)
    def readFile(self, name):
        return self._reply(lambda: {"text": self._files.read(name)})

    @pyqtSlot(str, str, result=str)
    def saveFile(self, name, text):
        return self._reply(lambda: (self._files.save(name, text), {"ok": True})[1])

    @pyqtSlot()
    def openFolder(self):
        from PyQt5.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._folder))
