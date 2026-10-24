"""Проверка выбора режима 3D/5D без слайсера.

    python tools/mode_check.py <папка с деталями> [--no-tz] [-v]

В папке — подпапки деталей кейса (STL + ТЗ в .docx). Для каждой детали считаются
геометрические признаки и вердикт recommend_print_mode (тот же код, что в плагине),
а ТЗ из .docx идёт как requirements_text. Ожидаемый режим берётся из parts.json.
Дальше — «ловушки» из примитивов: куб, сплошной цилиндр, тонкая трубка.
--no-tz: только геометрия, без текста ТЗ.
"""
import json
import math
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugin", "AIAssistant", "agent"))
import geometry  # noqa: E402
import numpy  # noqa: E402


def docx_text(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


def find_part(folder, stl, parts):
    """По имени STL (как check.py по имени модели), иначе по имени папки."""
    for name, key in ((stl.lower(), "match"), (os.path.basename(folder).lower(), "folder_match")):
        for pid, ref in parts.items():
            if not pid.startswith("_") and re.search(ref.get(key, ref["match"]), name):
                return pid
    return None


# ---- primitives

def _quad(a, b, c, d):
    return [[a, b, c], [a, c, d]]


def box(sx, sy, sz):
    p = [[x, y, z] for x in (0, sx) for y in (0, sy) for z in (0, sz)]
    faces = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    return numpy.array([t for f in faces for t in _quad(*[p[i] for i in f])], dtype=float)


def tube(outer_d, inner_d, height, segments=128):
    """Cylinder along z; inner_d=0 gives a solid cylinder."""
    ro, ri = outer_d / 2.0, inner_d / 2.0
    tris = []
    for i in range(segments):
        a0, a1 = 2 * math.pi * i / segments, 2 * math.pi * (i + 1) / segments
        o0 = [ro * math.cos(a0), ro * math.sin(a0)]
        o1 = [ro * math.cos(a1), ro * math.sin(a1)]
        tris += _quad(o0 + [0], o1 + [0], o1 + [height], o0 + [height])
        if ri > 0:
            i0 = [ri * math.cos(a0), ri * math.sin(a0)]
            i1 = [ri * math.cos(a1), ri * math.sin(a1)]
            tris += _quad(i1 + [0], i0 + [0], i0 + [height], i1 + [height])
            tris += _quad(o0 + [height], o1 + [height], i1 + [height], i0 + [height])
            tris += _quad(i0 + [0], i1 + [0], o1 + [0], o0 + [0])
        else:
            tris.append([[0, 0, height], o0 + [height], o1 + [height]])
            tris.append([[0, 0, 0], o1 + [0], o0 + [0]])
    return numpy.array(tris, dtype=float)


TRAPS = [
    ("Куб 30 мм", lambda: box(30, 30, 30), ["classic"]),
    ("Сплошной цилиндр D20 x 40 без отверстия", lambda: tube(20, 0, 40), ["classic"]),
    ("Тонкая трубка D20 x 40, стенка 1.2 мм", lambda: tube(20, 17.6, 40), ["cylindrical"]),
]


def report(name, expected, features, rec, verbose):
    ok = rec["verdict"] in expected
    print("%-4s %-45s ожидалось %-17s получено %-17s 3D %2d / 5D %2d, %s; решило: %s" % (
        "ok" if ok else "FAIL", name[:45], "/".join(expected), rec["verdict"], rec["score_3d"], rec["score_5d"],
        rec["confidence"], ", ".join(rec["decided_by"])))
    if verbose:
        print("     признаки:", json.dumps({k: v for k, v in features.items() if k not in ("size_mm",)},
                                          ensure_ascii=False))
        for c in rec["conditions"]:
            if c["passed"]:
                print("     +%d %s  %s: %s" % (c["weight"], c["favors"].upper(), c["name"], c["note"]))
    return ok


def main(argv):
    verbose = "-v" in argv
    use_tz = "--no-tz" not in argv
    args = [a for a in argv if not a.startswith("-")]
    with open(os.path.join(HERE, "parts.json"), encoding="utf-8") as f:
        parts = json.load(f)
    total = ok = 0
    if args:
        for folder in sorted(os.listdir(args[0])):
            path = os.path.join(args[0], folder)
            if not os.path.isdir(path):
                continue
            stls = [n for n in os.listdir(path) if n.lower().endswith(".stl")]
            pid = find_part(path, stls[0], parts) if stls else None
            if not pid or not stls:
                print("--   %s: деталь не распознана или нет STL" % folder)
                continue
            docs = [n for n in os.listdir(path) if n.lower().endswith(".docx")]
            text = docx_text(os.path.join(path, docs[0])) if docs and use_tz else None
            features = geometry.analyze(geometry.load_stl(os.path.join(path, stls[0])))
            rec = geometry.recommend(features, text)
            total += 1
            ok += report(parts[pid]["name"], parts[pid]["print_mode"], features, rec, verbose)
        print("Детали кейса: %d из %d" % (ok, total))
    trap_ok = 0
    for name, make, expected in TRAPS:
        features = geometry.analyze(make())
        trap_ok += report(name, expected, features, geometry.recommend(features), verbose)
    print("Ловушки: %d из %d" % (trap_ok, len(TRAPS)))
    return 0 if ok == total and trap_ok == len(TRAPS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
