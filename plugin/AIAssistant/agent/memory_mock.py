# Stand-in for agent/memory.py (the agent's memory of the user's edits) until the
# real module lands: the same API and the same memory.json layout as the
# contract in the v0.7.0 plan, so the plan card and the "Память агента" window
# can be built and shown now. Kept in its own file (memory_mock.json) with demo
# data, so it never touches the real memory. memory_ui.py uses agent/memory.py
# as soon as it exists; this file can then be deleted.
#
# Only json, os, threading, time and uuid: the slicer's Python has a cut-down
# standard library (no wave, no array), so copies go through json too.
import json
import os
import threading
import time
import uuid

FILE_NAME = "memory_mock.json"
SCOPES = ("part", "material", "mode", "global")
STATUSES = ("candidate", "active", "disabled", "rejected")
ROLES = ("novice", "expert")

_lock = threading.RLock()


def _copy(value):
    return json.loads(json.dumps(value))

_path = None
_data = None


# ---- storage

def set_path(path):
    """Tests and the bridge may point the store at another file."""
    global _path, _data
    with _lock:
        _path, _data = path, None


def _file():
    global _path
    if _path is None:
        try:
            from UM.Resources import Resources
            folder = os.path.join(Resources.getDataStoragePath(), "ai_assistant")
        except Exception:
            folder = os.path.join(os.path.expanduser("~"), ".ste_ai_assistant")
        _path = os.path.join(folder, FILE_NAME)
    return _path


def _empty():
    return {"version": 1, "role": "novice", "events": [], "rules": [], "cases": []}


def _load():
    global _data
    if _data is not None:
        return _data
    path = _file()
    try:
        with open(path, "r", encoding="utf-8") as f:
            _data = json.load(f)
        for key, value in _empty().items():
            _data.setdefault(key, value)
    except (OSError, ValueError):
        if os.path.exists(path):  # broken file: keep it for a look, start empty
            try:
                os.replace(path, path + ".broken")
            except OSError:
                pass
            _data = _empty()
        else:
            _data = _demo()
        _save()
    return _data


def _save():
    path = _file()
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _new_id(prefix):
    return prefix + uuid.uuid4().hex[:8]


def _key(item_id):
    return item_id[len("settings."):] if item_id.startswith("settings.") else item_id


# ---- role

def role():
    with _lock:
        return _load().get("role") or "novice"


def set_role(value):
    if value not in ROLES:
        raise ValueError("role: novice или expert")
    with _lock:
        _load()["role"] = value
        _save()


# ---- checks

# min, max and a short "why" for the settings the demo touches; the real module
# reads the limits from the Cura stack, materials.csv and knowledge/.
LIMITS = {
    "wall_line_count": (1, 30),
    "infill_sparse_density": (0, 100),
    "layer_height": (0.05, 0.6),
    "cylindrical_layer_height": (0.05, 0.6),
    "material_print_temperature": (150, 320),
    "material_bed_temperature": (0, 130),
    "speed_print": (5, 300),
    "top_bottom_thickness": (0, 10),
}
NOZZLE_MAX = {"pla": 230, "petg": 255, "tpu": 235}


def validate(key, value, context=None):
    """(ok, reason). reason is shown under the row in red when ok is False."""
    key = _key(key)
    context = context or {}
    limits = LIMITS.get(key)
    if limits is not None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False, "значение должно быть числом"
        lo, hi = limits
        if number < lo or number > hi:
            return False, "противоречит базе знаний: допустимо от %s до %s" % (_num(lo), _num(hi))
        if key == "material_print_temperature":
            material = str(context.get("material") or "").lower()
            for base, top in NOZZLE_MAX.items():
                if base in material and number > top:
                    return False, "противоречит базе знаний: для %s сопло не горячее %d °C" % (base.upper(), top)
    if key == "adhesion_type" and str(value) == "raft" and str(context.get("mode") or "").startswith("cylindrical"):
        return False, "противоречит базе знаний: в 5D подложку raft не ставят"
    return True, ""


def _num(v):
    return str(int(v)) if float(v) == int(v) else str(v)


def _role_allows(scope, who):
    return scope == "part" or who == "expert"


# ---- from the plan card

def record_edits(context, changed, remember=None):
    """Write the card's edits to the journal; remember={"keys": [...], "scope": ...}
    (or a list of such, one per scope) turns them into rules. Keys are the card's
    item ids ("settings.wall_line_count", "mode") or bare setting keys.
    Returns {"events", "rules", "rejected": [{"key", "reason"}]}."""
    context = context or {}
    groups = remember if isinstance(remember, list) else ([remember] if remember else [])
    scope_of = {}
    for group in groups:
        for k in group.get("keys") or []:
            scope_of[_key(k)] = group.get("scope") or "part"
    out = {"events": [], "rules": [], "rejected": []}
    with _lock:
        data = _load()
        who = data.get("role") or "novice"
        now = time.time()
        for item_id, change in (changed or {}).items():
            key = _key(item_id)
            event = _event(context, key, change.get("proposed"), change.get("chosen"), "edit", who, now)
            data["events"].append(event)
            out["events"].append(event)
            scope = scope_of.get(key)
            if scope is None:
                continue
            if key == "mode":  # a precedent for this part, not a rule (the mode follows the geometry)
                part = context.get("part") or {}
                data["cases"].append({"id": _new_id("c_"), "ts": now, "mesh_hash": part.get("mesh_hash"),
                                      "features": part.get("features") or {}, "mode": change.get("chosen"),
                                      "material": context.get("material")})
                continue
            if not _role_allows(scope, who):
                out["rejected"].append({"key": key, "reason": "Новичок может запомнить только для этой детали"})
                continue
            ok, reason = validate(key, change.get("chosen"), context)
            if not ok:
                out["rejected"].append({"key": key, "reason": reason})
                continue
            rule = _upsert_rule(data, context, key, change.get("chosen"), scope, who, event["id"], now)
            out["rules"].append(rule)
        _save()
    return out


def _event(context, key, proposed, chosen, action, who, ts, **extra):
    part = context.get("part") or {}
    event = {"id": _new_id("e_"), "ts": ts,
             "part": {"name": part.get("name"), "mesh_hash": part.get("mesh_hash"), "class": part.get("class"),
                      "features": part.get("features") or {}},
             "material": context.get("material"), "mode": context.get("mode"), "key": key,
             "proposed": proposed, "chosen": chosen, "reason": context.get("reason") or "", "role": who,
             "action": action}
    event.update(extra)
    return event


def _scope_value(scope, context):
    part = context.get("part") or {}
    return {"part": part.get("mesh_hash") or part.get("name"), "material": context.get("material"),
            "mode": context.get("mode"), "global": None}[scope]


def _upsert_rule(data, context, key, value, scope, who, event_id, now):
    scope_value = _scope_value(scope, context)
    for rule in data["rules"]:
        if rule["key"] == key and rule["scope"] == scope and rule.get("scope_value") == scope_value \
                and rule["status"] != "rejected":
            same = str(rule["value"]) == str(value)
            rule["value"] = value
            rule["source_event_ids"].append(event_id)
            # a candidate becomes active on the second identical edit, or at once when an expert made it
            if rule["status"] == "candidate" and (same or who == "expert"):
                rule["status"] = "active"
            rule["updated_ts"] = now
            _log_rule(data, rule, "rule_edit", who, now)
            return rule
    rule = {"id": _new_id("r_"), "key": key, "value": value, "scope": scope, "scope_value": scope_value,
            "status": "active" if scope == "part" or who == "expert" else "candidate",
            "created_ts": now, "updated_ts": now, "role": who, "source_event_ids": [event_id], "note": "",
            # labels for the window, so the journal reads without the slicer
            "part_name": (context.get("part") or {}).get("name")}
    data["rules"].append(rule)
    _log_rule(data, rule, "rule_add", who, now)
    return rule


def _log_rule(data, rule, action, who, now, previous=None):
    """Every rule change is a journal line too; the copy of the rule after the
    change lets rollback() rebuild the rules at any moment."""
    data["events"].append({"id": _new_id("e_"), "ts": now, "part": {"name": rule.get("part_name")},
                           "material": rule.get("scope_value") if rule["scope"] == "material" else None,
                           "mode": rule.get("scope_value") if rule["scope"] == "mode" else None,
                           "key": rule["key"], "proposed": previous, "chosen": rule.get("value"),
                           "reason": "", "role": who, "action": action, "rule_id": rule["id"],
                           "rule": _copy(rule)})


# ---- applying rules to a plan

def _matches(rule, context):
    part = context.get("part") or {}
    scope, value = rule["scope"], rule.get("scope_value")
    if scope == "global":
        return True
    if scope == "part":
        return value is not None and value in (part.get("mesh_hash"), part.get("name"))
    return value is not None and str(value) == str(context.get(scope))


def rules_for(context):
    """Active rules that apply here, the narrowest scope first."""
    order = {"part": 0, "material": 1, "mode": 2, "global": 3}
    with _lock:
        rules = [r for r in _load()["rules"] if r["status"] == "active" and _matches(r, context or {})]
    return sorted(_copy(rules), key=lambda r: order.get(r["scope"], 9))


def apply_rules(plan, context):
    """(plan, applied): plan with the active rules put over its settings;
    applied = [{item_id, rule_id, from, to}] for the "из ваших правок" plaque."""
    plan = _copy(plan or {})
    settings = plan.setdefault("settings", {})
    applied, seen = [], set()
    for rule in rules_for(context):
        if rule["key"] in seen:
            continue
        seen.add(rule["key"])
        before = settings.get(rule["key"])
        if str(before) == str(rule["value"]):
            continue
        settings[rule["key"]] = rule["value"]
        applied.append({"item_id": "settings." + rule["key"], "rule_id": rule["id"], "from": before,
                        "to": rule["value"]})
    return plan, applied


def similar_cases(features, k=3):
    with _lock:
        return _copy(_load()["cases"][-k:])


# ---- the "Память агента" window

def _filtered(rows, flt, ts_key):
    flt = flt or {}
    out = []
    for row in rows:
        ts = row.get(ts_key) or 0
        if flt.get("from_ts") and ts < flt["from_ts"]:
            continue
        if flt.get("to_ts") and ts > flt["to_ts"]:
            continue
        ok = True
        for field in ("material", "mode", "key", "status", "role", "action", "scope"):
            want = flt.get(field)
            if want in (None, ""):
                continue
            have = row.get(field)
            if field in ("material", "mode") and "scope" in row:  # a rule: material/mode live in scope_value
                have = row.get("scope_value") if row.get("scope") == field else None
            if str(have) != str(want):
                ok = False
                break
        part = flt.get("part")
        if ok and part:
            name = (row.get("part") or {}).get("name") if isinstance(row.get("part"), dict) else row.get("part_name")
            ok = part.lower() in str(name or "").lower()
        if ok:
            out.append(row)
    return out


def _sorted(rows, sort, ts_key):
    sort = sort or {}
    by = sort.get("by") or "ts"
    field = ts_key if by == "ts" else by

    def value(row):
        if field == "part":
            v = (row.get("part") or {}).get("name") if isinstance(row.get("part"), dict) else row.get("part_name")
        elif field in ("material", "mode") and "scope" in row:
            v = row.get("scope_value") if row.get("scope") == field else None
        else:
            v = row.get(field)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return (0, v, "")
        return (1, 0, "" if v is None else str(v).lower())
    return sorted(rows, key=value, reverse=bool(sort.get("desc", by == "ts")))


def list_events(filter=None, sort=None):
    with _lock:
        rows = _copy(_load()["events"])
    for row in rows:
        row.pop("rule", None)
        row.pop("rules", None)
    return _sorted(_filtered(rows, filter, "ts"), sort, "ts")


def list_rules(filter=None, sort=None):
    with _lock:
        rows = _copy(_load()["rules"])
    return _sorted(_filtered(rows, filter, "updated_ts"), sort, "updated_ts")


def get_rule(rule_id):
    with _lock:
        for rule in _load()["rules"]:
            if rule["id"] == rule_id:
                return _copy(rule)
    return None


def update_rule(rule_id, **fields):
    """Change value, scope, scope_value, status or note. Raises ValueError when
    the new value fails validate() or the role may not use that scope."""
    with _lock:
        data = _load()
        who = data.get("role") or "novice"
        for rule in data["rules"]:
            if rule["id"] != rule_id:
                continue
            new = dict(rule)
            new.update({k: v for k, v in fields.items() if k in ("value", "scope", "scope_value", "status", "note")})
            if new["scope"] not in SCOPES or new["status"] not in STATUSES:
                raise ValueError("неизвестная область или статус")
            touches = any(str(new.get(k)) != str(rule.get(k)) for k in ("value", "scope", "scope_value"))
            if touches and not _role_allows(new["scope"], who):
                raise ValueError("Правила для материала, режима и «всегда» меняет только Эксперт")
            if new["scope"] in ("material", "mode") and not new.get("scope_value"):
                raise ValueError("выберите, для какого материала или режима правило")
            ok, reason = validate(new["key"], new["value"],
                                  {"material": new.get("scope_value") if new["scope"] == "material" else None,
                                   "mode": new.get("scope_value") if new["scope"] == "mode" else None})
            if not ok:
                raise ValueError(reason)
            previous = rule.get("value")
            rule.update(new)
            rule["updated_ts"] = time.time()
            action = "rule_off" if fields.get("status") == "disabled" else "rule_edit"
            _log_rule(data, rule, action, who, rule["updated_ts"], previous)
            _save()
            return _copy(rule)
    raise ValueError("правило не найдено")


def disable_rule(rule_id):
    return update_rule(rule_id, status="disabled")


def delete_rule(rule_id):
    with _lock:
        data = _load()
        for i, rule in enumerate(data["rules"]):
            if rule["id"] == rule_id:
                del data["rules"][i]
                gone = dict(rule, status="deleted")
                data["events"].append({"id": _new_id("e_"), "ts": time.time(), "part": {"name": rule.get("part_name")},
                                       "material": rule.get("scope_value") if rule["scope"] == "material" else None,
                                       "mode": rule.get("scope_value") if rule["scope"] == "mode" else None,
                                       "key": rule["key"], "proposed": rule.get("value"), "chosen": None,
                                       "reason": "", "role": data.get("role"), "action": "rule_delete",
                                       "rule_id": rule["id"], "rule": gone})
                _save()
                return True
    raise ValueError("правило не найдено")


def rollback(to_ts):
    """Put the rules back as they were at to_ts. The journal is only appended
    to, so the rollback itself is a journal line and can be rolled back too."""
    with _lock:
        data = _load()
        rules, order = {}, []
        for event in sorted(data["events"], key=lambda e: e["ts"]):
            if event["ts"] > to_ts:
                break
            if event.get("action") == "rollback" and "rules" in event:
                rules = {r["id"]: _copy(r) for r in event["rules"]}
                order = [r["id"] for r in event["rules"]]
                continue
            rule = event.get("rule")
            if not rule:
                continue
            if event["action"] == "rule_delete":
                rules.pop(rule["id"], None)
                continue
            if rule["id"] not in rules:
                order.append(rule["id"])
            rules[rule["id"]] = _copy(rule)
        before = len(data["rules"])
        data["rules"] = [rules[i] for i in order if i in rules]
        data["events"].append({"id": _new_id("e_"), "ts": time.time(), "part": {}, "material": None, "mode": None,
                               "key": None, "proposed": before, "chosen": len(data["rules"]),
                               "reason": "к состоянию на %s" % time.strftime("%d.%m.%Y %H:%M", time.localtime(to_ts)),
                               "role": data.get("role"), "action": "rollback", "to_ts": to_ts,
                               "rules": _copy(data["rules"])})
        _save()
        return len(data["rules"])


def export():
    with _lock:
        data = _copy(_load())
    data.pop("role", None)  # the role belongs to this PC, not to the shared rules
    return data


def import_(data, merge=True):
    """Add rules from an export (merge) or replace them. Returns how many rules
    came in. Rules are checked like edits; failing ones are skipped."""
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise ValueError("это не файл памяти агента: нет списка rules")
    with _lock:
        mine = _load()
        who = mine.get("role") or "novice"
        if not merge:
            mine["rules"] = []
        known = {(r["key"], r["scope"], str(r.get("scope_value")), str(r["value"])) for r in mine["rules"]}
        added = 0
        now = time.time()
        for rule in data["rules"]:
            if not isinstance(rule, dict) or not rule.get("key") or rule.get("scope") not in SCOPES:
                continue
            sig = (rule["key"], rule["scope"], str(rule.get("scope_value")), str(rule.get("value")))
            if sig in known or not validate(rule["key"], rule.get("value"), {})[0]:
                continue
            new = dict(rule, id=_new_id("r_"), updated_ts=now, source_event_ids=list(rule.get("source_event_ids") or []))
            new.setdefault("created_ts", now)
            new.setdefault("status", "active")
            new.setdefault("note", "")
            new["note"] = (new["note"] + " " if new["note"] else "") + "(импорт)"
            mine["rules"].append(new)
            known.add(sig)
            _log_rule(mine, new, "rule_add", who, now)
            added += 1
        _save()
        return added


# ---- demo data for the first start

def _demo():
    now = time.time()
    day = 86400.0
    data = _empty()
    parts = [("Шестерня z24", "a1f3", "gear"), ("Колесо конвейера", "b7c2", "wheel"),
             ("Втулка 40", "c9d4", "bushing"), ("Кронштейн", "d2e8", "bracket")]

    def ctx(i, material, mode):
        name, mesh, cls = parts[i]
        return {"part": {"name": name, "mesh_hash": mesh, "class": cls}, "material": material, "mode": mode}

    edits = [
        (19.2, 0, "fiberpart_pa12_g12", "classic", "wall_line_count", 3, 5, "novice"),
        (18.9, 0, "fiberpart_pa12_g12", "classic", "infill_sparse_density", 20, 40, "novice"),
        (14.1, 1, "fiberpart_pa12_g12", "cylindrical_full", "wall_line_count", 3, 5, "expert"),
        (11.0, 1, "fiberpart_pa12_g12", "cylindrical_full", "mode", "cylindrical", "cylindrical_full", "expert"),
        (9.5, 2, "enduse_petg", "cylindrical", "material_print_temperature", 240, 245, "novice"),
        (6.2, 3, "proto_pla", "classic", "speed_print", 60, 45, "novice"),
        (4.8, 3, "proto_pla", "classic", "support_enable", True, False, "novice"),
        (2.3, 2, "enduse_petg", "cylindrical", "cylindrical_layer_height", 0.2, 0.15, "expert"),
        (0.4, 0, "fiberpart_pa12_g12", "classic", "top_bottom_thickness", 0.8, 1.2, "expert"),
    ]
    events = []
    for days, i, material, mode, key, proposed, chosen, who in edits:
        event = _event(ctx(i, material, mode), key, proposed, chosen, "edit", who, now - days * day)
        events.append(event)
    data["events"] = events

    def rule(days, key, value, scope, scope_value, status, who, part_name, src, note=""):
        ts = now - days * day
        r = {"id": _new_id("r_"), "key": key, "value": value, "scope": scope, "scope_value": scope_value,
             "status": status, "created_ts": ts, "updated_ts": ts, "role": who,
             "source_event_ids": [events[src]["id"]], "note": note, "part_name": part_name}
        data["rules"].append(r)
        _log_rule(data, r, "rule_add", who, ts)
        return r

    rule(18.9, "infill_sparse_density", 40, "part", "a1f3", "active", "novice", "Шестерня z24", 1)
    rule(14.1, "wall_line_count", 5, "material", "fiberpart_pa12_g12", "active", "expert", "Колесо конвейера", 2,
         "зубья и обод ломались при 3 стенках")
    rule(9.5, "material_print_temperature", 245, "material", "enduse_petg", "candidate", "novice", "Втулка 40", 4)
    rule(6.2, "speed_print", 45, "global", None, "disabled", "novice", "Кронштейн", 5)
    rule(2.3, "cylindrical_layer_height", 0.15, "mode", "cylindrical", "active", "expert", "Втулка 40", 7)
    rule(0.4, "top_bottom_thickness", 1.2, "material", "fiberpart_pa12_g12", "active", "expert", "Шестерня z24", 8)
    data["cases"].append({"id": _new_id("c_"), "ts": now - 11 * day, "mesh_hash": "b7c2", "features": {},
                          "mode": "cylindrical_full", "material": "fiberpart_pa12_g12"})
    data["events"].sort(key=lambda e: e["ts"])
    return data
