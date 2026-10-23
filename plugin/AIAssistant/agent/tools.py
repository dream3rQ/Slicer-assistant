# Tools the LLM can call. Each tool is a plain function registered with @tool;
# mcp.py exposes the registry over JSON-RPC and to the LLM as `tools`.
#
# Coordinates shown to the LLM use printer axes: X left-right, Y front-back,
# Z up. Internally the slicer (Cura) uses Y as the vertical axis and Z pointing
# to the front, so helpers below convert between the two.
import contextlib
import json
import math
import os
import re
import time
from collections import OrderedDict

from .mainthread import run_on_main
from . import config
from . import materials

TOOLS = OrderedDict()


class ToolError(Exception):
    """An expected failure with a message the LLM can act on."""


def tool(name, description, properties=None, required=None, main_thread=True, for_llm=True):
    """for_llm=False keeps a tool on the MCP server and the HTTP API but out of the
    list sent to the LLM: every listed schema is resent on every step."""
    def decorator(fn):
        TOOLS[name] = {
            "for_llm": for_llm,
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
            "fn": fn,
            "main_thread": main_thread,
        }
        return fn
    return decorator


def call(name, arguments, context):
    entry = TOOLS.get(name)
    if entry is None:
        raise ToolError("Нет инструмента %s" % name)
    if entry["main_thread"]:
        return run_on_main(entry["fn"], context, **arguments)
    return entry["fn"](context, **arguments)


# ---------------------------------------------------------------- helpers

def _app():
    from UM.Application import Application
    return Application.getInstance()


def _global_stack():
    stack = _app().getGlobalContainerStack()
    if stack is None:
        raise ToolError("В слайсере не выбран принтер")
    return stack


def _extruder_stacks():
    stack = _global_stack()
    return OrderedDict(sorted(stack.extruders.items(), key=lambda kv: int(kv[0])))


def _extruder_stack(position):
    stacks = _extruder_stacks()
    key = str(position if position is not None else 0)
    if key not in stacks:
        raise ToolError("Нет экструдера %s. Доступны: %s" % (key, ", ".join(stacks.keys())))
    return stacks[key]


@contextlib.contextmanager
def _keep_user_changes():
    # Changing material or quality may pop the "Keep or discard changes?"
    # dialog. Answer it with "keep" for the duration of the change.
    prefs = _app().getPreferences()
    key = "steslicer/choice_on_profile_override"
    old = prefs.getValue(key)
    prefs.setValue(key, "always_keep")
    try:
        yield
    finally:
        prefs.setValue(key, old if old is not None else "always_ask")


def _jsonable(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


def _r(x, nd=2):
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return x


# ---- scene helpers

def _scene_root():
    return _app().getController().getScene().getRoot()


def _objects():
    from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator
    result = []
    for node in DepthFirstIterator(_scene_root()):
        if not node.callDecoration("isSliceable") and not node.callDecoration("isGroup"):
            continue
        parent = node.getParent()
        if parent is not None and parent.callDecoration("isGroup"):
            continue
        if node.getMeshData() is None and not node.callDecoration("isGroup"):
            continue
        result.append(node)
    return result


def _object_id(node):
    return "obj_%x" % id(node)


def _find_object(object_id):
    objs = _objects()
    if not objs:
        raise ToolError("Сцена пуста: сначала загрузите модель (load_model)")
    if object_id in (None, "", "auto"):
        if len(objs) == 1:
            return objs[0]
        raise ToolError("На сцене несколько объектов, укажите object_id: %s" % ", ".join(_object_id(n) for n in objs))
    for node in objs:
        if _object_id(node) == object_id or node.getName() == object_id:
            return node
    raise ToolError("Нет объекта %s. Есть: %s" % (object_id, ", ".join(_object_id(n) for n in objs)))


def _bbox_printer(node):
    box = node.getBoundingBox()
    if box is None:
        return None
    return {
        "size_mm": {"x": _r(box.width), "y": _r(box.depth), "z": _r(box.height)},
        "center_mm": {"x": _r(box.center.x), "y": _r(-box.center.z)},
        "bottom_z_mm": _r(box.bottom),
        "top_z_mm": _r(box.top),
    }


def _describe_object(node):
    info = {"object_id": _object_id(node), "name": node.getName()}
    info.update(_bbox_printer(node) or {})
    try:
        position = node.callDecoration("getActiveExtruderPosition")
        info["extruder"] = int(position) if position is not None else None
    except Exception:
        pass
    try:
        info["outside_build_area"] = bool(node.isOutsideBuildArea())
    except Exception:
        pass
    return info


def _object_extruders():
    positions = set()
    for node in _objects():
        position = node.callDecoration("getActiveExtruderPosition")
        if position is not None:
            positions.add(int(position))
    return sorted(positions)


def _default_extruder(extruder):
    """Explicit extruder, else the one the objects print with, else 0."""
    if extruder is not None:
        return int(extruder)
    used = _object_extruders()
    return used[0] if len(used) == 1 else 0


def _assign_extruder(node, position):
    stack = _extruder_stack(position)
    node.callDecoration("setActiveExtruder", stack.getId())


def _drop_to_plate(node):
    from UM.Math.Vector import Vector
    from UM.Operations.TranslateOperation import TranslateOperation
    box = node.getBoundingBox()
    if box is not None and abs(box.bottom) > 1e-3:
        TranslateOperation(node, Vector(0, -box.bottom, 0)).push()


# Unit vectors in Cura space for printer-space directions.
_PRINTER_DIR_TO_CURA = {
    "+x": (1, 0, 0), "-x": (-1, 0, 0),
    "+y": (0, 0, -1), "-y": (0, 0, 1),
    "+z": (0, 1, 0), "-z": (0, -1, 0),
}


def _rotation_bringing_up(cura_vec):
    """Quaternion that rotates the given Cura unit axis onto Cura +Y (printer up)."""
    from UM.Math.Quaternion import Quaternion
    from UM.Math.Vector import Vector
    x, y, z = cura_vec
    if y == 1:
        return None
    if y == -1:
        return Quaternion.fromAngleAxis(math.pi, Vector.Unit_X)
    if x == 1:
        return Quaternion.fromAngleAxis(math.pi / 2, Vector.Unit_Z)
    if x == -1:
        return Quaternion.fromAngleAxis(-math.pi / 2, Vector.Unit_Z)
    if z == 1:
        return Quaternion.fromAngleAxis(-math.pi / 2, Vector.Unit_X)
    return Quaternion.fromAngleAxis(math.pi / 2, Vector.Unit_X)


def _triangles(node):
    import numpy
    mesh = node.getMeshData()
    if mesh is None:
        return None
    transformed = mesh.getTransformed(node.getWorldTransformation())
    verts = transformed.getVertices()
    if transformed.hasIndices():
        tris = verts[transformed.getIndices()]
    else:
        tris = verts[: (len(verts) // 3) * 3].reshape(-1, 3, 3)
    return numpy.asarray(tris, dtype=numpy.float64)


# ---------------------------------------------------------------- overview

@tool("get_scene_info",
      "Состояние слайсера: принтер, режим, профиль и доступные профили качества, материалы, объекты, рабочая область.")
def get_scene_info(ctx):
    app = _app()
    gs = _global_stack()
    mm = app.getMachineManager()
    extruders = []
    for pos, ext in _extruder_stacks().items():
        extruders.append({
            "position": pos,
            "enabled": bool(ext.isEnabled) if hasattr(ext, "isEnabled") else True,
            "material_id": ext.material.getMetaDataEntry("base_file", ext.material.getId()),
            "material_name": ext.material.getName(),
            "nozzle": ext.variant.getName(),
        })
    errors = []
    try:
        checker = app.getMachineErrorChecker()
        errors = sorted(getattr(checker, "_error_keys", []) or [])
    except Exception:
        pass
    return {
        "printer": gs.getName(),
        "printer_definition": gs.definition.getId(),
        "print_mode": gs.getProperty("printing_mode", "value"),
        "quality_type": mm.activeQualityType,
        "quality_name": mm.activeQualityOrQualityChangesName,
        "extruders": extruders,
        "build_volume_mm": {
            "x": gs.getProperty("machine_width", "value"),
            "y": gs.getProperty("machine_depth", "value"),
            "z": gs.getProperty("machine_height", "value"),
        },
        "cylinder_base_diameter_mm": gs.getProperty("cylindrical_mode_base_diameter", "value"),
        "quality_profiles": [{"quality_type": p["quality_type"], "layer_height": p["layer_height"]}
                             for p in list_quality_profiles(ctx)["profiles"] if p["available"]],
        "objects": [_describe_object(n) for n in _objects()],
        "settings_with_errors": errors,
    }


# ---------------------------------------------------------------- print mode

PRINT_MODES_ALLOWED = ["classic", "cylindrical", "cylindrical_full"]


@tool("set_print_mode",
      "Переключить режим печати. classic = 3D (плоские слои); cylindrical = 5D Spiral (спираль вокруг "
      "поворотной оси); cylindrical_full = 5D (послойно вокруг оси, основание-цилиндр). "
      "В 5D деталь насаживается на цилиндрическое основание по вертикальной оси в центре стола.",
      {"mode": {"type": "string", "enum": PRINT_MODES_ALLOWED}}, ["mode"])
def set_print_mode(ctx, mode):
    gs = _global_stack()
    options = gs.getProperty("printing_mode", "options") or {}
    if mode not in PRINT_MODES_ALLOWED:
        raise ToolError("Разрешены только режимы %s" % ", ".join(PRINT_MODES_ALLOWED))
    if options and mode not in options:
        raise ToolError("Принтер не поддерживает режим %s. Есть: %s" % (mode, ", ".join(options.keys())))
    gs.userChanges.setProperty("printing_mode", "value", mode)
    return {
        "print_mode": gs.getProperty("printing_mode", "value"),
        "label": (options or {}).get(mode),
        "cylinder_base_diameter_mm": gs.getProperty("cylindrical_mode_base_diameter", "value"),
    }


# ---------------------------------------------------------------- quality

@tool("list_quality_profiles", "Список профилей качества (quality_type) для текущего принтера и материала, с высотой слоя.")
def list_quality_profiles(ctx):
    app = _app()
    gs = _global_stack()
    groups = app.getQualityManager().getQualityGroups(gs)
    out = []
    for quality_type, group in groups.items():
        layer_height = None
        try:
            node = group.node_for_global
            if node is not None:
                layer_height = node.getContainer().getProperty("layer_height", "value")
        except Exception:
            pass
        out.append({"quality_type": quality_type, "name": group.name,
                    "available": bool(group.is_available), "layer_height": layer_height})
    return {"active": app.getMachineManager().activeQualityType, "profiles": out}


@tool("set_quality_profile",
      "Выбрать профиль качества по quality_type из list_quality_profiles. Делай это ДО точечных настроек.",
      {"quality_type": {"type": "string"}}, ["quality_type"])
def set_quality_profile(ctx, quality_type):
    app = _app()
    groups = app.getQualityManager().getQualityGroups(_global_stack())
    if quality_type not in groups:
        raise ToolError("Нет профиля %s. Есть: %s" % (quality_type, ", ".join(groups.keys())))
    if not groups[quality_type].is_available:
        raise ToolError("Профиль %s недоступен для текущего материала" % quality_type)
    with _keep_user_changes():
        app.getMachineManager().setQualityGroupByQualityType(quality_type)
    return {"active": app.getMachineManager().activeQualityType,
            "layer_height": _global_stack().getProperty("layer_height", "value")}


# ---------------------------------------------------------------- materials

def _available_materials(position):
    app = _app()
    gs = _global_stack()
    ext = _extruder_stack(position)
    nodes = app.getMaterialManager().getAvailableMaterialsForMachineExtruder(gs, ext) or {}
    out = []
    for root_id, node in nodes.items():
        meta = node.getMetadata() if hasattr(node, "getMetadata") else node.metadata
        out.append({
            "material_id": root_id,
            "name": meta.get("name"),
            "brand": meta.get("brand"),
            "type": meta.get("material"),
            "color": meta.get("color_name"),
            "density": meta.get("properties", {}).get("density") if isinstance(meta.get("properties"), dict) else None,
            "description": " ".join((meta.get("description") or "").split()),
            "adhesion_info": " ".join((meta.get("adhesion_info") or "").split()),
        })
    out.sort(key=lambda m: (m.get("brand") != "Stereotech", m.get("brand") or "", m.get("name") or ""))
    return out


@tool("list_materials",
      "Материалы, доступные для экструдера, с кратким назначением от производителя.",
      {"extruder": {"type": "integer", "description": "Номер экструдера; по умолчанию тот, которым печатается объект"},
       "brand": {"type": "string", "description": "Фильтр по бренду, например Stereotech"}})
def list_materials(ctx, extruder=None, brand=None):
    extruder = _default_extruder(extruder)
    items = _available_materials(extruder)
    if brand:
        items = [m for m in items if (m.get("brand") or "").lower() == brand.lower()]
    active = _extruder_stack(extruder).material
    # Short form: the full manufacturer texts are ~10k characters and stay in the history for the whole run.
    short = [{"material_id": m["material_id"], "name": m["name"], "type": m["type"],
              "about": _first_sentence(m["description"], 160)} for m in items]
    return {"extruder": extruder, "active": active.getMetaDataEntry("base_file", active.getId()), "materials": short}


def _first_sentence(text, limit):
    text = (text or "").strip()
    end = text.find(". ")
    if 0 < end < limit:
        return text[:end + 1]
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


@tool("set_material",
      "Установить материал по material_id из list_materials. По умолчанию — на экструдер, которым печатается "
      "объект. Делай это ДО точечных настроек.",
      {"material_id": {"type": "string"},
       "extruder": {"type": "integer", "description": "Номер экструдера (0 — основной)"}},
      ["material_id"])
def set_material(ctx, material_id, extruder=None):
    extruder = _default_extruder(extruder)
    ids = [m["material_id"] for m in _available_materials(extruder)]
    if material_id not in ids:
        close = [i for i in ids if material_id.lower() in i.lower()]
        raise ToolError("Материал %s недоступен. Похожие: %s" % (material_id, ", ".join(close[:10]) or "нет"))
    mm = _app().getMachineManager()
    with _keep_user_changes():
        mm.setMaterialById(str(extruder), material_id)
    ext = _extruder_stack(extruder)
    result = {"extruder": extruder,
              "material_id": ext.material.getMetaDataEntry("base_file", ext.material.getId()),
              "material_name": ext.material.getName(),
              "quality_type": mm.activeQualityType}
    used = _object_extruders()
    if used and extruder not in used:
        result["warning"] = ("Объекты печатаются экструдером %s, а материал поставлен на %s. "
                             "Вызови set_object_extruder или поставь материал на нужный экструдер."
                             % (", ".join(str(u) for u in used), extruder))
    return result


@tool("set_object_extruder",
      "Назначить объекту экструдер (0 — основной). Материал печати берётся с этого экструдера.",
      {"object_id": {"type": "string"}, "extruder": {"type": "integer"}}, ["extruder"])
def set_object_extruder(ctx, extruder, object_id=None):
    node = _find_object(object_id)
    _assign_extruder(node, extruder)
    return _describe_object(node)


@tool("recommend_materials",
      "Ранжировать материалы по требованиям ТЗ (ключи — по таблице в базе знаний). «Желательное» — в desirable.",
      {"requirements": {"type": "object", "properties": materials.REQUIREMENT_SCHEMA},
       "desirable": {"type": "array", "items": {"type": "string"},
                     "description": "Ключи requirements, которые не обязательны"},
       "extruder": {"type": "integer"}},
      ["requirements"])
def recommend_materials(ctx, requirements, desirable=None, extruder=None):
    available = None
    try:
        available = set(m["material_id"] for m in _available_materials(_default_extruder(extruder)))
    except Exception:
        pass
    return materials.recommend(requirements or {}, desirable, available)


# ---------------------------------------------------------------- settings

def _setting_info(stack, key):
    info = OrderedDict()
    info["key"] = key
    for prop in ("label", "type", "unit", "value", "default_value", "minimum_value", "maximum_value",
                 "minimum_value_warning", "maximum_value_warning", "enabled", "settable_per_extruder"):
        try:
            info[prop] = _jsonable(stack.getProperty(key, prop))
        except Exception:
            pass
    options = stack.getProperty(key, "options")
    if options:
        info["options"] = _jsonable(options)
    desc = stack.getProperty(key, "description")
    if desc:
        info["description"] = " ".join(str(desc).split())[:300]
    return info


def _target_stacks(key, extruder):
    gs = _global_stack()
    if gs.getProperty(key, "type") is None:
        import difflib
        close = difflib.get_close_matches(str(key), list(gs.getAllKeys()), n=4, cutoff=0.6)
        raise ToolError("Нет настройки %s.%s Иначе найди ключ через search_settings." %
                        (key, (" Похожие: %s." % ", ".join(close)) if close else ""))
    if gs.getProperty(key, "settable_per_extruder"):
        return [_extruder_stack(_default_extruder(extruder))]
    return [gs]


def _coerce(stack, key, value):
    setting_type = stack.getProperty(key, "type")
    try:
        if setting_type == "float":
            return float(value)
        if setting_type in ("int", "extruder", "optional_extruder"):
            return int(float(value))
        if setting_type == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("true", "1", "yes", "да", "on")
            return bool(value)
        if setting_type == "enum":
            options = stack.getProperty(key, "options") or {}
            value = str(value)
            if options and value not in options:
                by_label = [k for k, v in options.items() if str(v).lower() == value.lower()]
                if by_label:
                    return by_label[0]
                raise ToolError("Для %s допустимо: %s" % (key, ", ".join(options.keys())))
            return value
    except (TypeError, ValueError):
        raise ToolError("Значение %r не подходит для %s (тип %s)" % (value, key, setting_type))
    return str(value) if not isinstance(value, str) else value


def _range_warning(stack, key, value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    lo = stack.getProperty(key, "minimum_value")
    hi = stack.getProperty(key, "maximum_value")
    wlo = stack.getProperty(key, "minimum_value_warning")
    whi = stack.getProperty(key, "maximum_value_warning")
    try:
        if lo is not None and value < float(lo):
            return "ошибка: меньше минимума %s" % lo
        if hi is not None and value > float(hi):
            return "ошибка: больше максимума %s" % hi
        if wlo is not None and value < float(wlo):
            return "предупреждение: ниже рекомендуемого %s" % wlo
        if whi is not None and value > float(whi):
            return "предупреждение: выше рекомендуемого %s" % whi
    except (TypeError, ValueError):
        return None
    return None


@tool("search_settings",
      "Поиск настроек слайсера по ключу или названию (англ. или рус.). Возвращает точные ключи, текущие значения, "
      "единицы и допустимые варианты. Используй перед get/set, если не уверен в ключе.",
      {"query": {"type": "string", "description": "Например: infill, wall, support, temperature, adhesion"},
       "limit": {"type": "integer"}},
      ["query"])
def search_settings(ctx, query, limit=25):
    gs = _global_stack()
    # Several words = OR search, ranked by how many words matched.
    tokens = [t for t in re.split(r"[\s,;|/]+", query.lower()) if t]
    if not tokens:
        raise ToolError("Пустой запрос")
    hits = []
    for key in sorted(gs.getAllKeys()):
        label = str(gs.getProperty(key, "label") or "")
        if gs.getProperty(key, "type") == "category":
            continue
        haystack = key.lower() + " " + label.lower()
        score = sum(1 for t in tokens if t in haystack)
        if score:
            hits.append({"score": score, "key": key, "label": label,
                         "value": _jsonable(gs.getProperty(key, "value")),
                         "unit": gs.getProperty(key, "unit"),
                         "enabled": _jsonable(gs.getProperty(key, "enabled")),
                         "per_extruder": bool(gs.getProperty(key, "settable_per_extruder"))})
    hits.sort(key=lambda h: (-h["score"], not h["enabled"], len(h["key"])))
    for h in hits:
        h.pop("score")
    return {"count": len(hits), "results": hits[: max(1, min(int(limit), 80))]}


@tool("get_setting",
      "Подробно о настройке: значение, единицы, границы, варианты, описание.",
      {"key": {"type": "string"},
       "extruder": {"type": "integer", "description": "Для настроек экструдера; по умолчанию 0"}},
      ["key"])
def get_setting(ctx, key, extruder=None):
    stack = _target_stacks(key, extruder)[0]
    return _setting_info(stack, key)


@tool("set_settings",
      "Изменить одну или несколько настроек. Глобальные и экструдерные настройки определяются автоматически. "
      "Возвращает фактические значения и предупреждения о выходе за допустимые границы.",
      {"changes": {"type": "array", "items": {"type": "object", "properties": {
          "key": {"type": "string"},
          "value": {"description": "Число, строка или true/false"},
          "extruder": {"type": "integer"}},
          "required": ["key", "value"]}},
       "reason": {"type": "string", "description": "Коротко: зачем (покажем пользователю)"}},
      ["changes"])
def set_settings(ctx, changes, reason=None):
    results = []
    for change in changes:
        key = change.get("key")
        try:
            stacks = _target_stacks(key, change.get("extruder"))
            for stack in stacks:
                value = _coerce(stack, key, change.get("value"))
                stack.userChanges.setProperty(key, "value", value)
                actual = stack.getProperty(key, "value")
                item = {"key": key, "label": stack.getProperty(key, "label"),
                        "value": _jsonable(actual), "unit": stack.getProperty(key, "unit")}
                warning = _range_warning(stack, key, actual)
                if warning:
                    item["warning"] = warning
                if not stack.getProperty(key, "enabled"):
                    item["warning"] = (item.get("warning", "") + "; " if item.get("warning") else "") + \
                        "настройка неактивна при текущих параметрах и не повлияет на печать"
                results.append(item)
        except ToolError as e:
            results.append({"key": key, "error": str(e)})
    return {"results": results}


@tool("reset_settings",
      "Сбросить пользовательские изменения настроек (все или указанные ключи) к значениям профиля.",
      {"keys": {"type": "array", "items": {"type": "string"}}})
def reset_settings(ctx, keys=None):
    stacks = [_global_stack()] + list(_extruder_stacks().values())
    removed = 0
    for stack in stacks:
        if keys:
            for key in keys:
                if stack.userChanges.hasProperty(key, "value"):
                    stack.userChanges.removeInstance(key)
                    removed += 1
        else:
            removed += len(stack.userChanges.getAllKeys())
            stack.userChanges.clear()
    return {"reset": removed}


# ---------------------------------------------------------------- models

def _resolve_file(ctx, file):
    if ctx is not None and file in getattr(ctx, "uploads", {}):
        return ctx.uploads[file]["path"]
    if ctx is not None:
        for item in getattr(ctx, "uploads", {}).values():
            if item["name"] == file:
                return item["path"]
    if os.path.isfile(file):
        return file
    raise ToolError("Файл %s не найден. Используй file_id из вложений пользователя." % file)


@tool("load_model",
      "Загрузить 3D-модель (STL/OBJ/3MF) на стол. file — file_id вложения пользователя или путь к файлу.",
      {"file": {"type": "string"}}, ["file"], main_thread=False)
def load_model(ctx, file):
    from PyQt5.QtCore import QUrl
    path = _resolve_file(ctx, file)
    before = set(_object_id(n) for n in run_on_main(_objects))
    run_on_main(lambda: _app().readLocalFile(QUrl.fromLocalFile(path), True))
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(0.4)
        new = run_on_main(lambda: [_describe_object(n) for n in _objects() if _object_id(n) not in before])
        if new:
            time.sleep(0.6)  # let platform physics settle the model

            def finish():
                # New objects inherit whatever extruder tab was active in the UI;
                # print with the main extruder unless the agent decides otherwise.
                nodes = [n for n in _objects() if _object_id(n) not in before]
                for n in nodes:
                    _assign_extruder(n, 0)
                return [_describe_object(n) for n in nodes]
            new = run_on_main(finish)
            return {"loaded": new,
                    "note": "Размеры в мм в осях принтера: x — влево-вправо, y — вперёд-назад, z — вверх. "
                            "Объект назначен на основной экструдер 0."}
    raise ToolError("Модель не появилась на сцене за 120 с")


@tool("list_objects", "Список объектов на столе с габаритами и положением (мм, оси принтера, z вверх).")
def list_objects(ctx):
    return {"objects": [_describe_object(n) for n in _objects()]}


@tool("analyze_model",
      "Геометрический анализ объекта: габариты, вероятная ось вращения (для 5D), площадь опоры и доля нависаний "
      "для 6 вариантов укладки. Используй для выбора ориентации и режима.",
      {"object_id": {"type": "string"}})
def analyze_model(ctx, object_id=None):
    import numpy
    node = _find_object(object_id)
    tris = _triangles(node)
    if tris is None or len(tris) == 0:
        raise ToolError("У объекта нет геометрии")
    # Convert Cura (x, y-up, z-front) to printer (x, y-back, z-up).
    pts = numpy.stack([tris[..., 0], -tris[..., 2], tris[..., 1]], axis=-1)
    flat = pts.reshape(-1, 3)
    mn, mx = flat.min(axis=0), flat.max(axis=0)
    size = mx - mn
    cross = numpy.cross(pts[:, 1] - pts[:, 0], pts[:, 2] - pts[:, 0])
    area2 = numpy.linalg.norm(cross, axis=1)
    keep = area2 > 1e-12
    normals = numpy.zeros_like(cross)
    normals[keep] = cross[keep] / area2[keep][:, None]
    areas = area2 / 2.0
    total_area = float(areas.sum())

    # Rotation axis guess: the axis whose two perpendicular extents match and
    # whose vertices sit at a near-constant radius band.
    axes = ["x", "y", "z"]
    symmetry = []
    for i, name in enumerate(axes):
        a, b = [j for j in range(3) if j != i]
        if size[a] <= 0 or size[b] <= 0:
            continue
        ratio = min(size[a], size[b]) / max(size[a], size[b])
        symmetry.append((ratio, name))
    symmetry.sort(reverse=True)
    rotation_axis = symmetry[0][1] if symmetry and symmetry[0][0] > 0.97 else None

    options = []
    for direction, (axis_index, sign) in (("-z", (2, -1)), ("+z", (2, 1)), ("-x", (0, -1)),
                                          ("+x", (0, 1)), ("-y", (1, -1)), ("+y", (1, 1))):
        down = numpy.zeros(3)
        down[axis_index] = sign
        cos_down = normals.dot(down)
        coord = pts[..., axis_index].mean(axis=1) * sign
        extreme = flat[:, axis_index].max() * sign if sign > 0 else -flat[:, axis_index].min()
        on_plate = (cos_down > 0.98) & (numpy.abs(coord - extreme) < 0.3)
        overhang = (cos_down > math.cos(math.radians(45))) & ~on_plate
        rest = [k for k in range(3) if k != axis_index]
        options.append({
            "face_down": direction,
            "height_mm": _r(size[axis_index]),
            "footprint_mm": [_r(size[rest[0]]), _r(size[rest[1]])],
            "contact_area_mm2": _r(areas[on_plate].sum(), 1),
            "overhang_area_mm2": _r(areas[overhang].sum(), 1),
            "overhang_share_pct": _r(100.0 * areas[overhang].sum() / max(total_area, 1e-9), 1),
        })
    options.sort(key=lambda o: (o["overhang_area_mm2"] - 2 * o["contact_area_mm2"]))
    return {
        "object_id": _object_id(node),
        "size_mm": {"x": _r(size[0]), "y": _r(size[1]), "z": _r(size[2])},
        "surface_area_mm2": _r(total_area, 1),
        "triangles": int(len(tris)),
        "rotation_axis_guess": rotation_axis,
        "rotation_axis_note": ("Для 5D ось вращения детали должна быть вертикальной (z). "
                               "Если ось x или y — сначала orient_object(axis_up=...)."),
        "orientations_best_first": options,
    }


@tool("orient_object",
      "Повернуть объект: либо положить гранью вниз (face_down: '-z','+z','-x','+x','-y','+y' — какая сторона "
      "СЕЙЧАС станет низом), либо поставить ось вертикально (axis_up: 'x','y','z'). После поворота объект "
      "опускается на стол.",
      {"object_id": {"type": "string"},
       "face_down": {"type": "string", "enum": ["-z", "+z", "-x", "+x", "-y", "+y"]},
       "axis_up": {"type": "string", "enum": ["x", "y", "z"]}})
def orient_object(ctx, object_id=None, face_down=None, axis_up=None):
    from UM.Operations.RotateOperation import RotateOperation
    node = _find_object(object_id)
    if axis_up:
        up_cura = _PRINTER_DIR_TO_CURA["+" + axis_up]
    elif face_down:
        d = _PRINTER_DIR_TO_CURA[face_down]
        up_cura = (-d[0], -d[1], -d[2])
    else:
        raise ToolError("Укажи face_down или axis_up")
    rotation = _rotation_bringing_up(up_cura)
    if rotation is not None:
        RotateOperation(node, rotation, rotate_around_point=node.getWorldPosition()).push()
    _drop_to_plate(node)
    return _describe_object(node)


@tool("rotate_object",
      "Повернуть объект на заданные углы (градусы) вокруг осей принтера x, y, z (z — вертикаль).",
      {"object_id": {"type": "string"},
       "x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}})
def rotate_object(ctx, object_id=None, x=0, y=0, z=0):
    from UM.Math.Quaternion import Quaternion
    from UM.Math.Vector import Vector
    from UM.Operations.RotateOperation import RotateOperation
    node = _find_object(object_id)
    for angle, axis in ((x, Vector.Unit_X), (y, Vector.Unit_Z), (z, Vector.Unit_Y)):
        if angle:
            q = Quaternion.fromAngleAxis(math.radians(float(angle)), axis)
            RotateOperation(node, q, rotate_around_point=node.getWorldPosition()).push()
    _drop_to_plate(node)
    return _describe_object(node)


@tool("move_object",
      "Переместить центр объекта в точку (x, y) стола в мм; (0, 0) — центр стола и ось 5D.",
      {"object_id": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"}})
def move_object(ctx, object_id=None, x=0, y=0):
    from UM.Math.Vector import Vector
    from UM.Operations.TranslateOperation import TranslateOperation
    node = _find_object(object_id)
    box = node.getBoundingBox()
    target_cura_z = -float(y)
    TranslateOperation(node, Vector(float(x) - box.center.x, 0, target_cura_z - box.center.z)).push()
    _drop_to_plate(node)
    return _describe_object(node)


@tool("scale_object",
      "Масштабировать объект равномерно (factor, 1.0 = без изменений). Не масштабируй детали из ТЗ без явной просьбы.",
      {"object_id": {"type": "string"}, "factor": {"type": "number"}}, ["factor"])
def scale_object(ctx, factor, object_id=None):
    from UM.Math.Vector import Vector
    from UM.Operations.ScaleOperation import ScaleOperation
    node = _find_object(object_id)
    f = float(factor)
    if not 0.01 <= f <= 100:
        raise ToolError("Недопустимый масштаб %s" % factor)
    ScaleOperation(node, Vector(f, f, f), scale_around_point=node.getWorldPosition()).push()
    _drop_to_plate(node)
    return _describe_object(node)


@tool("arrange_all", "Автоматически расставить все объекты на столе.")
def arrange_all(ctx):
    _app().arrangeAll()
    return {"status": "расстановка запущена; проверь list_objects через пару секунд"}


@tool("delete_object", "Удалить объект со сцены.", {"object_id": {"type": "string"}}, ["object_id"])
def delete_object(ctx, object_id):
    from UM.Operations.RemoveSceneNodeOperation import RemoveSceneNodeOperation
    node = _find_object(object_id)
    RemoveSceneNodeOperation(node).push()
    return {"deleted": object_id}


@tool("clear_scene", "Удалить все объекты со стола.")
def clear_scene(ctx):
    _app().deleteAll()
    return {"objects": 0}


# ---------------------------------------------------------------- slicing

def _print_information():
    pi = _app().getPrintInformation()
    info = {}
    try:
        seconds = int(pi.currentPrintTime)
        info["print_time_s"] = seconds
        info["print_time"] = "%d ч %02d мин" % (seconds // 3600, (seconds % 3600) // 60)
    except Exception:
        info["print_time"] = str(pi.currentPrintTime)
    try:
        weights = [_r(w, 1) for w in pi.materialWeights]
        lengths = [_r(l, 2) for l in pi.materialLengths]
        names = list(pi.materialNames)
        info["material_weight_g"] = weights
        info["material_length_m"] = lengths
        info["material_names"] = names
        info["by_extruder"] = [
            {"extruder": i, "material": names[i] if i < len(names) else None, "weight_g": w,
             "length_m": lengths[i] if i < len(lengths) else None}
            for i, w in enumerate(weights) if w]
        info["total_weight_g"] = _r(sum(w for w in weights if isinstance(w, (int, float))), 1)
    except Exception:
        pass
    return info


@tool("slice",
      "Запустить нарезку и дождаться результата. Возвращает время печати и расход материала или причину ошибки.",
      {"timeout_s": {"type": "integer", "description": "По умолчанию 900"}},
      main_thread=False)
def slice_model(ctx, timeout_s=900):
    from UM.Backend.Backend import BackendState
    state = {"value": None, "progress": 0.0, "error": None}

    def on_state(new_state):
        state["value"] = new_state

    def on_progress(amount):
        state["progress"] = float(amount)

    def on_error(job=None):
        msg = None
        try:
            msg = job.getMessage() if job is not None else None
        except Exception:
            pass
        state["error"] = msg or "backend error"

    def start():
        if not _objects():
            raise ToolError("На столе нет объектов")
        backend = _app().getBackend()
        backend.backendStateChange.connect(on_state)
        backend.processingProgress.connect(on_progress)
        if hasattr(backend, "backendError"):
            backend.backendError.connect(on_error)
        if hasattr(backend, "forceSlice"):
            backend.forceSlice()
        else:
            backend.slice()
        return backend

    def stop_listening(backend):
        for signal, cb in ((backend.backendStateChange, on_state), (backend.processingProgress, on_progress)):
            try:
                signal.disconnect(cb)
            except Exception:
                pass
        if hasattr(backend, "backendError"):
            try:
                backend.backendError.disconnect(on_error)
            except Exception:
                pass

    backend = run_on_main(start)
    started = time.time()
    last_reported = -1.0
    try:
        while time.time() - started < timeout_s:
            time.sleep(0.3)
            if ctx is not None and state["progress"] - last_reported >= 0.05:
                last_reported = state["progress"]
                ctx.emit("progress", {"stage": "slicing", "value": round(state["progress"], 2)})
            if state["value"] == BackendState.Done:
                time.sleep(0.8)  # print time estimates arrive right after Done
                info = run_on_main(_print_information)
                info["status"] = "done"
                info["seconds"] = round(time.time() - started, 1)
                if ctx is not None:
                    ctx.emit("progress", {"stage": "slicing", "value": 1.0, "result": info})
                return info
            if state["value"] in (BackendState.Error, BackendState.Disabled) or state["error"]:
                errors = run_on_main(lambda: sorted(getattr(_app().getMachineErrorChecker(), "_error_keys", []) or []))
                raise ToolError("Нарезка не удалась (%s). Настройки с ошибками: %s. Проверь, что модель "
                                "внутри рабочей области, в 5D — насажена на ось (центр стола, ось вертикально)."
                                % (state["error"] or "Unable to slice", ", ".join(errors) or "нет"))
        raise ToolError("Нарезка не завершилась за %s с" % timeout_s)
    finally:
        run_on_main(stop_listening, backend)


@tool("get_slice_result", "Время печати и расход материала по последней нарезке.")
def get_slice_result(ctx):
    return _print_information()


# ---------------------------------------------------------------- export

def _results_dir(name):
    safe = "".join(c for c in (name or "result") if c not in '<>:"/\\|?*').strip() or "result"
    path = os.path.join(config.results_root(), safe)
    os.makedirs(path, exist_ok=True)
    return path


def _write_gcode(path):
    # Like the project writer, GCodeWriter renders its preview thumbnail via
    # @call_on_qt_thread, so it must be called from a worker thread (this is
    # also how the slicer's own WriteFileJob calls writers).
    import threading
    from UM.Mesh.MeshWriter import MeshWriter
    if threading.current_thread() is threading.main_thread():
        raise ToolError("internal: G-code writer called on the main thread")
    writer = _app().getMeshFileHandler().getWriter("GCodeWriter")
    if writer is None:
        raise ToolError("Плагин GCodeWriter не найден")
    with open(path, "w", encoding="utf-8", newline="") as stream:
        ok = writer.write(stream, [_scene_root()], MeshWriter.OutputMode.TextMode)
    if not ok:
        raise ToolError("G-code не записан: %s. Сначала выполни slice." % (writer.getInformation() or "нет данных"))
    return os.path.getsize(path)


def _write_project(path):
    # Must NOT run on the main thread: ThreeMFWorkspaceWriter.write is wrapped
    # in @call_on_qt_thread, which posts to the main loop and blocks until it
    # runs — called from the main thread that deadlocks the whole slicer.
    import threading
    from UM.Mesh.MeshWriter import MeshWriter
    if threading.current_thread() is threading.main_thread():
        raise ToolError("internal: project writer called on the main thread")
    writer = _app().getWorkspaceFileHandler().getWriter("3MFWriter")
    if writer is None:
        raise ToolError("Плагин 3MFWriter не найден")
    with open(path, "wb") as stream:
        ok = writer.write(stream, [_scene_root()], MeshWriter.OutputMode.BinaryMode)
    if ok is False:
        raise ToolError("Проект 3MF не записан")
    return os.path.getsize(path)


@tool("export_gcode", "Сохранить G-code последней нарезки в файл.",
      {"folder_name": {"type": "string", "description": "Папка результатов (название детали)"}},
      main_thread=False)
def export_gcode(ctx, folder_name=None):
    path = os.path.join(_results_dir(folder_name), "result.gcode")
    return {"path": path, "bytes": _write_gcode(path)}


@tool("save_project_3mf", "Сохранить проект (модель + все настройки) в 3MF.",
      {"folder_name": {"type": "string", "description": "Папка результатов (название детали)"}},
      main_thread=False)
def save_project_3mf(ctx, folder_name=None):
    path = os.path.join(_results_dir(folder_name), "project.3mf")
    return {"path": path, "bytes": _write_project(path)}


SUMMARY_KEYS = ("layer_height", "cylindrical_layer_height", "wall_line_count", "top_bottom_thickness",
                "infill_sparse_density", "infill_pattern", "support_enable", "support_type", "support_tree_enable",
                "support_enable_cylindrical", "adhesion_type", "cylindrical_raft_type", "speed_print",
                "cool_fan_speed", "xy_offset_layer_0", "material_print_temperature", "material_bed_temperature")


def _settings_summary():
    """Effective values of the settings the reference answers are checked against
    (see references/check.py), taken from each extruder that prints an object."""
    gs = _global_stack()
    mm = _app().getMachineManager()
    used = _object_extruders() or [0]
    extruders = []
    for pos in used:
        ext = _extruder_stack(pos)
        values = OrderedDict()
        for key in SUMMARY_KEYS:
            try:
                values[key] = _jsonable(ext.getProperty(key, "value"))
            except Exception:
                values[key] = None
        extruders.append({"position": pos,
                          "material_id": ext.material.getMetaDataEntry("base_file", ext.material.getId()),
                          "settings": values})
    return {"model_name": _model_name(),
            "print_mode": gs.getProperty("printing_mode", "value"),
            "quality_type": mm.activeQualityType,
            "extruders": extruders,
            "objects": [_describe_object(n) for n in _objects()],
            "print": _print_information()}


def _model_name():
    """Name of the first object without extension: the case wants results/<model_name>/."""
    objects = _objects()
    if not objects:
        return None
    return os.path.splitext(objects[0].getName() or "")[0] or None


def _unique_results_dir(name):
    # A repeated run of the same part goes to "<name>_2", "<name>_3"... instead of overwriting.
    base = os.path.join(config.results_root(), "".join(c for c in name if c not in '<>:"/\\|?*').strip() or "result")
    path, n = base, 2
    while os.path.isdir(path) and os.listdir(path):
        path, n = "%s_%d" % (base, n), n + 1
    os.makedirs(path, exist_ok=True)
    return path


@tool("save_results",
      "Сохранить project.3mf, result.gcode и summary.json в папку results/<имя модели>. Имя папки берётся "
      "из имени модели автоматически. Вызывай после успешной нарезки.",
      {"folder_name": {"type": "string", "description": "Только если на столе нет модели"}}, [], main_thread=False)
def save_results(ctx, folder_name=None):
    folder = _unique_results_dir(run_on_main(_model_name) or folder_name or "result")
    out = {"folder": folder}
    out["project_3mf_bytes"] = _write_project(os.path.join(folder, "project.3mf"))
    out["gcode_bytes"] = _write_gcode(os.path.join(folder, "result.gcode"))
    # Machine-readable record of the decisions, for comparing runs with the reference answers.
    summary = run_on_main(_settings_summary)
    with open(os.path.join(folder, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    if ctx is not None:
        ctx.last_results_folder = folder
    return out


# ---------------------------------------------------------------- combined steps
# Every step resends the whole conversation, so the usual chains are single tools:
# fewer steps is the biggest saving. The fine-grained tools stay on the MCP server.

@tool("apply_plan",
      "Применить решение за один вызов, в правильном порядке: режим → материал → профиль качества → настройки. "
      "Любой пункт можно пропустить. Ошибки по пунктам возвращаются, остальное применяется.",
      {"mode": {"type": "string", "enum": PRINT_MODES_ALLOWED},
       "material_id": {"type": "string"},
       "quality_type": {"type": "string"},
       "settings": {"type": "object", "description": "{ключ: значение}, например {\"wall_line_count\": 6}"},
       "extruder": {"type": "integer", "description": "По умолчанию экструдер объекта"},
       "reasons": {"type": "object", "description": "Пользователь видит их до применения: {mode|material_id|"
                                                    "quality_type|ключ настройки: причина, до 10 слов}"}})
def apply_plan(ctx, mode=None, material_id=None, quality_type=None, settings=None, extruder=None, reasons=None):
    out = OrderedDict()
    errors = []

    def attempt(label, fn):
        try:
            return fn()
        except ToolError as e:
            errors.append("%s: %s" % (label, e))
            return None

    if mode:
        r = attempt("режим", lambda: set_print_mode(ctx, mode))
        if r:
            out["print_mode"] = r["print_mode"]
    if material_id:
        r = attempt("материал", lambda: set_material(ctx, material_id, extruder))
        if r:
            out["material"] = "%s (экструдер %s)" % (r["material_id"], r["extruder"])
            if r.get("warning"):
                errors.append("материал: " + r["warning"])
    if quality_type:
        r = attempt("профиль", lambda: set_quality_profile(ctx, quality_type))
        if r:
            out["quality_type"] = r["active"]
    if settings:
        changes = [{"key": k, "value": v, "extruder": extruder} for k, v in settings.items()]
        applied = OrderedDict()
        for item in set_settings(ctx, changes)["results"]:
            if "error" in item:
                errors.append("%s: %s" % (item["key"], item["error"]))
                continue
            applied[item["key"]] = item["value"]
            if item.get("warning"):
                errors.append("%s: %s" % (item["key"], item["warning"]))
        out["settings"] = applied
    out["layer_height"] = _jsonable(_global_stack().getProperty("layer_height", "value"))
    if errors:
        out["problems"] = errors
    return out


_catalogs = []


def _tr(context, text):
    """The settings panel shows definition texts through the translation catalog; show the same words."""
    if not text:
        return text
    try:
        if not _catalogs:
            from UM.i18n import i18nCatalog
            _catalogs.extend(i18nCatalog(name) for name in ("fdmprinter.def.json", "fdmextruder.def.json"))
        for catalog in _catalogs:
            translated = catalog.i18nc(context, str(text))
            if translated and translated != text:
                return translated
    except Exception:
        pass
    return text


# Plan card sections, in display order: Cura setting category -> section.
PLAN_GROUPS = [
    ("Режим, материал и профиль", ()),
    ("Геометрия и прочность", ("printing_mode_category", "reinforcement", "resolution", "shell", "infill")),
    ("Температура и скорость", ("material", "speed", "travel", "cooling")),
    ("Адгезия и поддержки", ("support", "platform_adhesion")),
    ("Прочее", ()),
]


def _category(key):
    try:
        found = _global_stack().definition.findDefinitions(key=key)
        node = found[0] if found else None
        while node is not None and node.parent is not None:
            node = node.parent
        return node.key if node is not None else None
    except Exception:
        return None


def _plan_group(item):
    if item["kind"] != "setting":
        return PLAN_GROUPS[0][0]
    category = _category(item.get("key") or "")
    for name, categories in PLAN_GROUPS:
        if category in categories:
            return name
    return PLAN_GROUPS[-1][0]


@tool("preview_plan",
      "Что изменит apply_plan: текущие и предлагаемые значения, варианты и предупреждения, без применения.",
      TOOLS["apply_plan"]["inputSchema"]["properties"], for_llm=False)
def preview_plan(ctx, mode=None, material_id=None, quality_type=None, settings=None, extruder=None, reasons=None):
    reasons = reasons or {}
    gs = _global_stack()
    position = _default_extruder(extruder)
    items = []

    def item(item_id, kind, label, current, proposed, options, reason):
        entry = OrderedDict([("id", item_id), ("kind", kind), ("label", label), ("current", current),
                             ("proposed", proposed), ("unchanged", current == proposed), ("reason", reason)])
        if options is not None:
            entry["options"] = options
            if proposed not in [o["value"] for o in options]:
                entry["error"] = "Нет такого варианта"
        items.append(entry)
        return entry

    if mode:
        options = gs.getProperty("printing_mode", "options") or {}
        item("mode", "mode", "Режим печати", gs.getProperty("printing_mode", "value"), mode,
             [{"value": m, "label": _tr("printing_mode option %s" % m, options.get(m, m))}
              for m in PRINT_MODES_ALLOWED if not options or m in options], reasons.get("mode"))
    if material_id:
        active = _extruder_stack(position).material
        item("material_id", "material", "Материал", active.getMetaDataEntry("base_file", active.getId()), material_id,
             [{"value": m["material_id"], "label": m["name"] or m["material_id"]}
              for m in _available_materials(position)], reasons.get("material_id") or reasons.get("material"))
    if quality_type:
        profiles = [p for p in list_quality_profiles(ctx)["profiles"] if p["available"]]
        item("quality_type", "quality", "Профиль качества", _app().getMachineManager().activeQualityType, quality_type,
             [{"value": p["quality_type"], "label": "%s (слой %s мм)" % (p["name"], p["layer_height"])}
              if p["layer_height"] is not None else {"value": p["quality_type"], "label": p["name"]}
              for p in profiles], reasons.get("quality_type") or reasons.get("quality"))
    for key, value in (settings or {}).items():
        try:
            stack = _target_stacks(key, extruder)[0]
        except ToolError as e:
            entry = item("settings." + key, "setting", key, None, value, None, reasons.get(key))
            entry["error"] = str(e)
            continue
        options = stack.getProperty(key, "options")
        entry = item("settings." + key, "setting", _tr("%s label" % key, stack.getProperty(key, "label")),
                     _jsonable(stack.getProperty(key, "value")), value,
                     [{"value": k, "label": _tr("%s option %s" % (key, k), v)} for k, v in options.items()]
                     if options else None, reasons.get(key))
        entry["key"] = key
        entry["type"] = stack.getProperty(key, "type")
        entry["unit"] = stack.getProperty(key, "unit")
        for prop in ("minimum_value", "maximum_value"):
            limit = _jsonable(stack.getProperty(key, prop))
            if isinstance(limit, (int, float)) and not isinstance(limit, bool):
                entry[prop.split("_")[0]] = limit
        try:
            entry["proposed"] = _jsonable(_coerce(stack, key, value))
            entry["unchanged"] = entry["proposed"] == entry["current"]
            entry.pop("error", None)
            warning = _range_warning(stack, key, entry["proposed"])
            if warning:
                entry["warning"] = warning
        except ToolError as e:
            entry["error"] = str(e)
    order = [name for name, _ in PLAN_GROUPS]
    for entry in items:
        entry["group"] = _plan_group(entry)
    items.sort(key=lambda e: order.index(e["group"]))  # stable: the LLM's order within a section
    return {"extruder": position, "items": items}


@tool("slice_and_save",
      "Нарезать и, если успешно, сохранить project.3mf, result.gcode и summary.json в results/<имя модели>.",
      {}, main_thread=False)
def slice_and_save(ctx):
    info = slice_model(ctx)
    saved = save_results(ctx)
    return {"print_time": info.get("print_time"), "total_weight_g": info.get("total_weight_g"),
            "by_extruder": info.get("by_extruder"), "folder": saved["folder"]}


# Kept for MCP clients and the HTTP API, hidden from the LLM (see tool()).
for _name in ("set_print_mode", "list_quality_profiles", "set_quality_profile", "set_material",
              "set_object_extruder", "get_setting", "set_settings", "reset_settings", "list_objects",
              "scale_object", "arrange_all", "delete_object", "slice", "get_slice_result",
              "export_gcode", "save_project_3mf", "save_results"):
    TOOLS[_name]["for_llm"] = False
