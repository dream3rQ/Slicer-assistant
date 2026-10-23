"""Сверка результатов агента с эталоном.

    python check.py <папка results>

Ищет во вложенных папках summary.json (его пишет save_results с версии 0.1.8),
а если его нет — project.3mf, и сравнивает с эталоном (parts.json): что совпало,
что нет и что не проверялось. Деталь узнаётся по имени модели, а для старых
результатов без summary.json — по имени папки.
"""
import configparser
import json
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SETTING_CHECKS = ("layer_height", "wall_line_count", "infill_sparse_density",
                  "support_enable", "support_enable_cylindrical", "adhesion_type")


def find_part(summary, parts, folder):
    names = " ".join([summary.get("model_name") or ""] +
                     [(o.get("name") or "") for o in summary.get("objects", [])]).lower()
    for pid, ref in parts.items():
        if not pid.startswith("_") and names.strip() and re.search(ref["match"], names):
            return pid
    folder = os.path.basename(folder).lower()
    for pid, ref in parts.items():
        if not pid.startswith("_") and re.search(ref.get("folder_match", ref["match"]), folder):
            return pid
    return None


def _cfg(z, name):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(z.read(name).decode("utf-8"))
    return parser


def _flatten(settings, out):
    for key, node in settings.items():
        if "default_value" in node:
            out[key] = node["default_value"]
        _flatten(node.get("children", {}), out)
    return out


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return {"True": True, "False": False}.get(value, value)


def summary_from_3mf(path):
    """Rough summary of an older project.3mf: values the agent set are exact; the
    rest come from the quality profile or the definition default (marked "~")."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        cfgs = {n.split("/")[-1][:-len(".inst.cfg")]: _cfg(z, n) for n in names if n.endswith(".inst.cfg")}
        glob = _cfg(z, [n for n in names if n.endswith(".global.cfg")][0])
        main = None
        for n in names:
            if n.endswith(".extruder.cfg"):
                c = _cfg(z, n)
                if c.get("metadata", "position", fallback="0") == "0":
                    main = c
        definition = json.loads(z.read([n for n in names if n.endswith(".def.json") and "extruder" not in n][0]))
    defaults = _flatten(definition.get("settings", {}), {})
    layers = []
    # User changes win over the quality profile; the extruder stack wins over the global one.
    for idx in ("0", "2"):
        for stack in (main, glob):
            name = stack.get("containers", idx, fallback=None) if stack else None
            if name in cfgs and cfgs[name].has_section("values"):
                layers.append(dict(cfgs[name].items("values")))

    def value(key):
        for layer in layers:
            if key in layer and not str(layer[key]).startswith("="):
                return _number(layer[key])
        default = defaults.get(key)
        return None if default is None else "~%s" % default

    keys = ("layer_height", "wall_line_count", "infill_sparse_density", "support_enable",
            "support_enable_cylindrical", "adhesion_type")
    return {"print_mode": value("printing_mode"),
            "extruders": [{"material_id": main.get("containers", "3") if main else None,
                           "settings": {k: value(k) for k in keys}}],
            "objects": [], "print": {}}


def check_value(expected, actual):
    if actual is None:
        return "нет данных"
    if isinstance(actual, str) and actual.startswith("~"):
        return "skip (не задано агентом)"
    if isinstance(expected, bool):
        return "ok" if bool(actual) == expected else "ошибка"
    if isinstance(expected, list) and len(expected) == 2 and all(isinstance(x, (int, float)) for x in expected):
        return "ok" if expected[0] - 1e-6 <= float(actual) <= expected[1] + 1e-6 else "ошибка"
    return "ok" if actual in expected else "ошибка"


def check(summary, ref):
    rows = []
    mode = summary.get("print_mode")
    rows.append(("режим", "/".join(ref["print_mode"]), mode, "ok" if mode in ref["print_mode"] else "ошибка"))
    ext = (summary.get("extruders") or [{}])[0]
    mat = ext.get("material_id")
    m = ref["material"]
    verdict = "ok" if mat == m["best"] else ("допустимо" if mat in m["ok"] else "ошибка")
    if m.get("unsure") and verdict == "ошибка":
        verdict = "skip (эталон не уверен)"
    rows.append(("материал", m["best"] + (" (+" + ", ".join(m["ok"]) + ")" if m["ok"] else ""), mat, verdict))
    settings = ext.get("settings") or {}
    for key in SETTING_CHECKS:
        if key in ref:
            rows.append((key, str(ref[key]), settings.get(key), check_value(ref[key], settings.get(key))))
    return rows


def main(root):
    with open(os.path.join(HERE, "parts.json"), encoding="utf-8") as f:
        parts = json.load(f)
    total = {"ok": 0, "all": 0}
    for dirpath, _, files in sorted(os.walk(root)):
        if "summary.json" in files:
            with open(os.path.join(dirpath, "summary.json"), encoding="utf-8") as f:
                summary = json.load(f)
        elif "project.3mf" in files:
            path = os.path.join(dirpath, "project.3mf")
            if os.path.getsize(path) == 0:
                print("\n== %s\n   project.3mf пустой (сохранение не завершилось)" % dirpath)
                continue
            summary = summary_from_3mf(path)
        else:
            continue
        pid = find_part(summary, parts, dirpath)
        print("\n== %s" % dirpath)
        if not pid:
            print("   деталь не распознана по имени модели")
            continue
        ref = parts[pid]
        print("   %s (эталон: %s), %s" % (ref["name"], ref["source"], summary.get("print", {}).get("print_time", "")))
        for name, exp, act, verdict in check(summary, ref):
            print("   %-28s %-12s ожидалось %-40s получено %s" % (name, verdict, exp, act))
            if not verdict.startswith("skip"):
                total["all"] += 1
                total["ok"] += verdict in ("ok", "допустимо")
    if total["all"]:
        print("\nИтого: %d из %d проверок совпали с эталоном" % (total["ok"], total["all"]))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
