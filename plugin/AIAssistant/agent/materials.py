# Material properties table and requirement matching for recommend_materials.
# The table lives in ../knowledge/materials.csv so the print specialist can
# edit it in Excel (";"-separated, decimal comma or point both accepted).
import csv
import io
import os

CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge", "materials.csv")

NUMERIC = ("water_24h_pct", "uv", "ozone", "heat_c", "tg_c", "modulus_mpa", "elongation_pct", "toughness",
           "oil_fuel", "alkali", "acid", "alcohol", "wear", "density", "print_difficulty")
BOOLEAN = ("conductive_filler", "food_contact", "smooth_surface", "special")

# requirement key -> (column, kind, human label)
CRITERIA = [
    ("max_water_24h_pct", "water_24h_pct", "max", "водопоглощение за 24 ч, %"),
    ("min_uv", "uv", "min", "стойкость к УФ (0–2)"),
    ("min_ozone", "ozone", "min", "стойкость к озону (0–2)"),
    ("min_heat_c", "heat_c", "min", "рабочая температура без размягчения, °C"),
    ("min_tg_c", "tg_c", "min", "температура стеклования Tg, °C"),
    ("min_modulus_mpa", "modulus_mpa", "min", "модуль упругости, МПа"),
    ("min_elongation_pct", "elongation_pct", "min", "удлинение при разрыве, %"),
    ("max_density", "density", "max", "плотность, г/см³"),
    ("min_toughness", "toughness", "min", "ударная вязкость, нехрупкость (0–2)"),
    ("min_oil_fuel", "oil_fuel", "min", "стойкость к маслам и топливу (0–2)"),
    ("min_alkali", "alkali", "min", "стойкость к щелочам (0–2)"),
    ("min_acid", "acid", "min", "стойкость к кислотам (0–2)"),
    ("min_alcohol", "alcohol", "min", "стойкость к спиртам (0–2)"),
    ("min_wear", "wear", "min", "износостойкость (0–2)"),
    ("flexible", "flex", "flex", "гибкость"),
    ("friction", "friction", "friction", "коэффициент трения"),
    ("no_conductive_filler", "conductive_filler", "false", "без токопроводящего наполнителя"),
    ("antistatic", "conductive_filler", "true", "антистатичность"),
    ("food_contact", "food_contact", "true", "допуск к пищевому контакту"),
    ("smooth_surface", "smooth_surface", "true", "гладкая поверхность"),
]

# Types only: how to map ТЗ wording to these keys is in knowledge/30_materials.md, which is
# already in the system prompt, so repeating it here would be paid for twice on every step.
REQUIREMENT_SCHEMA = {
    "max_water_24h_pct": {"type": "number"},
    "min_uv": {"type": "integer"},
    "min_ozone": {"type": "integer"},
    "min_heat_c": {"type": "number"},
    "min_tg_c": {"type": "number"},
    "min_modulus_mpa": {"type": "number"},
    "min_elongation_pct": {"type": "number"},
    "max_density": {"type": "number"},
    "min_toughness": {"type": "integer"},
    "min_oil_fuel": {"type": "integer"},
    "min_alkali": {"type": "integer"},
    "min_acid": {"type": "integer"},
    "min_alcohol": {"type": "integer"},
    "min_wear": {"type": "integer"},
    "flexible": {"type": "boolean"},
    "friction": {"type": "string", "enum": ["low", "high"]},
    "no_conductive_filler": {"type": "boolean"},
    "antistatic": {"type": "boolean"},
    "food_contact": {"type": "boolean"},
    "smooth_surface": {"type": "boolean"},
}

_cache = {"mtime": None, "rows": []}


def _num(text):
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def load():
    mtime = os.path.getmtime(CSV_PATH)
    if _cache["mtime"] == mtime:
        return _cache["rows"]
    with io.open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        sample = f.read()
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    rows = []
    for raw in csv.DictReader(io.StringIO(sample), delimiter=delimiter):
        mid = (raw.get("material_id") or "").strip()
        if not mid:
            continue
        row = {k: (v or "").strip() for k, v in raw.items() if k}
        for key in NUMERIC:
            row[key] = _num(row.get(key))
        for key in BOOLEAN:
            row[key] = row.get(key) in ("1", "true", "да", "yes")
        rows.append(row)
    _cache["mtime"], _cache["rows"] = mtime, rows
    return rows


def _check(row, kind, column, target):
    """Return (ok, margin) or None when the table has no data for it."""
    value = row.get(column)
    if kind in ("min", "max"):
        if value is None:
            return None
        target = float(target)
        diff = value - target if kind == "min" else target - value
        return diff >= 0, diff / max(abs(target), 0.05)
    if kind == "flex":
        is_flexible = value == "flexible"
        if target:
            return is_flexible, 0.0
        return value in ("rigid", "semi"), 0.0
    if kind == "friction":
        if target == "low":
            return value == "low", 0.0
        return value == "high", 0.0
    if kind == "true":
        return (not target) or bool(value), 0.0
    if kind == "false":
        return (not target) or not value, 0.0
    return None


def recommend(requirements, desirable=None, available_ids=None, include_special=False, limit=6):
    desirable = set(desirable or [])
    requirements = dict(requirements)
    if requirements.get("flexible") is None:
        # Most parts are rigid: an elastomer is only offered when flexibility was asked for.
        requirements["flexible"] = False
    unknown = [k for k in requirements if k not in REQUIREMENT_SCHEMA]
    scored = []
    for row in load():
        if row["special"] and not include_special:
            continue
        if available_ids is not None and row["material_id"] not in available_ids:
            continue
        passed, failed, hard_fails, soft_fails, margin, penalty = [], [], 0, 0, 0.0, 0.0
        for key, column, kind, label in CRITERIA:
            if key not in requirements or requirements[key] is None:
                continue
            result = _check(row, kind, column, requirements[key])
            value = row.get(column)
            item = "%s: %s (нужно %s %s)" % (label, value, {"min": "≥", "max": "≤"}.get(kind, "="),
                                              requirements[key])
            if result is None:
                failed.append(item + " — нет данных")
                soft_fails += 1
                continue
            ok, m = result
            margin += max(min(m, 1.0), -1.0)
            if ok:
                passed.append(item)
                continue
            failed.append(item + (" — желательно" if key in desirable else ""))
            # Weigh misses by size: 0.12 % water against 0.1 % is minor, 0.8 % is gross.
            weight = min(1.5, 0.25 + 0.5 * abs(m)) if kind in ("min", "max") else 1.0
            if key in desirable:
                soft_fails += 1
                penalty += 0.3 * weight
            else:
                hard_fails += 1
                # Rigid vs elastic is a different class of part, worse than missing one property.
                penalty += 2.0 if key == "flexible" else weight
        scored.append(((round(penalty, 2), -margin, row["print_difficulty"] or 0), {
            "material_id": row["material_id"],
            "name": row["name"],
            "base": row["base"],
            "fails_required": hard_fails,
            "fails_desirable": soft_fails,
            "penalty": round(penalty, 2),
            "failed": failed,
            "good_for": row.get("good_for"),
            "caveats": row.get("caveats"),
        }))
    scored.sort(key=lambda s: s[0])
    out = {
        "candidates": [s[1] for s in scored[:limit]],
        "next": [{"material_id": s[1]["material_id"], "penalty": s[1]["penalty"]}
                 for s in scored[limit:limit + 4]],
        "note": "Кандидаты отсортированы по penalty (0 — все требования выполнены; близкий промах весит меньше). "
                "Свойства ориентировочные (FDM-печать, сухой материал). Если penalty близки, решай по самым "
                "важным для детали требованиям и по description из list_materials; называй компромиссы.",
    }
    if unknown:
        out["ignored_requirements"] = unknown
    return out
