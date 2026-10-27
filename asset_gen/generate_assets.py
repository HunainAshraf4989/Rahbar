#!/usr/bin/env python3
"""Parametric generator for VoiceStudio teaching assets.

Answers the question "can the pictures be system-generated rather than drawn by
hand?" — yes, but generated as *code that draws*, not as pixels from an image
model. A diffusion model emits an unstructured raster; this emits geometry, so
every part carries a name and the tutor can point at it.

One spec (a few dozen numbers, checkable against a datasheet) produces three
files per component:

    <id>.svg          traced-diagram layer; every part is an element with an id
    <id>.parts.json   the part map: id + spoken Urdu + written figure + labels
    <id>.png          photo-like raster base, in the SAME coordinate space

The SVG overlays the PNG exactly, which is Option C.

Domain knowledge lives in the spec (terminal count, numbering, layout), not in
drawing skill. Reading a datasheet is not the same job as being an auto
electrician.

Usage:  /home/hunain/base/bin/python asset_gen/generate_assets.py
Output: asset_gen/out/
"""

import json
import math
import os
import random
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
W, H = 640, 512                      # shared coordinate space, all assets
SS = 3                               # supersample factor for mask edges
F_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
F_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


# ══════════════════════════════════════════════════════════════════════════
# SPECS — the only part a human writes. Numbers come from the component
# datasheet / ISO 7588 standard, not from looking at a picture.
# ══════════════════════════════════════════════════════════════════════════

SPECS = [
    {
        "id": "relay_4pin",
        "kind": "iso_mini_relay",
        "curriculum_node": "AE/devices/relay",
        "title_en": "4-pin ISO mini relay",
        "title_ur": "چار پن والا ریلے",
        "case_text": ["12V  40A", "ISO MINI RELAY", "SPST-NO"],
        "internals": True,
        "terminals": [
            {"n": "86", "role": "coil",  "spoken_ur": "چھیاسی", "label_en": "Coil terminal",
             "label_ur": "کوائل کا ایک سرا", "note_en": "One end of the coil"},
            {"n": "85", "role": "coil",  "spoken_ur": "پچاسی",  "label_en": "Coil terminal",
             "label_ur": "کوائل کا دوسرا سرا", "note_en": "Other end of the coil; usually earthed"},
            {"n": "30", "role": "power", "spoken_ur": "تیس",    "label_en": "Common / battery feed",
             "label_ur": "بیٹری کی سپلائی", "note_en": "Battery supply in"},
            {"n": "87", "role": "power", "spoken_ur": "ستاسی",  "label_en": "Normally-open output",
             "label_ur": "لوڈ کی طرف", "note_en": "Supply out to the load when energised"},
        ],
    },
    {
        # Same family, one extra terminal. The whole point of a parametric
        # generator: this costs four lines, not another drawing.
        "id": "relay_5pin",
        "kind": "iso_mini_relay",
        "curriculum_node": "AE/devices/relay",
        "title_en": "5-pin ISO mini relay (changeover)",
        "title_ur": "پانچ پن والا ریلے",
        "case_text": ["12V  40/30A", "ISO MINI RELAY", "SPDT"],
        "internals": True,
        "terminals": [
            {"n": "86",  "role": "coil",  "spoken_ur": "چھیاسی",       "label_en": "Coil terminal",
             "label_ur": "کوائل کا ایک سرا", "note_en": "One end of the coil"},
            {"n": "85",  "role": "coil",  "spoken_ur": "پچاسی",        "label_en": "Coil terminal",
             "label_ur": "کوائل کا دوسرا سرا", "note_en": "Other end of the coil; usually earthed"},
            {"n": "30",  "role": "power", "spoken_ur": "تیس",          "label_en": "Common / battery feed",
             "label_ur": "بیٹری کی سپلائی", "note_en": "Battery supply in"},
            {"n": "87a", "role": "power", "spoken_ur": "ستاسی اے",     "label_en": "Normally-closed output",
             "label_ur": "عام حالت میں جڑا ہوا", "note_en": "Connected to 30 when the relay is OFF"},
            {"n": "87",  "role": "power", "spoken_ur": "ستاسی",        "label_en": "Normally-open output",
             "label_ur": "لوڈ کی طرف", "note_en": "Connected to 30 when the relay is ON"},
        ],
    },
    {
        "id": "fuse_blade_std",
        "kind": "blade_fuse",
        "curriculum_node": "AE/devices/fuse",
        "title_en": "Standard (ATO) blade fuse",
        "title_ur": "بلیڈ فیوز",
        "rating_a": 20,
        "body_rgb": [206, 178, 66],          # 20 A colour code: yellow
        "internals": True,
        "terminals": [
            {"n": "L", "role": "blade", "spoken_ur": "بایاں بلیڈ", "label_en": "Left blade",
             "label_ur": "بایاں بلیڈ", "note_en": "Push-fit contact into the fuse box"},
            {"n": "R", "role": "blade", "spoken_ur": "دایاں بلیڈ", "label_en": "Right blade",
             "label_ur": "دایاں بلیڈ", "note_en": "Push-fit contact into the fuse box"},
        ],
    },
]


# ══════════════════════════════════════════════════════════════════════════
# GEOMETRY — spec in, named shapes out. Nothing here is hand-placed.
# ══════════════════════════════════════════════════════════════════════════

def relay_geometry(spec):
    """Lay out an ISO mini relay from its terminal list.

    Coil terminals go left, power terminals right, each group centred within
    its half of the case. Blade widths follow role (coil blades are narrower
    than power blades, per ISO 7588).
    """
    case = {"x": 150, "y": 90, "w": 340, "h": 250, "r": 11}
    top_dx, top_dy = 34, -30

    coil = [t for t in spec["terminals"] if t["role"] == "coil"]
    power = [t for t in spec["terminals"] if t["role"] == "power"]

    parts, order = {}, []

    parts["case"] = {
        "kind": "case", "case": case, "top_dx": top_dx, "top_dy": top_dy,
        "svg": [
            ("polygon", [(case["x"], case["y"]),
                         (case["x"] + case["w"], case["y"]),
                         (case["x"] + case["w"] + top_dx, case["y"] + top_dy),
                         (case["x"] + top_dx, case["y"] + top_dy)]),
            ("polygon", [(case["x"] + case["w"], case["y"]),
                         (case["x"] + case["w"] + top_dx, case["y"] + top_dy),
                         (case["x"] + case["w"] + top_dx, case["y"] + case["h"] + top_dy),
                         (case["x"] + case["w"], case["y"] + case["h"])]),
            ("rect", (case["x"], case["y"], case["w"], case["h"], case["r"])),
        ],
    }
    order.append("case")

    # Blades sit in one row across the underside of the case. Gaps are solved
    # to fit whatever terminals the spec lists — 4-pin and 5-pin both work
    # without touching this code. Role changes get a wider gap so the coil
    # pair reads as a pair.
    row = coil + power
    DIM = {"coil": (28, 85), "power": (42, 95)}
    margin = 26
    interior = case["w"] - 2 * margin
    widths = [DIM[t["role"]][0] for t in row]
    changes = sum(1 for a, b in zip(row, row[1:]) if a["role"] != b["role"])
    units = (len(row) - 1) + 0.7 * changes
    gap = max(12.0, min(46.0, (interior - sum(widths)) / units)) if units else 0.0

    x = case["x"] + margin
    total = sum(widths) + gap * units
    x += (interior - total) / 2.0                       # centre the whole row
    for i, t in enumerate(row):
        bw, bh = DIM[t["role"]]
        pid = "terminal_%s" % t["n"]
        box = (round(x), case["y"] + case["h"], bw, bh)
        parts[pid] = {"kind": "terminal", "box": box, "spec": t,
                      "svg": [("rect", (box[0], box[1], box[2], box[3], 3))]}
        order.append(pid)
        x += bw
        if i < len(row) - 1:
            x += gap * (1.7 if row[i + 1]["role"] != t["role"] else 1.0)

    if spec.get("internals"):
        cx = (parts["terminal_%s" % coil[0]["n"]]["box"][0]
              + parts["terminal_%s" % coil[-1]["n"]]["box"][0]) / 2 + 14
        parts["coil"] = {
            "kind": "internal", "internal": True,
            "spec": {"label_en": "Copper coil", "label_ur": "تانبے کی کوائل",
                     "spoken_ur": "کوائل", "n": "",
                     "note_en": "Energising this makes an electromagnet"},
            "svg": [("rect", (cx - 45, 155, 90, 120, 8)),
                    ("ellipse", (cx, 155, 45, 13))],
            "hatch": [(cx - 43, y, cx + 43, y) for y in range(175, 260, 18)],
        }
        parts["armature"] = {
            "kind": "internal", "internal": True,
            "spec": {"label_en": "Armature", "label_ur": "آرمیچر", "spoken_ur": "آرمیچر", "n": "",
                     "note_en": "The magnet pulls this across"},
            "svg": [("polygon", [(300, 140), (396, 140), (396, 166),
                                 (326, 166), (326, 268), (300, 268)])],
        }
        parts["contacts"] = {
            "kind": "internal", "internal": True,
            "spec": {"label_en": "Switch contacts", "label_ur": "کانٹیکٹس",
                     "spoken_ur": "کانٹیکٹس", "n": "",
                     "note_en": "Close to pass the large current"},
            "svg": [("rect", (350, 232, 46, 13, 2)), ("rect", (350, 260, 46, 13, 2))],
        }
        order += ["coil", "armature", "contacts"]

    return parts, order


def fuse_geometry(spec):
    """Lay out a standard ATO blade fuse."""
    body = {"x": 236, "y": 120, "w": 168, "h": 190, "r": 14}
    parts, order = {}, []

    parts["body"] = {
        "kind": "case", "case": body,
        "spec": {"label_en": "Fuse body", "label_ur": "فیوز کا جسم",
                 "spoken_ur": "فیوز", "n": "",
                 "note_en": "Colour tells you the rating"},
        "svg": [("rect", (body["x"], body["y"], body["w"], body["h"], body["r"]))],
    }
    order.append("body")

    bw, bh = 40, 96
    for t, bx in zip(spec["terminals"],
                     [body["x"] + 22, body["x"] + body["w"] - 22 - bw]):
        pid = "blade_%s" % t["n"]
        box = (bx, body["y"] + body["h"] - 12, bw, bh)
        parts[pid] = {"kind": "terminal", "box": box, "spec": t,
                      "svg": [("rect", (box[0], box[1], box[2], box[3], 3))]}
        order.append(pid)

    if spec.get("internals"):
        parts["element"] = {
            "kind": "internal", "internal": True,
            "spec": {"label_en": "Fusible element", "label_ur": "پگھلنے والی تار",
                     "spoken_ur": "فیوز کی تار", "n": "",
                     "note_en": "Melts and breaks the circuit on overload"},
            "svg": [("polyline", [(276, 250), (276, 196), (306, 168),
                                  (334, 196), (364, 168), (364, 250)])],
        }
        order.append("element")
    return parts, order


BUILDERS = {"iso_mini_relay": relay_geometry, "blade_fuse": fuse_geometry}


# ══════════════════════════════════════════════════════════════════════════
# EMIT 1 — the SVG. Every part is an element carrying its id.
# ══════════════════════════════════════════════════════════════════════════

def shape_svg(kind, v):
    if kind == "rect":
        x, y, w, h, r = v
        return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"/>'
    if kind == "polygon":
        pts = " ".join(f"{x},{y}" for x, y in v)
        return f'<polygon points="{pts}"/>'
    if kind == "polyline":
        pts = " ".join(f"{x},{y}" for x, y in v)
        return f'<polyline points="{pts}" fill="none"/>'
    if kind == "ellipse":
        cx, cy, rx, ry = v
        return f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}"/>'
    raise ValueError(kind)


def write_svg(spec, parts, order, path):
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"',
        f'     data-asset="{spec["id"]}" data-curriculum-node="{spec["curriculum_node"]}">',
        f'  <title>{spec["title_en"]}</title>',
        '  <desc>Generated by asset_gen/generate_assets.py from a parametric spec.',
        '        Every addressable part is an element with an id.</desc>',
    ]
    for pid in order:
        p = parts[pid]
        cls = "part internal" if p.get("internal") else "part"
        shapes = [shape_svg(k, v) for k, v in p["svg"]]
        if len(shapes) == 1 and not p.get("hatch"):
            s = shapes[0].replace("<", f'<', 1)
            tag, rest = s[1:].split(" ", 1)
            lines.append(f'  <{tag} class="{cls}" id="{pid}" {rest}')
        else:
            lines.append(f'  <g class="{cls}" id="{pid}">')
            lines += [f"    {s}" for s in shapes]
            lines.append("  </g>")
        if p.get("hatch"):
            seg = " ".join(f"M{a},{b} L{c},{d}" for a, b, c, d in p["hatch"])
            lines.append(f'  <path class="detail" data-of="{pid}" d="{seg}" fill="none"/>')
    lines.append("</svg>")
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")


# ══════════════════════════════════════════════════════════════════════════
# EMIT 2 — the part map. Carries the id / spoken-Urdu / written triple that
# the script contract forces on us (the tutor says "پچاسی", the id is "85").
# ══════════════════════════════════════════════════════════════════════════

def write_part_map(spec, parts, order, path):
    entries = []
    for pid in order:
        p = parts[pid]
        s = p.get("spec", {})
        entries.append({
            "id": pid,
            "written": s.get("n", ""),
            "spoken_ur": s.get("spoken_ur", ""),
            "label_en": s.get("label_en", pid.replace("_", " ").title()),
            "label_ur": s.get("label_ur", ""),
            "note_en": s.get("note_en", ""),
            "visible_in_photo": not p.get("internal", False),
        })
    doc = {
        "asset_id": spec["id"],
        "curriculum_node": spec["curriculum_node"],
        "title_en": spec["title_en"],
        "title_ur": spec["title_ur"],
        "viewbox": [0, 0, W, H],
        "generated_by": "asset_gen/generate_assets.py",
        "provenance": {
            "method": "parametric — geometry computed from a written spec",
            "image_model_used": False,
            "spec_source": "component datasheet / ISO 7588 terminal numbering",
            "reviewed_by": None,
            "reviewed_on": None,
        },
        "part_vocabulary": [e["id"] for e in entries],
        "parts": entries,
    }
    open(path, "w", encoding="utf-8").write(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    return doc


# ══════════════════════════════════════════════════════════════════════════
# EMIT 3 — the photo-like raster, from the SAME geometry, so the SVG
# overlays it exactly. This is a rendered stand-in for a photograph.
# ══════════════════════════════════════════════════════════════════════════

def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _vgrad(size, top, bot, curve=1.0):
    w, h = size
    im = Image.new("RGB", (1, h))
    px = im.load()
    for y in range(h):
        px[0, y] = _lerp(top, bot, (y / max(1, h - 1)) ** curve)
    return im.resize((w, h), Image.BILINEAR)


def _hgrad(size, left, right):
    w, h = size
    im = Image.new("RGB", (w, 1))
    px = im.load()
    for x in range(w):
        px[x, 0] = _lerp(left, right, x / max(1, w - 1))
    return im.resize((w, h), Image.BILINEAR)


def _rmask(size, r):
    m = Image.new("L", (size[0] * SS, size[1] * SS), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        [0, 0, size[0] * SS - 1, size[1] * SS - 1], radius=r * SS, fill=255)
    return m.resize(size, Image.LANCZOS)


def _pmask(pts):
    m = Image.new("L", (W * SS, H * SS), 0)
    ImageDraw.Draw(m).polygon([(x * SS, y * SS) for x, y in pts], fill=255)
    return m.resize((W, H), Image.LANCZOS)


def _blade(img, box):
    x, y, w, h = box
    bl = _hgrad((w, h), (128, 132, 138), (206, 210, 214))
    p = bl.load()
    for i in range(w):
        t = i / max(1, w - 1)
        spec = int(58 * math.exp(-((t - 0.34) ** 2) / 0.012))
        for j in range(h):
            r, g, b = p[i, j]
            k = spec + random.randint(-7, 7) - int(30 * (j / h) ** 2)
            p[i, j] = tuple(max(0, min(255, c + k)) for c in (r, g, b))
    img.paste(bl, (x, y), _rmask((w, h), 3))


def _emboss(d, xy, text, font, base, anchor="la"):
    x, y = xy
    d.text((x, y + 1), text, font=font, fill=(12, 13, 16), anchor=anchor)
    d.text((x, y), text, font=font, fill=base, anchor=anchor)


def _finish(img):
    vig = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vig).ellipse([-150, -130, W + 150, H + 130], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(90))
    img = Image.composite(img, Image.eval(img, lambda v: int(v * 0.86)), vig)
    grain = Image.effect_noise((W, H), 6).convert("L")
    img = Image.blend(img, Image.merge("RGB", (grain,) * 3), 0.035)
    return img.filter(ImageFilter.UnsharpMask(radius=1.4, percent=55, threshold=3))


def _bench():
    img = Image.new("RGB", (W, H), (222, 224, 228))
    img.paste(_vgrad((W, H), (236, 237, 240), (198, 201, 207), 1.6), (0, 0))
    return img


def _shadow(img, boxes, ellipse):
    sh = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(sh)
    d.ellipse(ellipse, fill=120)
    for (x, y, w, h) in boxes:
        d.rectangle([x - 6, y, x + w + 10, y + h + 14], fill=95)
    sh = sh.filter(ImageFilter.GaussianBlur(16))
    return Image.composite(Image.new("RGB", (W, H), (120, 124, 132)), img, sh)


def render_relay(spec, parts, order, path):
    random.seed(7)
    img = _bench()
    case = parts["case"]["case"]
    x0, y0, w, h = case["x"], case["y"], case["w"], case["h"]
    x1, y1 = x0 + w, y0 + h
    tdx, tdy = parts["case"]["top_dx"], parts["case"]["top_dy"]
    blades = [parts[p]["box"] for p in order if parts[p]["kind"] == "terminal"]

    img = _shadow(img, blades, [x0 - 26, y1 + 58, x1 + 30, y1 + 118])
    for b in blades:
        _blade(img, b)
        d = ImageDraw.Draw(img)
        d.line([(b[0] + 1, b[1]), (b[0] + 1, b[1] + b[3] - 2)], fill=(238, 240, 243))
        d.line([(b[0] + b[2] - 2, b[1]), (b[0] + b[2] - 2, b[1] + b[3] - 2)], fill=(96, 100, 106))

    img.paste(_vgrad((W, H), (46, 48, 54), (20, 21, 25), 1.2), (0, 0),
              _pmask([(x1, y0), (x1 + tdx, y0 + tdy), (x1 + tdx, y1 + tdy), (x1, y1)]))
    img.paste(_vgrad((W, H), (78, 81, 88), (52, 55, 61)), (0, 0),
              _pmask([(x0, y0), (x1, y0), (x1 + tdx, y0 + tdy), (x0 + tdx, y0 + tdy)]))

    face = _vgrad((w, h), (74, 77, 84), (26, 28, 33), 1.35)
    fp = face.load()
    for j in range(h):
        for i in range(w):
            u, v = i / w, j / h
            k = int(30 * math.exp(-(((u - .30) ** 2) / .09 + ((v - .22) ** 2) / .22))
                    - 22 * (max(0.0, u - .80) / .20) ** 2 + random.randint(-4, 4))
            fp[i, j] = tuple(max(0, min(255, c + k)) for c in fp[i, j])
    img.paste(face, (x0, y0), _rmask((w, h), case["r"]))

    d = ImageDraw.Draw(img)
    d.line([(x0 + 8, y0 + 1), (x1 - 9, y0 + 1)], fill=(112, 116, 124), width=2)
    d.line([(x0 + 1, y0 + 9), (x0 + 1, y1 - 10)], fill=(96, 100, 108), width=2)
    d.line([(x0 + 9, y1 - 2), (x1 - 10, y1 - 2)], fill=(14, 15, 18), width=2)
    d.line([(x1 - 2, y0 + 10), (x1 - 2, y1 - 10)], fill=(16, 17, 21), width=2)
    for i in range(4):
        ry = y0 + 168 + i * 21
        d.line([(x0 + 30, ry), (x1 - 34, ry)], fill=(20, 21, 25), width=2)
        d.line([(x0 + 30, ry + 2), (x1 - 34, ry + 2)], fill=(88, 92, 99))

    f_big = ImageFont.truetype(F_BOLD, 30)
    f_sm = ImageFont.truetype(F_BOLD, 15)
    f_mid = ImageFont.truetype(F_MONO, 19)
    txt = spec["case_text"]
    _emboss(d, (x0 + 28, y0 + 26), txt[0], f_big, (150, 154, 161))
    for i, line in enumerate(txt[1:]):
        _emboss(d, (x0 + 30, y0 + 68 + i * 24), line, f_sm, (126, 130, 137))
    for pid in order:
        p = parts[pid]
        if p["kind"] != "terminal":
            continue
        bx, _, bw, _ = p["box"]
        _emboss(d, (bx + bw // 2, y1 - 32), p["spec"]["n"], f_mid,
                (158, 162, 169), anchor="ma")

    _finish(img).save(path, optimize=True)


def render_fuse(spec, parts, order, path):
    random.seed(11)
    img = _bench()
    b = parts["body"]["case"]
    x0, y0, w, h = b["x"], b["y"], b["w"], b["h"]
    blades = [parts[p]["box"] for p in order if parts[p]["kind"] == "terminal"]
    img = _shadow(img, blades, [x0 - 20, y0 + h + 84, x0 + w + 24, y0 + h + 132])
    for bl in blades:
        _blade(img, bl)

    base = tuple(spec["body_rgb"])
    hi = tuple(min(255, c + 46) for c in base)
    lo = tuple(int(c * 0.62) for c in base)
    body = _vgrad((w, h), hi, lo, 1.25)
    p = body.load()
    for j in range(h):
        for i in range(w):
            u, v = i / w, j / h
            k = int(34 * math.exp(-(((u - .28) ** 2) / .05 + ((v - .18) ** 2) / .30))
                    + random.randint(-5, 5))
            p[i, j] = tuple(max(0, min(255, c + k)) for c in p[i, j])
    img.paste(body, (x0, y0), _rmask((w, h), b["r"]))

    d = ImageDraw.Draw(img)
    d.rounded_rectangle([x0 + 18, y0 + 22, x0 + w - 18, y0 + h - 46],
                        radius=8, outline=tuple(int(c * .72) for c in base), width=2)
    if parts.get("element"):
        pts = parts["element"]["svg"][0][1]
        d.line(pts, fill=(228, 226, 214), width=7, joint="curve")
        d.line(pts, fill=(160, 152, 128), width=3, joint="curve")
    f = ImageFont.truetype(F_BOLD, 44)
    _emboss(d, (x0 + w // 2, y0 + h - 42), str(spec["rating_a"]), f,
            (46, 40, 16), anchor="md")
    _finish(img).save(path, optimize=True)


RENDERERS = {"iso_mini_relay": render_relay, "blade_fuse": render_fuse}


# ══════════════════════════════════════════════════════════════════════════

def main():
    os.makedirs(OUT, exist_ok=True)
    index = []
    for spec in SPECS:
        parts, order = BUILDERS[spec["kind"]](spec)
        base = os.path.join(OUT, spec["id"])
        write_svg(spec, parts, order, base + ".svg")
        doc = write_part_map(spec, parts, order, base + ".parts.json")
        RENDERERS[spec["kind"]](spec, parts, order, base + ".png")
        n_int = sum(1 for p in parts.values() if p.get("internal"))
        index.append({"asset_id": spec["id"], "curriculum_node": spec["curriculum_node"],
                      "parts": len(order), "internal_parts": n_int,
                      "files": [spec["id"] + e for e in (".svg", ".parts.json", ".png")]})
        print(f"  {spec['id']:16s} {len(order)} parts "
              f"({n_int} internal)  →  .svg .parts.json .png")
    open(os.path.join(OUT, "index.json"), "w", encoding="utf-8").write(
        json.dumps({"generated_by": "asset_gen/generate_assets.py",
                    "assets": index}, ensure_ascii=False, indent=2) + "\n")
    print(f"\n{len(index)} assets → {OUT}")


if __name__ == "__main__":
    main()
