# Memory of the user's edits on this PC: a journal of every edit made on the
# plan card, rules the user asked to remember, and print-mode precedents.
#
# Stored in the slicer's data folder as ai_assistant/memory.json (one file per
# PC, export/import as JSON). Plain Python with no slicer imports, so it can be
# tested without the slicer; the slicer-side pieces (the data folder, setting
# limits from the active stack) are plugged in by session.py.
#
# An edit is not a rule. Every edit goes to the journal; it becomes a rule only
# when the user presses "Запомнить" and the value passes validate(). A novice
# may remember a value for this part only; material / mode / "always" rules are
# an expert's. The same edit made twice without "Запомнить" becomes a candidate:
# the LLM sees it as a hint, the code does not apply it until an expert turns
# it on. The print mode is never a rule: a mode edit is stored as a precedent
# (part features + the chosen mode), applied for the same mesh and shown to the
# LLM as an example for similar parts.
#
# The journal is append-only and every rule change carries a snapshot of the
# rule, so the rules can be rolled back to any moment.
import copy
import csv
import io
import json
import os
import shutil
import threading
import time
import uuid

VERSION = 1
SCOPES = ("part", "material", "mode", "global")
STATUSES = ("candidate", "active", "disabled", "rejected")
ROLES = ("novice", "expert")
ACTIONS = ("edit", "rule_add", "rule_edit", "rule_off", "rule_delete", "rollback")
# Most specific first: a part rule beats a material rule beats a mode rule beats "always".
SCOPE_ORDER = {"part": 0, "material": 1, "mode": 2, "global": 3}
SCOPE_LABELS = {"part": "эта деталь", "material": "этот материал", "mode": "этот режим", "global": "всегда"}
PRINT_MODES = ("classic", "cylindrical", "cylindrical_full")
NON_SETTING_KEYS = ("material_id", "quality_type")
CANDIDATE_AFTER = 2          # the same edit this many times without "Запомнить" -> candidate
PROMPT_LINES = 15            # cap of the "Опыт этого ПК" block

KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge")

# fn(key, context) -> {"type", "minimum_value", "maximum_value", "minimum_value_warning",
# "maximum_value_warning", "options"} or None when the slicer has no such setting.
# Set by session.py to read the active stack; None in tests (only knowledge checks run).
limits_provider = None


class MemoryRefused(Exception):
    """An expected failure with a message for the user."""


def default_path():
    try:
        from UM.Resources import Resources
        base = Resources.getDataStoragePath()
    except Exception:
        base = os.path.join(os.path.expanduser("~"), ".ste_ai_assistant")
    return os.path.join(base, "ai_assistant", "memory.json")


def _now():
    return time.time()


def _new_id(prefix):
    return prefix + uuid.uuid4().hex[:8]


def rule_key(item_id):
    """Plan card item id -> rule key: settings.wall_line_count -> wall_line_count."""
    item_id = str(item_id or "")
    return item_id[len("settings."):] if item_id.startswith("settings.") else item_id


def item_id_for(key):
    """Rule key -> plan card item id."""
    return key if key in NON_SETTING_KEYS or key == "mode" else "settings." + key


def _same(a, b):
    if a == b:
        return True
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


def _part_of(context):
    part = (context or {}).get("part") or {}
    return {"name": part.get("name"), "mesh_hash": part.get("mesh_hash"),
            "class": part.get("class"), "features": part.get("features") or {}}


def _scope_value(scope, context):
    context = context or {}
    if scope == "part":
        part = _part_of(context)
        return part["mesh_hash"] or part["name"]
    if scope == "material":
        return context.get("material")
    if scope == "mode":
        return context.get("mode")
    return None


class Memory:
    def __init__(self, path=None):
        self.path = path or default_path()
        self.lock = threading.RLock()
        self.data = None
        self.load_note = None    # set when a broken file was moved aside on load
        self._role = None

    # ---------------------------------------------------------------- storage

    def _empty(self):
        return {"version": VERSION, "role": "novice", "events": [], "rules": [], "cases": []}

    def _load(self):
        if self.data is not None:
            return self.data
        data = None
        if os.path.exists(self.path):
            try:
                with io.open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict) or not all(isinstance(data.get(k, []), list)
                                                         for k in ("events", "rules", "cases")):
                    raise ValueError("не та структура")
            except Exception as e:
                backup = "%s.broken-%s" % (self.path, time.strftime("%Y%m%d-%H%M%S"))
                try:
                    shutil.move(self.path, backup)
                    self.load_note = "Файл памяти был повреждён (%s), сохранён как %s; начата пустая память" % (
                        e, os.path.basename(backup))
                except OSError:
                    self.load_note = "Файл памяти повреждён (%s); начата пустая память" % e
                data = None
        empty = self._empty()
        if data is None:
            data = empty
        for key, value in empty.items():
            data.setdefault(key, value)
        self.data = data
        return data

    def _save(self):
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        tmp = "%s.tmp-%s" % (self.path, uuid.uuid4().hex[:6])
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, self.path)

    # ---------------------------------------------------------------- role

    def role(self):
        with self.lock:
            role = self._load().get("role")
            return role if role in ROLES else "novice"

    def set_role(self, role):
        if role not in ROLES:
            raise MemoryRefused("Роль: novice или expert")
        with self.lock:
            self._load()["role"] = role
            self._save()
            return role

    # ---------------------------------------------------------------- journal

    def _event(self, context, action, key=None, proposed=None, chosen=None, reason=None, **extra):
        context = context or {}
        event = {"id": _new_id("e_"), "ts": _now(), "part": _part_of(context),
                 "material": context.get("material"), "mode": context.get("mode"),
                 "key": key, "proposed": proposed, "chosen": chosen, "reason": reason,
                 "role": self.role(), "action": action}
        event.update(extra)
        self._load()["events"].append(event)
        return event

    def _rule_event(self, action, rule, context=None, reason=None):
        context = context or {}
        return self._event(context, action, key=rule["key"], chosen=rule.get("value"), reason=reason,
                           rule_id=rule["id"], rule=copy.deepcopy(rule))

    # ---------------------------------------------------------------- record edits from the card

    def record_edits(self, context, changed, remember=None, reasons=None):
        """changed: {item id: {"proposed", "chosen"}} as session._edited_plan returns it.
        remember: {"keys": [item ids or rule keys], "scope": one of SCOPES}.
        Returns {"events": [ids], "rules": {key: {"ok", "reason", "rule_id", "status"}},
        "cases": [ids]}."""
        context = context or {}
        reasons = reasons or {}
        remember = remember or {}
        remember_keys = {rule_key(k) for k in remember.get("keys") or []}
        scope = remember.get("scope") or "part"
        out = {"events": [], "rules": {}, "cases": []}
        with self.lock:
            self._load()
            for item_id, change in (changed or {}).items():
                key = rule_key(item_id)
                event = self._event(context, "edit", key, change.get("proposed"), change.get("chosen"),
                                    reasons.get(key) or reasons.get(item_id))
                out["events"].append(event["id"])
                if key == "mode":
                    case = self._add_case(context, change.get("chosen"))
                    if case:
                        out["cases"].append(case["id"])
                    if key in remember_keys:
                        out["rules"][key] = {"ok": True, "status": "case", "rule_id": case and case["id"],
                                             "reason": "Режим зависит от геометрии: запомнен как пример для "
                                                       "этой детали и похожих, а не как правило"}
                    continue
                if key in remember_keys:
                    out["rules"][key] = self._remember(context, key, change.get("chosen"), scope, event)
                else:
                    candidate = self._maybe_candidate(context, key, change.get("chosen"), event)
                    if candidate:
                        out["rules"][key] = {"ok": True, "status": "candidate", "rule_id": candidate["id"],
                                             "reason": "Та же правка уже была: сохранена как кандидат, "
                                                       "включает эксперт"}
            # "Запомнить" on a value the user kept as proposed: still a rule, without an edit event.
            for key in remember_keys - {rule_key(k) for k in (changed or {})}:
                value = (remember.get("values") or {}).get(key, (remember.get("values") or {}).get(item_id_for(key)))
                if value is None:
                    continue
                if key == "mode":
                    case = self._add_case(context, value)
                    out["rules"][key] = {"ok": True, "status": "case", "rule_id": case and case["id"]}
                    continue
                out["rules"][key] = self._remember(context, key, value, scope, None)
            self._save()
        return out

    def _remember(self, context, key, value, scope, event):
        if scope not in SCOPES:
            return {"ok": False, "reason": "Область: одна из %s" % ", ".join(SCOPES)}
        if key == "material_id" and scope == "material":
            return {"ok": False, "reason": "Материал нельзя запомнить «для этого материала»"}
        role = self.role()
        if role != "expert" and scope != "part":
            return {"ok": False, "reason": "Новичок может запомнить значение только для этой детали; "
                                           "правило «%s» создаёт эксперт" % SCOPE_LABELS[scope]}
        scope_value = _scope_value(scope, context)
        if scope != "global" and not scope_value:
            return {"ok": False, "reason": "Нет данных для области «%s»" % SCOPE_LABELS[scope]}
        ok, reason = self.validate(key, value, context, role=role)
        if not ok:
            if event is not None:
                event["note"] = "не запомнено: " + reason
            return {"ok": False, "reason": reason}
        source = [event["id"]] if event else []
        rule = self._find_rule(key, scope, scope_value)
        now = _now()
        if rule is not None:
            rule.update({"value": value, "status": "active", "updated_ts": now, "role": role})
            rule["source_event_ids"] = (rule.get("source_event_ids") or []) + source
            self._rule_event("rule_edit", rule, context)
        else:
            rule = {"id": _new_id("r_"), "key": key, "value": value, "scope": scope, "scope_value": scope_value,
                    "status": "active", "created_ts": now, "updated_ts": now, "role": role,
                    "source_event_ids": source, "note": reason or None}
            self._load()["rules"].append(rule)
            self._rule_event("rule_add", rule, context)
        return {"ok": True, "status": rule["status"], "rule_id": rule["id"], "reason": reason or None}

    def _maybe_candidate(self, context, key, value, event):
        material = context.get("material")
        if not material:
            return None
        same = [e for e in self._load()["events"]
                if e.get("action") == "edit" and e.get("key") == key and e.get("material") == material
                and _same(e.get("chosen"), value)]
        if len(same) < CANDIDATE_AFTER:
            return None
        rule = self._find_rule(key, "material", material)
        if rule is not None:
            if rule["status"] == "candidate" and not _same(rule["value"], value):
                rule.update({"value": value, "updated_ts": _now()})
                self._rule_event("rule_edit", rule, context)
            if event["id"] not in (rule.get("source_event_ids") or []):
                rule["source_event_ids"] = (rule.get("source_event_ids") or []) + [event["id"]]
            return rule if rule["status"] == "candidate" else None
        ok, reason = self.validate(key, value, context, role="expert")
        if not ok:
            return None
        now = _now()
        rule = {"id": _new_id("r_"), "key": key, "value": value, "scope": "material", "scope_value": material,
                "status": "candidate", "created_ts": now, "updated_ts": now, "role": self.role(),
                "source_event_ids": [e["id"] for e in same], "note": "повторная правка без «Запомнить»"}
        self._load()["rules"].append(rule)
        self._rule_event("rule_add", rule, context)
        return rule

    def _find_rule(self, key, scope, scope_value):
        for rule in self._load()["rules"]:
            if rule["key"] == key and rule["scope"] == scope and rule.get("scope_value") == scope_value:
                return rule
        return None

    def _add_case(self, context, mode):
        if mode not in PRINT_MODES:
            return None
        part = _part_of(context)
        if not part["mesh_hash"] and not part["features"]:
            return None
        features = dict(part["features"])
        if part["class"]:
            features.setdefault("class", part["class"])
        case = {"id": _new_id("c_"), "ts": _now(), "mesh_hash": part["mesh_hash"], "features": features,
                "mode": mode, "material": context.get("material"), "name": part["name"]}
        cases = self._load()["cases"]
        # One precedent per mesh: the latest choice wins.
        cases[:] = [c for c in cases if not (part["mesh_hash"] and c.get("mesh_hash") == part["mesh_hash"])]
        cases.append(case)
        return case

    # ---------------------------------------------------------------- validation

    def validate(self, key, value, context=None, role=None):
        """(ok, reason). Hard limits of the slicer and the knowledge base reject for
        everyone; the slicer's "recommended" range rejects a novice and is only a
        note for an expert. reason is a short Russian sentence for the card."""
        context = context or {}
        role = role or self.role()
        key = rule_key(key)
        if key == "mode":
            return False, "Режим печати не запоминается правилом: он зависит от геометрии детали"
        if value is None or (isinstance(value, str) and not value.strip()):
            return False, "Пустое значение"
        if key == "material_id":
            ids = {row["material_id"] for row in _materials()}
            if ids and value not in ids:
                return False, "Противоречит базе знаний: материала %s нет в materials.csv" % value
            special = [row for row in _materials() if row["material_id"] == value and row.get("special")]
            if special:
                return False, "Противоречит базе знаний: %s — специальный материал, не для правила" % value
            return True, ""
        limits = None
        if limits_provider is not None:
            try:
                limits = limits_provider(key, context)
            except Exception:
                limits = None
            if limits is None and key not in NON_SETTING_KEYS:
                return False, "В слайсере нет настройки %s" % key
        note = ""
        if limits:
            ok, reason, value = _check_limits(key, value, limits, role)
            if not ok:
                return False, reason
            note = reason
        for check in KNOWLEDGE_CHECKS:
            reason = check(key, value, context)
            if reason:
                return False, "Противоречит базе знаний: " + reason
        return True, note

    # ---------------------------------------------------------------- reading rules

    def rules_for(self, context, statuses=("active",)):
        """Rules that match the context, one per key, most specific scope first."""
        context = context or {}
        with self.lock:
            best = {}
            for rule in self._load()["rules"]:
                if rule.get("status") not in statuses:
                    continue
                if rule["scope"] != "global" and rule.get("scope_value") != _scope_value(rule["scope"], context):
                    if not (rule["scope"] == "part" and rule.get("scope_value") == _part_of(context)["name"]):
                        continue
                rank = (SCOPE_ORDER[rule["scope"]], -(rule.get("updated_ts") or 0))
                if rule["key"] not in best or rank < best[rule["key"]][0]:
                    best[rule["key"]] = (rank, rule)
            return [copy.deepcopy(r) for _, r in sorted(best.values(), key=lambda b: b[0])]

    def case_for(self, context):
        mesh_hash = _part_of(context)["mesh_hash"]
        if not mesh_hash:
            return None
        with self.lock:
            for case in reversed(self._load()["cases"]):
                if case.get("mesh_hash") == mesh_hash:
                    return copy.deepcopy(case)
        return None

    def apply_rules(self, plan, context):
        """apply_plan arguments with the active rules laid over them.
        Returns (plan, applied), applied = [{item_id, rule_id, from, to, scope, scope_value}].
        A precedent for the same mesh sets the mode (rule_id is the case id)."""
        plan = copy.deepcopy(plan or {})
        context = dict(context or {})
        if plan.get("material_id"):
            context["material"] = plan["material_id"]
        applied = []
        case = self.case_for(context)
        if case and case["mode"] != plan.get("mode"):
            applied.append({"item_id": "mode", "rule_id": case["id"], "from": plan.get("mode"), "to": case["mode"],
                            "scope": "part", "scope_value": case.get("mesh_hash"), "kind": "case"})
            plan["mode"] = case["mode"]
        context["mode"] = plan.get("mode") or context.get("mode")
        # A material rule (part / mode / always) goes first: material-scoped rules match the new material.
        material_rule = next((r for r in self.rules_for(context) if r["key"] == "material_id"), None)
        if material_rule and not _same(plan.get("material_id"), material_rule["value"]):
            applied.append(_applied("material_id", material_rule, plan.get("material_id")))
            plan["material_id"] = context["material"] = material_rule["value"]
        for rule in self.rules_for(context):
            key = rule["key"]
            if key == "material_id":
                continue
            if key in NON_SETTING_KEYS:
                current = plan.get(key)
                if not _same(current, rule["value"]):
                    plan[key] = rule["value"]
                    applied.append(_applied(key, rule, current))
                continue
            settings = plan.setdefault("settings", {})
            current = settings.get(key)
            if current is None or not _same(current, rule["value"]):
                settings[key] = rule["value"]
                applied.append(_applied(key, rule, current))
        if plan.get("settings") == {}:
            plan.pop("settings")
        return plan, applied

    def similar_cases(self, features, k=3, exclude_hash=None):
        features = features or {}
        with self.lock:
            scored = []
            for case in self._load()["cases"]:
                if exclude_hash and case.get("mesh_hash") == exclude_hash:
                    continue
                distance = _distance(features, case.get("features") or {})
                if distance is not None:
                    scored.append((distance, case))
            scored.sort(key=lambda s: s[0])
            return [dict(copy.deepcopy(c), distance=round(d, 3)) for d, c in scored[:k]]

    def prompt_block(self, context):
        """«Опыт этого ПК»: active rules for the context, candidates and precedents, ≤ 15 lines."""
        context = context or {}
        lines = []
        case = self.case_for(context)
        if case:
            lines.append("- Эта деталь уже печаталась: пользователь выбрал режим %s (применится сам)" % case["mode"])
        for rule in self.rules_for(context):
            lines.append("- Правило (%s): %s = %s — применится кодом" % (
                _scope_text(rule), rule["key"], _fmt(rule["value"])))
        for rule in self.rules_for(context, statuses=("candidate",)):
            lines.append("- Кандидат (%s): %s = %s — подсказка, не применяется" % (
                _scope_text(rule), rule["key"], _fmt(rule["value"])))
        part = _part_of(context)
        for similar in self.similar_cases(part["features"], k=3, exclude_hash=part["mesh_hash"]):
            if similar["distance"] > 0.35:
                continue
            lines.append("- Похожая деталь %s (%s): выбран режим %s" % (
                similar.get("name") or similar.get("mesh_hash") or similar["id"],
                (similar.get("features") or {}).get("class") or "без класса", similar["mode"]))
        if not lines:
            return ""
        return "Опыт этого ПК:\n" + "\n".join(lines[:PROMPT_LINES - 1])

    def snapshot(self, context):
        """Read-only view for the LLM (get_memory)."""
        context = context or {}
        part = _part_of(context)
        return {
            "role": self.role(),
            "rules": [_short_rule(r) for r in self.rules_for(context)],
            "candidates": [_short_rule(r) for r in self.rules_for(context, statuses=("candidate",))],
            "case": self.case_for(context),
            "similar_cases": self.similar_cases(part["features"], k=3, exclude_hash=part["mesh_hash"]),
            "note": "Правила применяет код перед показом плана; кандидаты и похожие детали — только подсказки.",
        }

    # ---------------------------------------------------------------- lists for the memory window

    def list_events(self, filter=None, sort=None):
        with self.lock:
            rows = [e for e in self._load()["events"] if _matches(e, filter, event=True)]
            return _sorted(rows, sort or "-ts")

    def list_rules(self, filter=None, sort=None):
        with self.lock:
            rows = [r for r in self._load()["rules"] if _matches(r, filter, event=False)]
            return _sorted(rows, sort or "-updated_ts")

    def list_cases(self):
        with self.lock:
            return copy.deepcopy(self._load()["cases"])

    # ---------------------------------------------------------------- editing rules

    def _rule(self, rule_id):
        for rule in self._load()["rules"]:
            if rule["id"] == rule_id:
                return rule
        raise MemoryRefused("Нет правила %s" % rule_id)

    def update_rule(self, rule_id, **fields):
        """Change value / scope / scope_value / status / note. Validated like a new rule;
        a novice may only touch part rules and cannot widen a scope."""
        allowed = {"value", "scope", "scope_value", "status", "note"}
        unknown = set(fields) - allowed
        if unknown:
            raise MemoryRefused("Нельзя менять: %s" % ", ".join(sorted(unknown)))
        with self.lock:
            rule = self._rule(rule_id)
            role = self.role()
            new = dict(rule, **fields)
            if new["scope"] not in SCOPES:
                raise MemoryRefused("Область: одна из %s" % ", ".join(SCOPES))
            if new["status"] not in STATUSES:
                raise MemoryRefused("Статус: один из %s" % ", ".join(STATUSES))
            if role != "expert" and (rule["scope"] != "part" or new["scope"] != "part"):
                raise MemoryRefused("Новичок может менять только правила «для этой детали»")
            if new["scope"] == "global":
                new["scope_value"] = None
            elif "scope" in fields and fields["scope"] != rule["scope"] and "scope_value" not in fields:
                raise MemoryRefused("Для новой области укажите scope_value")
            if "value" in fields or new["status"] == "active":
                ok, reason = self.validate(new["key"], new["value"], _context_for(new), role=role)
                if not ok:
                    raise MemoryRefused(reason)
            other = self._find_rule(new["key"], new["scope"], new.get("scope_value"))
            if other is not None and other["id"] != rule_id:
                raise MemoryRefused("Такое правило уже есть: %s" % other["id"])
            action = "rule_off" if new["status"] == "disabled" and rule["status"] != "disabled" else "rule_edit"
            new["updated_ts"] = _now()
            new["role"] = role
            rule.clear()
            rule.update(new)
            self._rule_event(action, rule)
            self._save()
            return copy.deepcopy(rule)

    def disable_rule(self, rule_id):
        return self.update_rule(rule_id, status="disabled")

    def delete_rule(self, rule_id):
        with self.lock:
            rule = self._rule(rule_id)
            if self.role() != "expert" and rule["scope"] != "part":
                raise MemoryRefused("Новичок может удалять только правила «для этой детали»")
            self._load()["rules"].remove(rule)
            self._rule_event("rule_delete", rule)
            self._save()
            return True

    def rules_at(self, ts):
        """The rules as they were at ts, replayed from the journal."""
        with self.lock:
            state = {}
            for event in self._load()["events"]:
                if event["ts"] > ts:
                    break
                action = event.get("action")
                if action in ("rule_add", "rule_edit", "rule_off") and event.get("rule"):
                    state[event["rule"]["id"]] = copy.deepcopy(event["rule"])
                elif action == "rule_delete" and event.get("rule_id"):
                    state.pop(event["rule_id"], None)
                elif action == "rollback":
                    state = {r["id"]: copy.deepcopy(r) for r in event.get("rules") or []}
            return list(state.values())

    def rollback(self, to_ts):
        """Restore the rules (and precedents) as they were at to_ts. The journal keeps
        everything, so a rollback can itself be rolled back."""
        with self.lock:
            if self.role() != "expert":
                raise MemoryRefused("Откат делает эксперт")
            to_ts = float(to_ts)
            rules = self.rules_at(to_ts)
            data = self._load()
            removed_cases = [c for c in data["cases"] if c["ts"] > to_ts]
            data["rules"] = rules
            data["cases"] = [c for c in data["cases"] if c["ts"] <= to_ts]
            self._event({}, "rollback", to_ts=to_ts, rules=copy.deepcopy(rules),
                        removed_cases=[c["id"] for c in removed_cases])
            self._save()
            return {"rules": len(rules), "removed_cases": len(removed_cases)}

    # ---------------------------------------------------------------- export / import

    def export(self):
        with self.lock:
            data = copy.deepcopy(self._load())
            data.pop("role", None)   # the role belongs to this PC, not to the rules
            data["exported_ts"] = _now()
            return data

    def import_(self, data, merge=True):
        """merge=True adds the other PC's rules and precedents (ours win on the same
        key + scope); merge=False replaces them. Imported rules are re-validated on
        this PC; those that fail are skipped. The journal records every rule added."""
        if isinstance(data, (str, bytes)):
            data = json.loads(data)
        if not isinstance(data, dict) or not isinstance(data.get("rules", []), list):
            raise MemoryRefused("Это не файл памяти агента")
        with self.lock:
            if self.role() != "expert":
                raise MemoryRefused("Импорт правил делает эксперт")
            mem = self._load()
            report = {"added": 0, "skipped": [], "cases": 0}
            if not merge:
                for rule in list(mem["rules"]):
                    mem["rules"].remove(rule)
                    self._rule_event("rule_delete", rule, reason="импорт с заменой")
            for raw in data.get("rules") or []:
                try:
                    key, scope = raw["key"], raw["scope"]
                except (KeyError, TypeError):
                    continue
                if scope not in SCOPES or raw.get("status") not in STATUSES:
                    report["skipped"].append({"key": key, "reason": "неизвестная область или статус"})
                    continue
                if self._find_rule(key, scope, raw.get("scope_value")) is not None:
                    report["skipped"].append({"key": key, "reason": "такое правило уже есть"})
                    continue
                ok, reason = self.validate(key, raw.get("value"), _context_for(raw), role="expert")
                if not ok:
                    report["skipped"].append({"key": key, "reason": reason})
                    continue
                now = _now()
                rule = {"id": _new_id("r_"), "key": key, "value": raw.get("value"), "scope": scope,
                        "scope_value": raw.get("scope_value"), "status": raw["status"], "created_ts": now,
                        "updated_ts": now, "role": raw.get("role") or "expert", "source_event_ids": [],
                        "note": "импорт" + (": " + raw["note"] if raw.get("note") else "")}
                mem["rules"].append(rule)
                self._rule_event("rule_add", rule, reason="импорт")
                report["added"] += 1
            known = {c.get("mesh_hash") for c in mem["cases"]}
            for case in data.get("cases") or []:
                if isinstance(case, dict) and case.get("mode") in PRINT_MODES and case.get("mesh_hash") not in known:
                    mem["cases"].append(dict(case, id=_new_id("c_"), ts=_now()))
                    report["cases"] += 1
            self._save()
            return report


# ---------------------------------------------------------------- helpers

def _applied(key, rule, current):
    return {"item_id": item_id_for(key), "rule_id": rule["id"], "from": current, "to": rule["value"],
            "scope": rule["scope"], "scope_value": rule.get("scope_value"), "kind": "rule"}


def _context_for(rule):
    """A context in which a rule applies, for validating it outside the card."""
    scope, value = rule.get("scope"), rule.get("scope_value")
    if scope == "material":
        return {"material": value}
    if scope == "mode":
        return {"mode": value}
    if scope == "part":
        return {"part": {"mesh_hash": value}}
    return {}


def _short_rule(rule):
    return {"id": rule["id"], "key": rule["key"], "value": rule["value"], "scope": rule["scope"],
            "scope_value": rule.get("scope_value"), "status": rule["status"]}


def _scope_text(rule):
    if rule["scope"] == "global":
        return "всегда"
    return "%s %s" % (SCOPE_LABELS[rule["scope"]], rule.get("scope_value"))


def _fmt(value):
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)


def _distance(a, b):
    """Mean normalised difference over the numeric features both parts have; a
    different part class adds 0.5. None when nothing is comparable."""
    diffs = []
    for key, x in a.items():
        y = b.get(key)
        if isinstance(x, bool) or isinstance(y, bool):
            if isinstance(x, bool) and isinstance(y, bool):
                diffs.append(0.0 if x == y else 1.0)
            continue
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            scale = max(abs(x), abs(y), 1e-9)
            diffs.append(min(abs(x - y) / scale, 1.0))
    if not diffs and not (a.get("class") and b.get("class")):
        return None
    distance = sum(diffs) / len(diffs) if diffs else 0.0
    if a.get("class") and b.get("class") and a["class"] != b["class"]:
        distance += 0.5
    return distance


FILTER_FIELDS = ("material", "mode", "key", "status", "role", "scope", "action", "rule_id")


def _matches(row, flt, event):
    """flt: {"from_ts", "to_ts", "material", "mode", "key", "status", "role", "part", "scope", "action"}.
    For rules material / mode match the scope value; for events "part" matches name or mesh hash."""
    if not flt:
        return True
    ts = row.get("ts") if event else row.get("updated_ts")
    if flt.get("from_ts") is not None and (ts or 0) < float(flt["from_ts"]):
        return False
    if flt.get("to_ts") is not None and (ts or 0) > float(flt["to_ts"]):
        return False
    for field in FILTER_FIELDS:
        wanted = flt.get(field)
        if wanted in (None, ""):
            continue
        if not event and field in ("material", "mode"):
            if row.get("scope") != field or row.get("scope_value") != wanted:
                return False
            continue
        actual = row.get(field)
        if field == "key":
            wanted = rule_key(wanted)
        if isinstance(wanted, (list, tuple, set)):
            if actual not in wanted:
                return False
        elif actual != wanted:
            return False
    part = flt.get("part")
    if part:
        if event:
            p = row.get("part") or {}
            if part not in (p.get("name"), p.get("mesh_hash")):
                return False
        elif not (row.get("scope") == "part" and row.get("scope_value") == part):
            return False
    return True


def _sorted(rows, sort):
    """sort: a field name, "-" in front for descending; e.g. "-ts", "key", "material"."""
    reverse = sort.startswith("-")
    field = sort.lstrip("-")

    def value(row):
        if field == "material" and "scope" in row:
            v = row.get("scope_value") if row.get("scope") == "material" else None
        else:
            v = row.get(field)
        return (v is None, "" if v is None else (v if isinstance(v, (int, float)) else str(v)))
    try:
        rows = sorted(rows, key=value, reverse=reverse)
    except TypeError:
        rows = sorted(rows, key=lambda r: str(value(r)), reverse=reverse)
    return copy.deepcopy(rows)


def _check_limits(key, value, limits, role):
    """(ok, reason, coerced value) against the slicer's own limits for the setting."""
    setting_type = limits.get("type")
    options = limits.get("options") or {}
    if options:
        if str(value) not in options:
            return False, "Для %s допустимо: %s" % (key, ", ".join(map(str, options))), value
        return True, "", value
    if setting_type == "bool":
        return True, "", value
    if setting_type in ("float", "int"):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False, "%s — число, а не %r" % (key, value), value
        lo, hi = _num(limits.get("minimum_value")), _num(limits.get("maximum_value"))
        wlo, whi = _num(limits.get("minimum_value_warning")), _num(limits.get("maximum_value_warning"))
        if lo is not None and number < lo:
            return False, "Меньше минимума слайсера %s для %s" % (_fmt_num(lo), key), value
        if hi is not None and number > hi:
            return False, "Больше максимума слайсера %s для %s" % (_fmt_num(hi), key), value
        soft = None
        if wlo is not None and number < wlo:
            soft = "ниже рекомендуемого слайсером %s" % _fmt_num(wlo)
        elif whi is not None and number > whi:
            soft = "выше рекомендуемого слайсером %s" % _fmt_num(whi)
        if soft:
            if role != "expert":
                return False, "Значение %s: правило с ним может создать только эксперт" % soft, value
            return True, "Значение %s" % soft, value
    return True, "", value


def _num(value):
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_num(x):
    return ("%g" % x) if isinstance(x, float) else str(x)


# ---------------------------------------------------------------- knowledge checks
# Hard rules from knowledge/*.md and materials.csv that a remembered value must not
# break. Each returns a reason (without the "Противоречит базе знаний: " prefix) or None.

_materials_cache = {"mtime": None, "rows": []}


def _materials():
    path = os.path.join(KNOWLEDGE_DIR, "materials.csv")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    if _materials_cache["mtime"] == mtime:
        return _materials_cache["rows"]
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        text = f.read()
    delimiter = ";" if text.count(";") >= text.count(",") else ","
    rows = []
    for raw in csv.DictReader(io.StringIO(text), delimiter=delimiter):
        mid = (raw.get("material_id") or "").strip()
        if mid:
            row = {k: (v or "").strip() for k, v in raw.items() if k}
            row["special"] = row.get("special") in ("1", "true", "да", "yes")
            rows.append(row)
    _materials_cache["mtime"], _materials_cache["rows"] = mtime, rows
    return rows


def _material_row(context):
    material = (context or {}).get("material")
    for row in _materials():
        if row["material_id"] == material:
            return row
    return None


def _base(context):
    row = _material_row(context)
    return (row or {}).get("base", "").upper()


def _check_fan(key, value, context):
    # 20_settings.md: "PC, ABS, PA и композиты на них: до 20 %; PP G30: 0–10 %".
    if key != "cool_fan_speed":
        return None
    number = _num(value)
    if number is None:
        return None
    material = (context or {}).get("material") or ""
    base = _base(context)
    if material == "fiberpart_pp_g30" and number > 10:
        return "обдув для PP G30 — 0–10 %%, а не %g %%" % number
    if any(b in base for b in ("PC", "ABS", "PA")) and number > 20:
        return "обдув для %s — не больше 20 %%, а не %g %%" % (base.split()[0], number)
    return None


def _check_5d_adhesion(key, value, context):
    # 10_print_modes.md: в 5D модель насажена на цилиндрическое основание; подложка (raft) не нужна.
    # TODO: уточнить у печатника, допустимы ли brim/skirt в 5D.
    if key == "adhesion_type" and str(value) == "raft" and (context or {}).get("mode") in ("cylindrical",
                                                                                           "cylindrical_full"):
        return "в 5D деталь насажена на основание, подложка raft не применяется"
    return None


def _check_layer(key, value, context):
    # 20_settings.md: высота слоя от 0.1 (точные профили, декор) до 0.2 (функциональные, гибкие);
    # больше половины сопла 0.4 мм слой не держит.
    if key not in ("layer_height", "cylindrical_layer_height"):
        return None
    number = _num(value)
    if number is not None and number > 0.3:
        return "высота слоя %g мм больше 0.3 мм для сопла 0.4" % number
    return None


def _check_temperature(key, value, context):
    # materials.csv: heat_c — температура размягчения материала. Сопло ниже неё не расплавит пруток,
    # стол выше неё деформирует нижние слои.
    if key not in ("material_print_temperature", "material_print_temperature_layer_0",
                   "material_bed_temperature", "material_bed_temperature_layer_0"):
        return None
    number = _num(value)
    row = _material_row(context)
    heat = _num((row or {}).get("heat_c"))
    if number is None or heat is None or row.get("special"):
        return None
    if key.startswith("material_print") and number <= heat:
        return "сопло %g °C не выше температуры размягчения %s (%g °C)" % (number, row["name"], heat)
    if key.startswith("material_bed") and number > heat + 30:
        return "стол %g °C намного выше температуры размягчения %s (%g °C)" % (number, row["name"], heat)
    return None


KNOWLEDGE_CHECKS = [_check_fan, _check_5d_adhesion, _check_layer, _check_temperature]


_instance = {"memory": None}


def get():
    """The plugin's memory (one per PC)."""
    if _instance["memory"] is None:
        _instance["memory"] = Memory()
    return _instance["memory"]
