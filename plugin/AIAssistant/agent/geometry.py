# Geometric features of a mesh and the 3D / 5D print mode recommendation.
#
# Pure numpy, no slicer imports: tools.py hands in the triangles in printer
# axes (X, Y, Z up) and tools/mode_check.py runs the same code on STL files
# outside the slicer.
#
# The score is a list of conditions, each pushing to 3D or 5D with a weight,
# so the agent can say which condition decided. Weights were tuned on the 7
# case parts and a few primitives (see tools/mode_check.py).
import hashlib
import math
import re
import struct

import numpy

AXES = "xyz"
SAMPLES = 60000
ANGLE_BINS = 720
AXIAL_BINS = 60
MIN_BASE_DIAMETER = 4.0      # minimum_value of cylindrical_mode_base_diameter
SPIRAL_MAX_WALL_MM = 2.0     # 5D Spiral: thin wall of 1-2 perimeters


def load_stl(path):
    """Triangles (N, 3, 3) of a binary or ASCII STL file."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) >= 84:
        count = struct.unpack("<I", data[80:84])[0]
        if 84 + 50 * count == len(data):
            record = numpy.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("a", "<u2")])
            return numpy.frombuffer(data, dtype=record, count=count, offset=84)["v"].astype(numpy.float64)
    values = [line.split()[1:4] for line in data.decode("latin-1").splitlines()
              if line.strip().startswith("vertex")]
    return numpy.array(values, dtype=numpy.float64).reshape(-1, 3, 3)


def _r(x, nd=2):
    return None if x is None else round(float(x), nd)


def mesh_hash(tris):
    """Hash that survives moving and rotating the model: sorted triangle edge lengths."""
    edges = numpy.stack([numpy.linalg.norm(tris[:, 1] - tris[:, 0], axis=1),
                         numpy.linalg.norm(tris[:, 2] - tris[:, 1], axis=1),
                         numpy.linalg.norm(tris[:, 0] - tris[:, 2], axis=1)], axis=1)
    edges = numpy.round(numpy.sort(edges, axis=1), 2)
    edges = edges[numpy.lexsort(edges.T[::-1])]
    return hashlib.sha1(edges.astype("<f4").tobytes()).hexdigest()[:16]


def _axis_center(normals, centers, areas, axis, size):
    """Point in the plane across `axis` that the normals of the side faces point
    at. Surfaces of revolution have every normal line crossing the axis, so this
    is a least squares fit; Cauchy reweighting ignores faces that do not (cam
    lobes, blades)."""
    u, v = [k for k in range(3) if k != axis]
    n2 = normals[:, [u, v]]
    length = numpy.hypot(n2[:, 0], n2[:, 1])
    keep = (numpy.abs(normals[:, axis]) < 0.98) & (length > 0.3)
    if keep.sum() < 3:
        return None
    n2 = n2[keep] / length[keep][:, None]
    p2 = centers[keep][:, [u, v]]
    a = numpy.stack([-n2[:, 1], n2[:, 0]], axis=1)
    b = n2[:, 0] * p2[:, 1] - n2[:, 1] * p2[:, 0]
    base = areas[keep]
    weights = base.copy()
    center = None
    for scale in (0.3, 0.1, 0.03, 0.01, 0.005):
        w = numpy.sqrt(weights)
        center = numpy.linalg.lstsq(a * w[:, None], b * w, rcond=-1)[0]  # rcond=None needs numpy >= 1.14; the slicer may ship older
        weights = base / (1.0 + (numpy.abs(a.dot(center) - b) / (scale * size)) ** 2)
    return center


def _lines_blocked(tris2, points):
    """For each 2D point: does the line through it along the axis hit a triangle?"""
    a, b, c = tris2[:, 0], tris2[:, 1], tris2[:, 2]
    out = []
    for px, py in points:
        d1 = (px - b[:, 0]) * (a[:, 1] - b[:, 1]) - (a[:, 0] - b[:, 0]) * (py - b[:, 1])
        d2 = (px - c[:, 0]) * (b[:, 1] - c[:, 1]) - (b[:, 0] - c[:, 0]) * (py - c[:, 1])
        d3 = (px - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (py - a[:, 1])
        neg = (d1 < 0) | (d2 < 0) | (d3 < 0)
        pos = (d1 > 0) | (d2 > 0) | (d3 > 0)
        out.append(bool(numpy.any(~(neg & pos))))
    return out


def _through_hole_radius(tris2, outer_radius):
    """Radius of the empty channel along the axis, or None when the axis line hits the part."""
    lo2, hi2 = tris2.min(axis=1), tris2.max(axis=1)
    near = numpy.all((lo2 <= outer_radius) & (hi2 >= -outer_radius), axis=1)
    tris2 = tris2[near]
    if _lines_blocked(tris2, [(0.0, 0.0)])[0]:
        return None
    angles = numpy.linspace(0, 2 * math.pi, 16, endpoint=False)
    lo, hi = 0.0, outer_radius
    for _ in range(14):
        mid = (lo + hi) / 2
        box = numpy.all((lo2[near] <= mid) & (hi2[near] >= -mid), axis=1)
        points = [(mid * math.cos(t), mid * math.sin(t)) for t in angles]
        if any(_lines_blocked(tris2[box], points)):
            hi = mid
        else:
            lo = mid
    return lo


def _sample(tris, areas, count):
    rng = numpy.random.RandomState(0)
    idx = rng.choice(len(tris), count, p=areas / areas.sum())
    r1 = numpy.sqrt(rng.random_sample(count))
    r2 = rng.random_sample(count)
    t = tris[idx]
    return (t[:, 0] * (1 - r1)[:, None] + t[:, 1] * (r1 * (1 - r2))[:, None]
            + t[:, 2] * (r1 * r2)[:, None])


def _overhang_share_3d(tris, normals, areas, total):
    """Smallest overhang share (faces steeper than 45 deg down, minus the bed
    contact) over the 6 face-down placements."""
    centers_all = tris.mean(axis=1)
    best = None
    for axis in range(3):
        for sign in (1, -1):
            cos_down = -sign * normals[:, axis]
            height = sign * centers_all[:, axis] - (sign * tris[:, :, axis]).min()
            on_bed = (cos_down > 0.98) & (height < 0.3)
            over = (cos_down > math.cos(math.radians(45))) & ~on_bed
            share = areas[over].sum() / total
            if best is None or share < best[0]:
                best = (share, ("-" if sign > 0 else "+") + AXES[axis])
    return best


def analyze(tris, base_diameter=6.0):
    """Features for the print mode choice. `tris` is (N, 3, 3) in printer axes, mm."""
    tris = numpy.asarray(tris, dtype=numpy.float64)
    cross = numpy.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    area2 = numpy.linalg.norm(cross, axis=1)
    keep = area2 > 1e-12
    tris, cross, area2 = tris[keep], cross[keep], area2[keep]
    if len(tris) == 0:
        raise ValueError("empty mesh")
    normals = cross / area2[:, None]
    areas = area2 / 2.0
    total = float(areas.sum())
    centers = tris.mean(axis=1)
    flat = tris.reshape(-1, 3)
    size = flat.max(axis=0) - flat.min(axis=0)
    volume = abs(float(numpy.einsum("ij,ij->i", tris[:, 0], numpy.cross(tris[:, 1], tris[:, 2])).sum()) / 6.0)

    # Rotation axis: the printer axis whose surfaces are most "of revolution".
    best = None
    for axis in range(3):
        u, v = [k for k in range(3) if k != axis]
        extent = max(size[u], size[v]) / 2 or 1.0
        center = _axis_center(normals, centers, areas, axis, extent)
        if center is None:
            continue
        direction = numpy.zeros(3)
        direction[axis] = 1.0
        offset = centers - numpy.insert(center, axis, 0.0)
        cr = numpy.cross(normals, direction)
        dist = numpy.abs((offset * cr).sum(axis=1)) / numpy.maximum(numpy.linalg.norm(cr, axis=1), 1e-9)
        cap = numpy.abs(normals[:, axis]) > 0.98
        coaxial = ~cap & (dist < max(0.3, 0.01 * extent))
        share = (areas[coaxial].sum() + areas[cap].sum()) / total
        side = areas[~cap].sum()
        side_share = areas[coaxial].sum() / side if side > 0 else 0.0
        if best is None or share > best["share"]:
            best = {"axis": axis, "center": center, "share": share, "side_share": side_share}
    axis = best["axis"]
    u, v = [k for k in range(3) if k != axis]
    center = best["center"]

    # Outer radius around the axis by angle and along the axis.
    points = _sample(tris, areas, SAMPLES)
    q = points[:, [u, v]] - center
    radius = numpy.hypot(q[:, 0], q[:, 1])
    angle_bin = ((numpy.arctan2(q[:, 1], q[:, 0]) + math.pi) / (2 * math.pi) * ANGLE_BINS).astype(int) % ANGLE_BINS
    r_angle = numpy.zeros(ANGLE_BINS)
    numpy.maximum.at(r_angle, angle_bin, radius)
    filled = r_angle > 0
    if not filled.all():
        idx = numpy.arange(ANGLE_BINS)
        r_angle = numpy.interp(idx, idx[filled], r_angle[filled], period=ANGLE_BINS)
    outer_r = float(r_angle.max())
    roundness = float(r_angle.std() / max(r_angle.mean(), 1e-9))
    spectrum = numpy.abs(numpy.fft.rfft(r_angle - r_angle.mean())) / ANGLE_BINS * 2
    harmonic = int(numpy.argmax(spectrum[1:200])) + 1
    harmonic_amp = float(spectrum[harmonic] / max(outer_r, 1e-9))

    h = points[:, axis]
    length = float(size[axis])
    axial_bin = numpy.clip(((h - h.min()) / max(length, 1e-9) * AXIAL_BINS).astype(int), 0, AXIAL_BINS - 1)
    r_axial = numpy.zeros(AXIAL_BINS)
    numpy.maximum.at(r_axial, axial_bin, radius)
    steps = numpy.diff(r_axial)
    signs = numpy.sign(steps[numpy.abs(steps) > 0.01 * outer_r])
    folds = int((numpy.diff(signs) != 0).sum()) if len(signs) > 1 else 0

    tris2 = tris[:, :, [u, v]] - center
    hole_r = _through_hole_radius(tris2, outer_r)
    hole_d = None if hole_r is None else 2 * hole_r

    # Overhangs if printed in 5D: faces looking at the axis away from the base.
    qc = centers[:, [u, v]] - center
    rc = numpy.hypot(qc[:, 0], qc[:, 1])
    radial = (normals[:, [u, v]] * qc).sum(axis=1) / numpy.maximum(rc, 1e-9)
    seat = max(hole_r or 0.0, base_diameter / 2.0)
    inward = (radial < -math.cos(math.radians(60))) & (rc > seat + 1.0)
    overhang_5d = float(areas[inward].sum() / total)
    overhang_3d, best_face_down = _overhang_share_3d(tris, normals, areas, total)
    outer_cyl = (numpy.abs(normals[:, axis]) < 0.3) & (radial > 0.9)

    diameter = 2 * outer_r
    teeth = (harmonic if best["share"] >= 0.5 and harmonic >= 6 and harmonic_amp >= 0.01 and roundness >= 0.01
             else 0)
    features = {
        "mesh_hash": mesh_hash(tris),
        "size_mm": {"x": _r(size[0]), "y": _r(size[1]), "z": _r(size[2])},
        "volume_cm3": _r(volume / 1000.0, 1),
        "surface_area_mm2": _r(total, 1),
        "triangles": int(len(tris)),
        "rotation_axis": AXES[axis],
        "axis_center_mm": [_r(center[0]), _r(center[1])],
        "revolution_share": _r(best["share"]),
        "side_revolution_share": _r(best["side_share"]),
        "outer_diameter_mm": _r(diameter, 1),
        "length_along_axis_mm": _r(length, 1),
        "h_over_d": _r(length / max(diameter, 1e-9)),
        "roundness": _r(roundness, 3),
        "angular_harmonic": harmonic,
        "angular_harmonic_amp": _r(harmonic_amp, 3),
        "teeth": teeth,
        "axial_folds": folds,
        "through_hole": hole_r is not None,
        "hole_diameter_mm": _r(hole_d, 1),
        "mean_wall_mm": _r(2.0 * volume / total, 2),
        "overhang_share_3d": _r(overhang_3d, 3),
        "best_face_down_3d": best_face_down,
        "overhang_share_5d": _r(overhang_5d, 3),
        "outer_cylinder_share": _r(areas[outer_cyl].sum() / total, 3),
        "base_diameter_mm": _r(base_diameter, 1),
    }
    features["part_class"] = part_class(features)
    return features


def part_class(f):
    if f["revolution_share"] < 0.3:
        return "freeform"
    if f["teeth"]:
        return "gear"
    if f["h_over_d"] < 0.25:
        return "disk"
    if f["axial_folds"] >= 4:
        return "bellows"
    if f["roundness"] > 0.02:
        return "shaft_profile"
    if f["through_hole"] and f["mean_wall_mm"] <= SPIRAL_MAX_WALL_MM:
        return "tube"
    if f["overhang_share_5d"] >= 0.15:
        return "impeller"
    return "wheel"


# Requirements text: each group is one condition. Stems, lower case.
REQUIREMENT_RULES = [
    ("5d", 3, "ТЗ: расслоение ступицы/обода", r"расслоен|отслоен|delamin"),
    ("5d", 3, "ТЗ: ступеньки от слоёв недопустимы", r"ступеньк|лестничн|staircase"),
    ("5d", 2, "ТЗ: высокая окружная скорость / центробежная нагрузка", r"окружн\w* скорост|центробежн"),
    ("5d", 2, "ТЗ: работает наружная поверхность (обод, протектор, кулачок, ролик)",
     r"протектор|обод|кулач|копир|ролик"),
    ("5d", 1, "ТЗ: кручение", r"кручен|крутящ|скручив|torque"),
    ("3d", 3, "ТЗ: точный профиль в плоскости (зубья, эвольвента, шлицы)", r"эвольвент|зуб|шлиц|звёздочк|звездочк"),
    ("3d", 3, "ТЗ: гибкая/эластичная деталь", r"эластичн|гибк|пыльник|уплотнител|мембран"),
    ("3d", 2, "ТЗ: декоративная модель", r"декоратив|фигурк|сувенир"),
]


def _condition(name, side, weight, passed, value, note):
    return {"name": name, "favors": side, "weight": weight, "passed": bool(passed), "value": value, "note": note}


def recommend(f, requirements_text=None):
    """3D / 5D score with a breakdown by condition. Only passed conditions count."""
    c = []
    base = f.get("base_diameter_mm") or 6.0
    revolution = f["revolution_share"] >= 0.6 and f["roundness"] <= 0.02
    c.append(_condition("Тело вращения", "5d", 2, revolution,
                        {"revolution_share": f["revolution_share"], "roundness": f["roundness"],
                         "axis": f["rotation_axis"]},
                        "поверхности соосны оси %s, наружный контур круглый" % f["rotation_axis"] if revolution else
                        "наружный контур не круглый или поверхности не соосны"))
    c.append(_condition("Свободная форма", "3d", 3, f["revolution_share"] < 0.3, f["revolution_share"],
                        "нет оси вращения: 5D не подходит"))
    hole = f["hole_diameter_mm"] or 0.0
    hole_ok = f["through_hole"] and hole >= MIN_BASE_DIAMETER
    note = "нет сквозного осевого отверстия" if not f["through_hole"] else (
        "отверстие %.1f мм меньше минимального основания %.0f мм" % (hole, MIN_BASE_DIAMETER) if not hole_ok else
        "отверстие %.1f мм, основание %.1f мм%s" % (hole, base, "" if hole >= base - 0.2 else
                                                     ": уменьши cylindrical_mode_base_diameter до отверстия"))
    c.append(_condition("Сквозное осевое отверстие", "5d", 2, hole_ok, f["hole_diameter_mm"], note))
    c.append(_condition("Нет осевого отверстия", "3d", 2, not hole_ok, f["hole_diameter_mm"],
                        "в 5D только full: центральную часть печатать в 3D, деталь не насадить на основание"))
    c.append(_condition("Зубья / шлицы по окружности", "3d", 5, f["teeth"] > 0, f["teeth"],
                        "%d периодов наружного контура: точный профиль лежит в плоскости слоя" % f["teeth"]
                        if f["teeth"] else "периодичности наружного контура нет"))
    c.append(_condition("Плоский диск (h/D < 0.25)", "3d", 5, f["h_over_d"] < 0.25, f["h_over_d"],
                        "высота много меньше диаметра"))
    c.append(_condition("Гофры вдоль оси", "3d", 3, f["axial_folds"] >= 4, f["axial_folds"],
                        "радиус несколько раз растёт и падает вдоль оси: в 5D складки печатаются нависанием"))
    profile = (f["revolution_share"] >= 0.5 and f["roundness"] > 0.02 and not f["teeth"]
               and f["angular_harmonic"] <= 3)
    c.append(_condition("Рабочий профиль вокруг оси (кулачок, эксцентрик)", "5d", 1, profile,
                        f["angular_harmonic"], "вал с некруглым профилем по окружности"))
    oh3, oh5 = f["overhang_share_3d"], f["overhang_share_5d"]
    c.append(_condition("В 5D меньше нависаний", "5d", 2, oh3 - oh5 >= 0.05, {"3d": oh3, "5d": oh5},
                        "нависания 3D %.0f%%, 5D %.0f%% поверхности" % (100 * oh3, 100 * oh5)))
    c.append(_condition("В 3D меньше нависаний", "3d", 3, oh5 - oh3 >= 0.05, {"3d": oh3, "5d": oh5},
                        "в 5D поверхности, обращённые к оси, придётся поддерживать (%.0f%% против %.0f%%)"
                        % (100 * oh5, 100 * oh3)))
    c.append(_condition("3D — режим по умолчанию", "3d", 1, True, None, "без причин для 5D печатаем в 3D"))

    text = (requirements_text or "").lower().replace("ё", "е")
    for side, weight, name, pattern in REQUIREMENT_RULES:
        found = re.search(pattern.replace("ё", "е"), text) if text else None
        c.append(_condition(name, side, weight, found, found.group(0) if found else None,
                            "в тексте: «%s»" % found.group(0) if found else "не сказано"))

    score_5d = sum(x["weight"] for x in c if x["passed"] and x["favors"] == "5d")
    score_3d = sum(x["weight"] for x in c if x["passed"] and x["favors"] == "3d")
    spiral = f["through_hole"] and hole_ok and (f["mean_wall_mm"] or 99) <= SPIRAL_MAX_WALL_MM
    spiral_or_full = "cylindrical" if spiral else "cylindrical_full"
    verdict = spiral_or_full if score_5d > score_3d else "classic"
    margin = abs(score_5d - score_3d)
    confidence = "high" if margin >= 4 else ("medium" if margin >= 2 else "low")
    decided = sorted((x for x in c if x["passed"] and x["favors"] == ("5d" if verdict != "classic" else "3d")),
                     key=lambda x: -x["weight"])
    return {
        "verdict": verdict,
        "score_3d": score_3d,
        "score_5d": score_5d,
        "confidence": confidence,
        "spiral_or_full": spiral_or_full,
        "spiral_or_full_note": ("тонкая стенка %.1f мм и сквозное отверстие: Spiral" % f["mean_wall_mm"] if spiral else
                                "стенка %.1f мм или нет отверстия: full" % (f["mean_wall_mm"] or 0)),
        "decided_by": [x["name"] for x in decided[:3]],
        "conditions": [x for x in c if x["passed"]] + [x for x in c if not x["passed"]],
    }
