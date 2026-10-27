#!/usr/bin/env python3
"""
Photo-realistic, part-addressable component renderer.

WHY THIS EXISTS
---------------
Decision 12 (handoff §9) chose Option C for the visual channel: a realistic base
image with a traced overlay carrying the part ids. Decision 13 ruled that assets are
*system-generated from a written spec*, never produced by an image-generation model —
a diffusion model emits unstructured pixels with no named parts, which destroys part
addressing and would need MORE hand annotation, not less.

The first generator (`generate_assets.py`) satisfied the addressing requirement but
drew flat 2-D shapes: a rectangle with four round pins. A real ISO mini relay does not
look like that, and the difference matters, because the trainee is meant to recognise
the object on the bench.

This module replaces the drawing step with an actual renderer: a small analytic
ray-tracer over a solid model of the component, with Blinn-Phong shading, soft
shadows, moulded-in lettering via bump mapping, and brushed metal on the terminals.

The property that makes it the right tool rather than a vanity exercise:

    the tracer already knows which surface every ray hit, so it writes an ID buffer
    alongside the colour buffer. The SVG overlay is contour-traced from that buffer.

So the overlay is *pixel-exact against the render by construction* — the alignment
that Option C needs is not maintained by hand, it is impossible to break.

COMPONENT ACCURACY (verified against datasheets, 14 Aug 2026 — do not "recall" these)
------------------------------------------------------------------------------------
  * Coil terminals 85/86 are 4.8 x 0.8 mm blades; load terminals 30/87 are
    6.3 x 0.8 mm. DIN 46244. Bosch and TE/Tyco agree.
    -> This is a teaching fact, not just a modelling one: blade width is how you tell
       the control side from the load side by eye.
  * Blades are FLAT, not round pins, and protrude ~11 mm below the base.
  * The footprint is 2 x 2, not a single row. Type A and Type B differ only in the
    positions of 30 and 86 — fitting the wrong one damages the circuit, which is why
    the bottom-view inset is part of the asset rather than decoration.
  * Body is approximately a 1-inch cube (~25.5 mm), moulded black plastic, carrying
    the circuit schematic, the voltage and current rating, and the terminal numbers.

Sources: Picker PC785 ISO 280 datasheet (dimensioned drawing); TE V23234 mini ISO
series; 12voltplanet automotive relay guide; Bosch aftermarket relay catalogue.

Run:  /home/hunain/base/bin/python asset_gen/render3d.py
"""

import json
import math
import os
import sys

import numpy as np

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("PIL required", file=sys.stderr)
    raise

OUT = os.path.join(os.path.dirname(__file__), "out")

# ---------------------------------------------------------------------------
# Output geometry. The 640x512 logical space is kept because the demo, the part
# maps and the existing overlay code all share it. We render at SS x that and
# downsample, which is where the anti-aliasing comes from.
# ---------------------------------------------------------------------------
LOGICAL_W, LOGICAL_H = 640, 512
SCALE = 2               # final PNG is 1280x1024, displayed at 640x512
SS = 2                  # supersampling on top of that
W, H = LOGICAL_W * SCALE * SS, LOGICAL_H * SCALE * SS

TILE_ROWS = 256         # render in horizontal bands to bound peak memory


# ---------------------------------------------------------------------------
# Small vector helpers (float32 throughout — halves memory over float64)
# ---------------------------------------------------------------------------

def v3(x, y, z):
    return np.array([x, y, z], dtype=np.float32)


def normalize(a, axis=-1):
    n = np.linalg.norm(a, axis=axis, keepdims=True)
    return a / np.maximum(n, 1e-9)


# ---------------------------------------------------------------------------
# The solid model
#
# Units are millimetres, matching the datasheets, so the numbers in the spec can
# be checked against a real part. The camera does the rest.
# ---------------------------------------------------------------------------

BODY_W, BODY_D, BODY_H = 25.5, 25.5, 24.0     # ~1 inch cube
BASE_H = 3.4                                   # moulded base plate under the body
BLADE_T = 0.8                                  # DIN 46244 blade thickness
BLADE_COIL_W = 4.8                             # terminals 85, 86
BLADE_LOAD_W = 6.3                             # terminals 30, 87
BLADE_LEN = 11.3                               # protrusion below the base

# The part is shown standing clear of the surface, the way a component is
# photographed for a catalogue, so all four blades stay visible and addressable.
GROUND_Y = -(BLADE_LEN + 5.0)

# Type B footprint, stated explicitly so the asset can say which variant it is.
# x = left(-)/right(+) looking at the front face, z = back(-)/front(+).
FOOTPRINT = {
    "terminal_85": (-6.9, +6.4, BLADE_COIL_W),
    "terminal_87": (+6.9, +6.4, BLADE_LOAD_W),
    "terminal_30": (-6.9, -6.4, BLADE_LOAD_W),
    "terminal_86": (+6.9, -6.4, BLADE_COIL_W),
}

MAT_PLASTIC = 0
MAT_METAL = 1
MAT_BRACKET = 2
MAT_COPPER = 3      # coil windings
MAT_IRON = 4        # core and armature — darker, duller than the plated blades
MAT_SILVER = 5      # contact facings

# Each entry: (part_id, centre, half-extents, material)
# `part_id` is what lands in the ID buffer and therefore what the SVG overlay and
# the LLM's closed vocabulary are keyed on.
def build_scene(cutaway=False):
    boxes = []

    def box(pid, cx, cy, cz, hx, hy, hz, mat):
        boxes.append({
            "kind": "box",
            "id": pid,
            "c": v3(cx, cy, cz),
            "h": v3(hx, hy, hz),
            "mat": mat,
        })

    def cyl(pid, cx, cy, cz, r, half_len, axis, mat):
        boxes.append({
            "kind": "cyl",
            "id": pid,
            "c": v3(cx, cy, cz),
            "r": float(r),
            "hh": float(half_len),
            "axis": int(axis),          # 0 = x, 1 = y, 2 = z
            "mat": mat,
        })
        # An AABB is carried alongside so the shadow query can stay cheap.
        h = [r, r, r]
        h[axis] = half_len
        boxes[-1]["h"] = v3(*h)

    body_cy = BASE_H + BODY_H / 2.0
    # The case is modelled as four walls plus a lid when cut away, so the interior
    # is genuinely visible rather than faked with transparency.
    if cutaway:
        t = 1.6
        box("case", 0, body_cy, -BODY_D / 2 + t / 2, BODY_W / 2, BODY_H / 2, t / 2, MAT_PLASTIC)
        box("case", -BODY_W / 2 + t / 2, body_cy, 0, t / 2, BODY_H / 2, BODY_D / 2, MAT_PLASTIC)
        box("case", 0, BASE_H + BODY_H - t / 2, 0, BODY_W / 2, t / 2, BODY_D / 2, MAT_PLASTIC)
        # right wall kept only as a thin sliver so the cut reads as a cut
        box("case", BODY_W / 2 - t / 2, body_cy, -BODY_D / 4, t / 2, BODY_H / 2, BODY_D / 4, MAT_PLASTIC)
    else:
        box("case", 0, body_cy, 0, BODY_W / 2, BODY_H / 2, BODY_D / 2, MAT_PLASTIC)

    # Base plate — slightly proud of the body, as on the real moulding.
    box("case", 0, BASE_H / 2, 0, BODY_W / 2 + 0.55, BASE_H / 2, BODY_D / 2 + 0.55, MAT_PLASTIC)

    # Terminals.
    for pid, (x, z, bw) in FOOTPRINT.items():
        box(pid, x, -BLADE_LEN / 2, z, bw / 2, BLADE_LEN / 2, BLADE_T / 2, MAT_METAL)

    # No mounting bracket. The relay in the TEVTA "head lamp circuit with relay"
    # topic is the plug-in type that sits in a fuse/relay box, which has none — and
    # two earlier bracketed versions both dominated the silhouette badly enough to
    # obscure the terminals the asset exists to teach.

    # Internals, only present in the cutaway build. These three parts are the whole
    # reason the visual channel is an overlay and not a photograph: they are sealed
    # inside the case, so a photo of a real relay can address five of eight parts
    # and leaves the coil, the armature and the contacts with nothing to point at.
    if cutaway:
        coil_x, coil_y = -5.6, BASE_H + 11.0
        # Wound copper spool on a bobbin, with an iron core up the middle.
        cyl("coil", coil_x, coil_y, 0.5, 4.5, 7.4, 1, MAT_COPPER)
        cyl("coil", coil_x, coil_y + 7.9, 0.5, 5.5, 0.6, 1, MAT_PLASTIC)   # top flange
        cyl("coil", coil_x, coil_y - 7.9, 0.5, 5.5, 0.6, 1, MAT_PLASTIC)   # bottom flange
        cyl("coil", coil_x, coil_y + 9.6, 0.5, 1.5, 2.2, 1, MAT_IRON)      # core, proud on top

        # Armature: an L-shaped iron lever hinged at the top, pulled by the core.
        box("armature", -0.6, BASE_H + 19.6, 0.5, 6.4, 1.0, 3.4, MAT_IRON)
        box("armature", 5.4, BASE_H + 13.2, 0.5, 1.0, 7.4, 3.4, MAT_IRON)

        # Contacts: a fixed silver facing and a moving one carried on the armature.
        cyl("contacts", 5.4, BASE_H + 5.9, 0.5, 1.9, 0.7, 1, MAT_SILVER)
        cyl("contacts", 5.4, BASE_H + 3.9, 0.5, 1.9, 0.7, 1, MAT_SILVER)

    return boxes


# ---------------------------------------------------------------------------
# Surface markings.
#
# Rendered once into small textures, then applied per-face as albedo modulation
# plus a bump term. The bump is what makes the lettering read as moulded into the
# plastic rather than printed on top of it.
# ---------------------------------------------------------------------------

def _font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def make_top_texture(px=512):
    """Top face: maker's marks and ratings.

    Foreshortened heavily in a three-quarter view, so it carries only text that
    still reads at a glance. The schematic lives on the front face instead.
    """
    im = Image.new("L", (px, px), 128)
    d = ImageDraw.Draw(im)
    hi, lo = 205, 58

    f_big = _font(px // 7, bold=True)
    f_sm = _font(px // 15, bold=True)
    f_xs = _font(px // 21)

    def emboss(xy, text, font, anchor="mm"):
        d.text((xy[0] + 3, xy[1] + 3), text, font=font, fill=hi, anchor=anchor)
        d.text(xy, text, font=font, fill=lo, anchor=anchor)

    emboss((px / 2, px * 0.26), "12V  40A", f_big)
    emboss((px / 2, px * 0.50), "ISO MINI RELAY", f_sm)
    emboss((px / 2, px * 0.66), "SPST-NO  ·  TYPE B", f_xs)
    d.rectangle([px * 0.10, px * 0.10, px * 0.90, px * 0.90], outline=112, width=3)
    return np.asarray(im, dtype=np.float32) / 255.0


def make_front_texture(px=512):
    """Front face: the moulded circuit schematic and the terminal numbers.

    The schematic is the genuine one — a coil between 85 and 86, a switch between
    30 and 87 — because reading it off the case is part of the skill being taught.
    """
    im = Image.new("L", (px, px), 128)
    d = ImageDraw.Draw(im)
    hi, lo = 202, 60

    def line(p0, p1, w=4):
        d.line([p0, p1], fill=lo, width=w)
        d.line([(p0[0] + 2, p0[1] + 2), (p1[0] + 2, p1[1] + 2)], fill=hi, width=max(1, w - 2))

    # --- coil branch: 85 ----[coil]---- 86, drawn across the upper half --------
    y = px * 0.30
    x0, x1 = px * 0.20, px * 0.80
    line((x0, y), (x0 + px * 0.10, y))
    for i in range(4):
        x = x0 + px * 0.10 + i * px * 0.075
        d.arc([x, y - px * 0.045, x + px * 0.085, y + px * 0.045], 180, 360, fill=lo, width=4)
        d.arc([x + 2, y - px * 0.040, x + px * 0.085 + 2, y + px * 0.050], 180, 360, fill=hi, width=2)
    line((x0 + px * 0.10 + 4 * px * 0.075, y), (x1, y))

    # --- switch branch: 30 --/ -- 87, drawn across the lower half --------------
    y2 = px * 0.55
    line((x0, y2), (x0 + px * 0.14, y2))
    line((x0 + px * 0.14, y2), (x0 + px * 0.40, y2 - px * 0.10))
    line((x0 + px * 0.46, y2), (x1, y2))
    d.ellipse([x0 + px * 0.125, y2 - 7, x0 + px * 0.125 + 14, y2 + 7], fill=lo)
    d.ellipse([x0 + px * 0.445, y2 - 7, x0 + px * 0.445 + 14, y2 + 7], fill=lo)

    fnum = _font(px // 11, bold=True)
    fsm = _font(px // 22, bold=True)

    def emboss(xy, text, font, anchor="mm"):
        d.text((xy[0] + 3, xy[1] + 3), text, font=font, fill=hi, anchor=anchor)
        d.text(xy, text, font=font, fill=lo, anchor=anchor)

    # Schematic endpoint labels.
    emboss((x0 - px * 0.06, y), "85", fsm)
    emboss((x1 + px * 0.06, y), "86", fsm)
    emboss((x0 - px * 0.06, y2), "30", fsm)
    emboss((x1 + px * 0.06, y2), "87", fsm)

    # Terminal numbers moulded low on the face, beside the blades they belong to.
    # The front pair in the Type B footprint is 85 (left) and 87 (right).
    emboss((px * 0.22, px * 0.86), "85", fnum)
    emboss((px * 0.78, px * 0.86), "87", fnum)
    return np.asarray(im, dtype=np.float32) / 255.0


TEX_TOP = None
TEX_FRONT = None


# ---------------------------------------------------------------------------
# Ray tracing
# ---------------------------------------------------------------------------

def _cyl_hit(ro, rd, prim):
    """Finite capped cylinder along one axis. Returns (t, normal), t = inf on miss."""
    a = prim["axis"]
    u, v = [q for q in range(3) if q != a]
    c, r, hh = prim["c"], prim["r"], prim["hh"]

    ou = ro[:, u] - c[u]
    ov = ro[:, v] - c[v]
    du, dv = rd[:, u], rd[:, v]

    A = du * du + dv * dv
    B = 2.0 * (ou * du + ov * dv)
    C = ou * ou + ov * ov - r * r
    disc = B * B - 4.0 * A * C

    t = np.full(ro.shape[0], np.inf, dtype=np.float32)
    is_cap = np.zeros(ro.shape[0], dtype=bool)

    ok = (disc > 0) & (A > 1e-12)
    if ok.any():
        sq = np.sqrt(np.maximum(disc, 0))
        for root in (-1.0, 1.0):
            tc = (-B + root * sq) / (2.0 * A)
            ha = ro[:, a] + tc * rd[:, a]
            good = ok & (tc > 1e-4) & (tc < t) & (np.abs(ha - c[a]) <= hh)
            t = np.where(good, tc, t)

    # End caps.
    da = np.where(np.abs(rd[:, a]) < 1e-9, 1e-9, rd[:, a])
    for s in (-1.0, 1.0):
        tc = (c[a] + s * hh - ro[:, a]) / da
        pu = ro[:, u] + tc * rd[:, u] - c[u]
        pv = ro[:, v] + tc * rd[:, v] - c[v]
        good = (tc > 1e-4) & (tc < t) & (pu * pu + pv * pv <= r * r)
        t = np.where(good, tc, t)
        is_cap = np.where(good, True, is_cap)

    hit = np.isfinite(t)
    nrm = np.zeros_like(ro)
    if hit.any():
        p = ro + rd * np.where(hit, t, 0.0)[:, None]
        side = np.zeros_like(ro)
        side[:, u] = p[:, u] - c[u]
        side[:, v] = p[:, v] - c[v]
        side = normalize(side)
        cap = np.zeros_like(ro)
        cap[:, a] = np.sign(p[:, a] - c[a])
        nrm = np.where(is_cap[:, None], cap, side)
    return t, nrm


def intersect_prims(ro, rd, prims):
    """Nearest-hit test over the whole scene.

    Returns distance, primitive index, surface normal, and — for boxes only — the
    axis and sign of the face that was hit, which the edge-rounding and the
    face-texture code both key on. Cylinders report axis -1.
    """
    n = ro.shape[0]
    best_t = np.full(n, np.inf, dtype=np.float32)
    best_i = np.full(n, -1, dtype=np.int32)
    best_ax = np.full(n, -1, dtype=np.int8)
    best_sg = np.zeros(n, dtype=np.int8)
    best_n = np.zeros((n, 3), dtype=np.float32)

    inv = 1.0 / np.where(np.abs(rd) < 1e-8, 1e-8, rd)
    idx = np.arange(n)

    for i, b in enumerate(prims):
        if b.get("kind") == "cyl":
            tc, nc = _cyl_hit(ro, rd, b)
            hit = tc < best_t
            if not hit.any():
                continue
            best_t = np.where(hit, tc, best_t)
            best_i = np.where(hit, i, best_i)
            best_ax = np.where(hit, -1, best_ax)
            best_n = np.where(hit[:, None], nc, best_n)
            continue

        lo = (b["c"] - b["h"] - ro) * inv
        hi = (b["c"] + b["h"] - ro) * inv
        tmin3 = np.minimum(lo, hi)
        tmax3 = np.maximum(lo, hi)
        tmin = tmin3.max(axis=1)
        tmax = tmax3.min(axis=1)
        hit = (tmax >= np.maximum(tmin, 0.0)) & (tmin < best_t) & (tmin > 1e-4)
        if not hit.any():
            continue
        ax = np.argmax(tmin3, axis=1).astype(np.int8)
        sg = np.where(rd[idx, ax] > 0, -1, 1).astype(np.int8)
        nb = np.zeros((n, 3), dtype=np.float32)
        nb[idx, ax] = sg
        best_t = np.where(hit, tmin, best_t)
        best_i = np.where(hit, i, best_i)
        best_ax = np.where(hit, ax, best_ax)
        best_sg = np.where(hit, sg, best_sg)
        best_n = np.where(hit[:, None], nb, best_n)
    return best_t, best_i, best_n, best_ax, best_sg


def occluded(ro, rd, boxes, maxd):
    """Any-hit shadow query."""
    n = ro.shape[0]
    out = np.zeros(n, dtype=bool)
    inv = 1.0 / np.where(np.abs(rd) < 1e-8, 1e-8, rd)
    for b in boxes:
        lo = (b["c"] - b["h"] - ro) * inv
        hi = (b["c"] + b["h"] - ro) * inv
        tmin = np.minimum(lo, hi).max(axis=1)
        tmax = np.maximum(lo, hi).min(axis=1)
        out |= (tmax >= np.maximum(tmin, 0.0)) & (tmin > 1e-3) & (tmin < maxd)
    return out


def hash_noise(p, freq):
    """Cheap value noise for the plastic grain."""
    q = p * freq
    s = np.sin(q[:, 0] * 12.9898 + q[:, 1] * 78.233 + q[:, 2] * 37.719) * 43758.5453
    return s - np.floor(s)


def shade(ro, rd, boxes, ids, opts):
    t, bi, nrm_all, ax, sg = intersect_prims(ro, rd, boxes)
    n = ro.shape[0]
    col = np.zeros((n, 3), dtype=np.float32)
    idbuf = np.full(n, -1, dtype=np.int16)

    sky_t = np.clip(rd[:, 1] * 0.5 + 0.5, 0, 1)[:, None]
    bg = opts["bg_bot"] * (1 - sky_t) + opts["bg_top"] * sky_t
    col[:] = bg

    L = normalize(opts["light"])

    # Ground plane and cast shadow are OFF by default. A catalogue image of a
    # component sits on clean white: the dropped shadow reads as staging, and the
    # brief here is that the asset should look like the part, not like a photograph
    # of the part on a table. `ground=True` restores it if ever wanted.
    if opts.get("ground"):
        gt = np.where(np.abs(rd[:, 1]) > 1e-6, (GROUND_Y - ro[:, 1]) / rd[:, 1], -1.0)
        ghit = (gt > 1e-4) & (gt < t)
        if ghit.any():
            gp = ro[ghit] + rd[ghit] * gt[ghit][:, None]
            r = np.linalg.norm(gp[:, [0, 2]], axis=1)
            fade = np.clip(1.0 - r / 62.0, 0, 1) ** 1.6
            base = np.array([0.94, 0.95, 0.97], dtype=np.float32) * (0.62 + 0.38 * fade)[:, None]
            sh = np.zeros(gp.shape[0], dtype=np.float32)
            for jitter in opts["light_jitter"]:
                ldir = normalize(L + jitter)
                sh += occluded(gp + np.array([0, 1e-3, 0], np.float32),
                               np.broadcast_to(ldir, gp.shape).copy(), boxes, 400.0)
            sh /= len(opts["light_jitter"])
            base *= (1.0 - 0.55 * sh)[:, None]
            col[ghit] = base

    m = bi >= 0
    if not m.any():
        return col, idbuf, t

    p = ro[m] + rd[m] * t[m][:, None]
    axm, sgm = ax[m], sg[m]
    nrm = nrm_all[m].copy()

    mats = np.array([b["mat"] for b in boxes])[bi[m]]
    pid = np.array([ids.index(b["id"]) for b in boxes])[bi[m]]
    idbuf[m] = pid

    # --- rounded edges -----------------------------------------------------
    # Real mouldings have a radius. Rather than model it, bend the normal toward
    # the neighbouring face as the hit approaches an edge; at this scale that is
    # visually indistinguishable and costs nothing.
    cent = np.stack([boxes[i]["c"] for i in bi[m]])
    half = np.stack([boxes[i]["h"] for i in bi[m]])
    loc = (p - cent) / np.maximum(half, 1e-6)
    for a in range(3):
        other = [q for q in range(3) if q != a]
        onax = axm == a
        if not onax.any():
            continue
        for o in other:
            e = np.abs(loc[:, o])
            k = np.clip((e - 0.80) / 0.20, 0, 1) ** 1.5
            bend = np.sign(loc[:, o]) * k * 0.85 * onax
            nrm[:, o] += bend
    nrm = normalize(nrm)

    # --- materials ---------------------------------------------------------
    albedo = np.zeros_like(p)
    spec_k = np.zeros(p.shape[0], dtype=np.float32)
    shiny = np.zeros(p.shape[0], dtype=np.float32)

    is_pl = mats == MAT_PLASTIC
    is_mt = mats == MAT_METAL
    is_br = mats == MAT_BRACKET

    grain = hash_noise(p, 3.2) * 0.5 + hash_noise(p, 11.0) * 0.5
    # Albedos are LINEAR reflectance. Black relay plastic is about 0.02 linear
    # (~0.17 sRGB) — it reads as a dark charcoal that picks up a clear sheen off
    # every rounded edge, not as a matte black hole.
    albedo[is_pl] = np.array([0.021, 0.022, 0.026], np.float32)
    albedo[is_pl] *= (0.84 + 0.34 * grain[is_pl])[:, None]
    spec_k[is_pl] = 0.42
    shiny[is_pl] = 44.0

    # Brushed tin-plated blade: modulate along the blade's long axis.
    brush = 0.82 + 0.18 * np.sin(p[:, 1] * 5.5 + hash_noise(p, 40.0) * 6.0)
    albedo[is_mt] = np.array([0.38, 0.39, 0.42], np.float32) * brush[is_mt][:, None]
    spec_k[is_mt] = 0.58
    shiny[is_mt] = 62.0

    # Galvanised bracket — deliberately duller than the blades so it recedes.
    albedo[is_br] = np.array([0.26, 0.27, 0.29], np.float32) * brush[is_br][:, None]
    spec_k[is_br] = 0.34
    shiny[is_br] = 38.0

    # Copper windings. The stripe runs around the spool's axis, so a turn count
    # reads off the surface the way it does on a real bobbin.
    is_cu = mats == MAT_COPPER
    if is_cu.any():
        turns = 0.5 + 0.5 * np.sin(p[:, 1] * 9.5)
        wind = 0.62 + 0.38 * turns ** 0.6
        albedo[is_cu] = np.array([0.34, 0.115, 0.045], np.float32) * wind[is_cu][:, None]
        spec_k[is_cu] = 0.55
        shiny[is_cu] = 34.0

    is_fe = mats == MAT_IRON
    albedo[is_fe] = np.array([0.155, 0.158, 0.168], np.float32)
    spec_k[is_fe] = 0.30
    shiny[is_fe] = 26.0

    is_ag = mats == MAT_SILVER
    albedo[is_ag] = np.array([0.62, 0.62, 0.60], np.float32)
    spec_k[is_ag] = 0.80
    shiny[is_ag] = 90.0

    # --- moulded markings --------------------------------------------------
    body_top_y = BASE_H + BODY_H
    on_top = is_pl & (axm == 1) & (sgm > 0) & (np.abs(p[:, 1] - body_top_y) < 0.6)
    if on_top.any() and TEX_TOP is not None:
        u = np.clip((p[on_top, 0] + BODY_W / 2) / BODY_W, 0, 1)
        v = np.clip((p[on_top, 2] + BODY_D / 2) / BODY_D, 0, 1)
        s, bump = _sample_bump(TEX_TOP, u, v)
        albedo[on_top] *= (0.55 + 0.95 * s)[:, None]
        nn = nrm[on_top]
        nn[:, 0] += bump[0] * 2.2
        nn[:, 2] += bump[1] * 2.2
        nrm[on_top] = normalize(nn)

    on_front = (is_pl & (axm == 2) & (sgm > 0)
                & (np.abs(p[:, 2] - BODY_D / 2) < 0.4)
                & (p[:, 1] > BASE_H + 0.2))
    if on_front.any() and TEX_FRONT is not None:
        u = np.clip((p[on_front, 0] + BODY_W / 2) / BODY_W, 0, 1)
        v = np.clip(1.0 - (p[on_front, 1] - BASE_H) / BODY_H, 0, 1)
        s, bump = _sample_bump(TEX_FRONT, u, v)
        albedo[on_front] *= (0.55 + 0.95 * s)[:, None]
        nn = nrm[on_front]
        nn[:, 0] += bump[0] * 2.0
        nn[:, 1] += bump[1] * 2.0
        nrm[on_front] = normalize(nn)

    # Mould parting line around the body's waist.
    seam = is_pl & (np.abs(p[:, 1] - (BASE_H + BODY_H * 0.52)) < 0.22) & (axm != 1)
    albedo[seam] *= 0.62

    # --- lighting ----------------------------------------------------------
    V = -rd[m]
    ndl = np.clip((nrm * L).sum(1), 0, 1)

    # Self-shadowing is kept, because without it the sectioned view loses all sense
    # of depth, but it is deliberately soft: at full strength the blades went black
    # under the base plate, which is a lighting artefact rather than how the part
    # looks in the hand.
    sh = np.zeros(p.shape[0], dtype=np.float32)
    for jitter in opts["light_jitter"]:
        ldir = normalize(L + jitter)
        sh += occluded(p + nrm * 1e-2, np.broadcast_to(ldir, p.shape).copy(), boxes, 400.0)
    sh /= len(opts["light_jitter"])
    ndl *= (1.0 - opts.get("self_shadow", 0.42) * sh)

    Hv = normalize(L + V)
    ndh = np.clip((nrm * Hv).sum(1), 0, 1)
    spec = spec_k * np.power(ndh, shiny)

    fill_dir = normalize(v3(-0.55, 0.35, 0.75))
    fill = np.clip((nrm * fill_dir).sum(1), 0, 1) * 0.42

    # Kicker from behind-left. On a near-black moulding this is what separates the
    # silhouette from the background and makes the edge radii legible.
    kick_dir = normalize(v3(-0.85, 0.28, -0.55))
    kndl = np.clip((nrm * kick_dir).sum(1), 0, 1)
    kH = normalize(kick_dir + V)
    kspec = spec_k * np.power(np.clip((nrm * kH).sum(1), 0, 1), shiny) * 0.75

    rim = np.power(np.clip(1.0 - np.clip((nrm * V).sum(1), 0, 1), 0, 1), 3.0) * 0.40
    amb = 0.30 + 0.22 * np.clip(nrm[:, 1], 0, 1)

    lit = albedo * (ndl[:, None] * 1.55 + fill[:, None] + kndl[:, None] * 0.55 + amb[:, None]) \
        + spec[:, None] * np.array([1.0, 0.99, 0.96], np.float32) \
        + kspec[:, None] * np.array([0.82, 0.87, 1.0], np.float32) \
        + rim[:, None] * np.array([0.60, 0.67, 0.80], np.float32)

    col[m] = lit
    return col, idbuf, t


def _sample_bump(tex, u, v):
    """Bilinear-ish sample plus a forward-difference gradient for the bump term."""
    hgt, wid = tex.shape
    xi = np.clip((u * (wid - 1)).astype(np.int32), 0, wid - 1)
    yi = np.clip((v * (hgt - 1)).astype(np.int32), 0, hgt - 1)
    s = tex[yi, xi]
    xr = np.clip(xi + 1, 0, wid - 1)
    yr = np.clip(yi + 1, 0, hgt - 1)
    gx = tex[yi, xr] - s
    gy = tex[yr, xi] - s
    return s, (gx, gy)


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------

def render(boxes, ids, cam_pos, cam_at, fov_deg=27.0, opts=None):
    opts = opts or {}
    opts.setdefault("light", v3(0.55, 0.92, 0.62))
    opts.setdefault("bg_top", np.array([0.985, 0.988, 0.995], np.float32))
    opts.setdefault("bg_bot", np.array([0.895, 0.905, 0.925], np.float32))
    opts.setdefault("light_jitter", [
        v3(0, 0, 0), v3(0.10, 0, 0.06), v3(-0.09, 0.04, 0.05),
        v3(0.03, -0.07, -0.09), v3(-0.05, -0.05, 0.10),
    ])

    fwd = normalize(cam_at - cam_pos)
    right = normalize(np.cross(fwd, v3(0, 1, 0)))
    up = np.cross(right, fwd)
    aspect = W / H
    th = math.tan(math.radians(fov_deg) / 2.0)

    colbuf = np.zeros((H, W, 3), dtype=np.float32)
    idbuf = np.full((H, W), -1, dtype=np.int16)

    for y0 in range(0, H, TILE_ROWS):
        y1 = min(H, y0 + TILE_ROWS)
        ys, xs = np.mgrid[y0:y1, 0:W]
        px = (2.0 * (xs + 0.5) / W - 1.0) * th * aspect
        py = (1.0 - 2.0 * (ys + 0.5) / H) * th
        d = normalize(fwd + right * px.reshape(-1, 1) + up * py.reshape(-1, 1))
        o = np.broadcast_to(cam_pos, d.shape).copy()
        c, ib, _ = shade(o, d, boxes, ids, opts)
        colbuf[y0:y1] = c.reshape(y1 - y0, W, 3)
        idbuf[y0:y1] = ib.reshape(y1 - y0, W)
        print(f"    rows {y1}/{H}", end="\r", file=sys.stderr)
    print(file=sys.stderr)
    return colbuf, idbuf


def tonemap(c):
    """Reinhard shoulder, normalised so that 1.0 still maps to 1.0.

    Without the (1+k) factor the curve pulls a near-white studio background down to
    mid-grey, which is exactly what it did on the first pass.
    """
    k = 0.55
    c = np.clip(c, 0, None)
    c = c * (1.0 + k) / (1.0 + c * k)
    return np.power(np.clip(c, 0, 1), 1.0 / 2.2)


def downsample(a, k):
    h, w = a.shape[:2]
    a = a[:h - h % k, :w - w % k]
    if a.ndim == 3:
        return a.reshape(h // k, k, w // k, k, a.shape[2]).mean(axis=(1, 3))
    return a.reshape(h // k, k, w // k, k).mean(axis=(1, 3))


def downsample_ids(a, k):
    """IDs must not be averaged — take the majority within each block."""
    h, w = a.shape
    a = a[:h - h % k, :w - w % k]
    blocks = a.reshape(h // k, k, w // k, k).transpose(0, 2, 1, 3).reshape(h // k, w // k, k * k)
    out = np.full((h // k, w // k), -1, dtype=np.int16)
    for val in np.unique(a):
        cnt = (blocks == val).sum(axis=2)
        out = np.where(cnt > (k * k) // 2, val, out)
    return out


# ---------------------------------------------------------------------------
# ID buffer -> SVG overlay
#
# This is the step that makes the whole approach worth it: the highlight shapes
# are contour-traced from the same render they overlay, so they cannot drift.
# ---------------------------------------------------------------------------

def trace_mask(mask):
    """Moore-neighbourhood boundary trace of the largest connected blob."""
    from collections import deque
    h, w = mask.shape
    lab = np.zeros((h, w), dtype=np.int32)
    cur, sizes = 0, {}
    for sy in range(h):
        row = mask[sy]
        if not row.any():
            continue
        for sx in np.flatnonzero(row):
            if lab[sy, sx] or not mask[sy, sx]:
                continue
            cur += 1
            q = deque([(sy, sx)])
            lab[sy, sx] = cur
            cnt = 0
            while q:
                cy, cx = q.popleft()
                cnt += 1
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not lab[ny, nx]:
                        lab[ny, nx] = cur
                        q.append((ny, nx))
            sizes[cur] = cnt
    if not sizes:
        return []
    keep = max(sizes, key=sizes.get)
    m = lab == keep

    start = None
    for yy in range(h):
        xs = np.flatnonzero(m[yy])
        if xs.size:
            start = (yy, int(xs[0]))
            break
    if start is None:
        return []

    nb = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]
    contour = [start]
    cy, cx = start
    bdir = 6
    guard = 0
    while guard < 400000:
        guard += 1
        found = False
        for s in range(8):
            k = (bdir + 1 + s) % 8
            ny, nx = cy + nb[k][0], cx + nb[k][1]
            if 0 <= ny < h and 0 <= nx < w and m[ny, nx]:
                bdir = (k + 4) % 8
                cy, cx = ny, nx
                contour.append((cy, cx))
                found = True
                break
        if not found:
            break
        if (cy, cx) == start and len(contour) > 3:
            break
    return contour


def rdp(pts, eps):
    """Douglas-Peucker, so the emitted paths are tens of points, not thousands."""
    if len(pts) < 3:
        return pts
    a, b = np.array(pts[0], float), np.array(pts[-1], float)
    ab = b - a
    L = np.hypot(*ab)
    if L < 1e-9:
        d = np.hypot(*(np.array(pts, float) - a).T)
    else:
        P = np.array(pts, float)
        d = np.abs(np.cross(np.broadcast_to(ab, P.shape), P - a)) / L
    i = int(np.argmax(d))
    if d[i] > eps:
        return rdp(pts[:i + 1], eps)[:-1] + rdp(pts[i:], eps)
    return [pts[0], pts[-1]]


def ids_to_svg(idbuf, ids, out_w, out_h, meta, path, eps=1.15):
    h, w = idbuf.shape
    sx, sy = out_w / w, out_h / h
    parts = []
    for i, pid in enumerate(ids):
        mask = idbuf == i
        if mask.sum() < 40:
            continue
        c = trace_mask(mask)
        if len(c) < 8:
            continue
        c = rdp(c, eps)
        pts = " ".join(f"{x * sx:.1f},{y * sy:.1f}" for (y, x) in c)
        parts.append((pid, pts, int(mask.sum())))

    with open(path, "w", encoding="utf-8") as f:
        f.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {out_w} {out_h}"\n')
        f.write(f'     data-asset="{meta["asset_id"]}" data-curriculum-node="{meta["curriculum_node"]}"\n')
        f.write(f'     data-view="{meta["view"]}">\n')
        f.write(f'  <title>{meta["title_en"]}</title>\n')
        f.write('  <desc>Contour-traced from the render ID buffer by asset_gen/render3d.py.\n')
        f.write('        Geometry is pixel-exact against the PNG of the same view by construction.</desc>\n')
        for pid, pts, area in parts:
            f.write(f'  <polygon class="part" id="{pid}" data-area="{area}" points="{pts}"/>\n')
        f.write('</svg>\n')
    return [p[0] for p in parts]


# ---------------------------------------------------------------------------
# Bottom-view footprint diagram.
#
# Not decoration: Type A and Type B differ only here, and fitting the wrong one
# damages the circuit. It is drawn to scale from the same FOOTPRINT constants the
# 3-D model uses, so the two views cannot disagree.
# ---------------------------------------------------------------------------

def render_footprint(path_png, path_svg, meta):
    w, h = 640, 512
    im = Image.new("RGB", (w * 2, h * 2), (243, 244, 247))
    d = ImageDraw.Draw(im)
    S = 2
    cx, cy = w, h
    mm = 13.0 * S

    d.rounded_rectangle([cx - BODY_W / 2 * mm, cy - BODY_D / 2 * mm,
                         cx + BODY_W / 2 * mm, cy + BODY_D / 2 * mm],
                        radius=10 * S, fill=(28, 30, 34), outline=(70, 74, 80), width=3 * S)

    f = _font(26 * S, bold=True)
    fsm = _font(15 * S)
    shapes = {}
    for pid, (x, z, bw) in FOOTPRINT.items():
        px = cx + x * mm
        py = cy - z * mm
        bw_px = bw * mm
        bt_px = max(6 * S, BLADE_T * mm * 3.2)
        box = [px - bw_px / 2, py - bt_px / 2, px + bw_px / 2, py + bt_px / 2]
        d.rectangle(box, fill=(206, 210, 216), outline=(120, 126, 134), width=2 * S)
        shapes[pid] = box
        num = pid.split("_")[1]
        d.text((px, py + (34 * S if z > 0 else -34 * S)), num, font=f,
               fill=(235, 238, 242), anchor="mm")
        d.text((px, py + (58 * S if z > 0 else -58 * S)), f"{bw:g} mm", font=fsm,
               fill=(150, 156, 165), anchor="mm")

    ft = _font(20 * S, bold=True)
    d.text((cx, 44 * S), "BOTTOM VIEW  ·  ISO MINI, TYPE B", font=ft, fill=(60, 66, 74), anchor="mm")
    d.text((cx, h * 2 - 46 * S),
           "coil blades 4.8 mm  ·  load blades 6.3 mm  ·  thickness 0.8 mm",
           font=fsm, fill=(110, 116, 126), anchor="mm")
    im.resize((w, h), Image.LANCZOS).save(path_png)

    with open(path_svg, "w", encoding="utf-8") as fh:
        fh.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}"\n')
        fh.write(f'     data-asset="{meta["asset_id"]}" data-view="footprint">\n')
        fh.write(f'  <title>{meta["title_en"]} — bottom view footprint</title>\n')
        for pid, b in shapes.items():
            x0, y0, x1, y1 = [v / 2 for v in b]
            pad = 6
            fh.write(f'  <rect class="part" id="{pid}" x="{x0-pad:.1f}" y="{y0-pad:.1f}" '
                     f'width="{x1-x0+2*pad:.1f}" height="{y1-y0+2*pad:.1f}" rx="3"/>\n')
        fh.write('</svg>\n')


# ---------------------------------------------------------------------------

def orbit(dist, elev_deg, azim_deg, centre_y):
    """Camera position on a sphere around the part. Azimuth is what the turntable
    steps through; elevation stays fixed so the part does not bob as it spins."""
    e, a = math.radians(elev_deg), math.radians(azim_deg)
    return v3(dist * math.cos(e) * math.sin(a),
              dist * math.sin(e) + centre_y,
              dist * math.cos(e) * math.cos(a))


def render_turntable(name, cutaway, frames, dist, elev, centre_y, out_dir,
                     width, height, ss, quality=88):
    """Render a full rotation, one traced overlay per frame.

    This is what makes the asset genuinely inspectable: the trainee drags to spin
    the component, and because every frame carries its own ID buffer, the part
    highlighting is pixel-exact at EVERY angle rather than only at the one the
    asset was authored from. A real-time WebGL viewer would give the same rotation
    but would throw away the ray-traced shading, and would need a mesh and a picking
    pass to recover the part addressing we already get for free.
    """
    global W, H, SS
    W, H, SS = width * ss, height * ss, ss

    manifest = {"asset_id": "relay_4pin", "view": name, "frames": [],
                "frame_count": frames, "elevation_deg": elev,
                "azimuth_step_deg": 360.0 / frames,
                "width": width, "height": height}

    boxes = build_scene(cutaway=cutaway)
    ids = sorted({b["id"] for b in boxes})

    for i in range(frames):
        az = 360.0 * i / frames
        cam = orbit(dist, elev, az, centre_y)
        print(f"[turntable:{name}] frame {i + 1}/{frames}  az={az:.0f}deg",
              file=sys.stderr)
        col, idb = render(boxes, ids, cam, v3(0.0, centre_y, 0.0), opts={})

        rgb = tonemap(downsample(col, ss))
        jpg = os.path.join(out_dir, f"relay_4pin_{name}_{i:02d}.jpg")
        Image.fromarray((rgb * 255).astype(np.uint8)).save(
            jpg, quality=quality, optimize=True, progressive=True)

        idl = downsample_ids(idb, ss)
        svg = os.path.join(out_dir, f"relay_4pin_{name}_{i:02d}.svg")
        got = ids_to_svg(idl, ids, width, height,
                         {"asset_id": "relay_4pin",
                          "curriculum_node": "AE/devices/relay",
                          "title_en": "4-pin ISO mini relay (Type B)",
                          "view": f"{name}@{az:.0f}"}, svg)
        manifest["frames"].append({
            "index": i, "azimuth_deg": round(az, 2),
            "jpg": os.path.basename(jpg), "svg": os.path.basename(svg),
            "parts": got,
        })

    path = os.path.join(out_dir, f"relay_4pin_{name}_turntable.json")
    with open(path, "w") as fh:
        json.dump(manifest, fh, indent=2)

    # Which parts are addressable from at least one angle, and from how many.
    tally = {}
    for fr in manifest["frames"]:
        for p in fr["parts"]:
            tally[p] = tally.get(p, 0) + 1
    print(f"[turntable:{name}] part visibility across {frames} frames: {tally}",
          file=sys.stderr)
    return manifest


def main_turntable():
    global TEX_TOP, TEX_FRONT
    os.makedirs(OUT, exist_ok=True)
    TEX_TOP = make_top_texture()
    TEX_FRONT = make_front_texture()

    frames = int(os.environ.get("TT_FRAMES", 24))
    width = int(os.environ.get("TT_W", 640))
    height = int(os.environ.get("TT_H", 512))
    ss = int(os.environ.get("TT_SS", 2))

    for name, cut in (("exterior", False), ("cutaway", True)):
        render_turntable(name, cut, frames, dist=112.0, elev=14.0, centre_y=7.0,
                         out_dir=OUT, width=width, height=height, ss=ss)
    print("[turntable] done", file=sys.stderr)


def main():
    global TEX_TOP, TEX_FRONT
    os.makedirs(OUT, exist_ok=True)
    TEX_TOP = make_top_texture()
    TEX_FRONT = make_front_texture()

    meta = {
        "asset_id": "relay_4pin",
        "curriculum_node": "AE/devices/relay",
        "title_en": "4-pin ISO mini relay (Type B)",
    }

    # Three-quarter view. Elevation is held down at 14 degrees on purpose: any
    # higher and the base plate occludes terminal 30, which is the battery feed and
    # therefore the one terminal that must never be unaddressable.
    #
    # Both views use the SAME camera so the cutaway registers pixel-for-pixel over
    # the exterior — toggling between them then reads as an x-ray of one object
    # rather than as two different pictures.
    CAM_POS = v3(55.97, 34.09, 93.16)
    CAM_AT = v3(0.0, 7.0, 0.0)
    views = [
        ("exterior", False, CAM_POS, CAM_AT),
        ("cutaway", True, CAM_POS, CAM_AT),
    ]

    written = {}
    for view, cut, cp, ca in views:
        print(f"[render] {view} at {W}x{H} …", file=sys.stderr)
        boxes = build_scene(cutaway=cut)
        ids = sorted({b["id"] for b in boxes})
        col, idb = render(boxes, ids, cp, ca, opts={})

        rgb = tonemap(downsample(col, SS))
        png = os.path.join(OUT, f"relay_4pin_{view}.png")
        Image.fromarray((rgb * 255).astype(np.uint8)).save(png, optimize=True)

        idl = downsample_ids(idb, SS)
        svg = os.path.join(OUT, f"relay_4pin_{view}.svg")
        got = ids_to_svg(idl, ids, LOGICAL_W, LOGICAL_H,
                         dict(meta, view=view), svg)
        written[view] = got
        print(f"[render] {view}: {png}  parts traced: {got}", file=sys.stderr)

    render_footprint(os.path.join(OUT, "relay_4pin_footprint.png"),
                     os.path.join(OUT, "relay_4pin_footprint.svg"), meta)
    print("[render] footprint written", file=sys.stderr)

    with open(os.path.join(OUT, "relay_4pin_views.json"), "w") as f:
        json.dump({
            "asset_id": meta["asset_id"],
            "generated_by": "asset_gen/render3d.py",
            "method": "analytic ray-traced solid model; SVG contour-traced from the render ID buffer",
            "image_model_used": False,
            "dimensions_mm": {
                "body": [BODY_W, BODY_D, BODY_H], "base_plate_h": BASE_H,
                "blade_thickness": BLADE_T, "blade_coil_w": BLADE_COIL_W,
                "blade_load_w": BLADE_LOAD_W, "blade_len": BLADE_LEN,
            },
            "footprint_type": "B",
            "sources": [
                "Picker PC785 ISO 280 relay datasheet (dimensioned drawing)",
                "DIN 46244 blade terminal sizes (4.8x0.8 coil, 6.3x0.8 load)",
                "12voltplanet automotive relay guide (Type A/B footprint)",
            ],
            "views": {k: {"png": f"relay_4pin_{k}.png", "svg": f"relay_4pin_{k}.svg",
                          "parts": v} for k, v in written.items()},
        }, f, indent=2)
    print("[render] done", file=sys.stderr)


if __name__ == "__main__":
    if "--turntable" in sys.argv:
        main_turntable()
    else:
        main()
