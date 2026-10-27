#!/usr/bin/env python3
"""
Builds the self-contained infographic demo page from real artefacts.

Nothing on the page is mocked up:
  * the infographic is the ray-traced render from asset_gen/render3d.py;
  * the highlight shapes are the contour traces from that render's ID buffer;
  * the prompt is the exact string sent to the API, block for block;
  * the JSON is the model's verbatim reply;
  * the validator verdict is the real validator's output on that reply;
  * the scores are against ground truth written before any model was run.

The one thing that is simulated is TIME: no audio is synthesised, so clause
durations are estimated from text length. That is labelled on the page.

Run:  /home/hunain/base/bin/python demo/infographic/build_demo.py
"""

import base64
import json
import os
import re
import glob

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(HERE, "VoiceStudio_Infographic_Demo.html")

# Urdu delivery rate used to estimate clause length. Labelled as an estimate on
# the page — there is no audio in this demo.
CHARS_PER_SECOND = 12.5
MIN_CLAUSE_MS = 850


def b64_png(path):
    with open(path, "rb") as fh:
        return "data:image/png;base64," + base64.b64encode(fh.read()).decode()


def parse_overlay(path):
    """Pull the traced part polygons out of a generated SVG."""
    src = open(path, encoding="utf-8").read()
    out = {}
    for m in re.finditer(r'<(polygon|rect)\s+class="part"\s+id="([^"]+)"([^/>]*)/?>', src):
        kind, pid, attrs = m.group(1), m.group(2), m.group(3)
        if kind == "polygon":
            pm = re.search(r'points="([^"]+)"', attrs)
            if not pm:
                continue
            pts = [[float(a), float(b)] for a, b in
                   (p.split(",") for p in pm.group(1).split())]
            out[pid] = {"kind": "poly", "points": pts}
        else:
            g = {k: float(v) for k, v in re.findall(r'(x|y|width|height)="([^"]+)"', attrs)}
            if len(g) == 4:
                out[pid] = {"kind": "rect", "x": g["x"], "y": g["y"],
                            "w": g["width"], "h": g["height"]}
    return out


def load_runs():
    runs = []
    for p in sorted(glob.glob(os.path.join(HERE, "runs", "battery_*.json"))):
        d = json.load(open(p, encoding="utf-8"))
        if d.get("dry_run"):
            continue
        if not any("response" in i for i in d.get("items", [])):
            continue
        runs.append(d)
    return runs


def est_ms(text):
    return max(MIN_CLAUSE_MS, int(len(text or "") / CHARS_PER_SECOND * 1000))


def main():
    aout = os.path.join(ROOT, "asset_gen", "out")

    def b64_jpg(path):
        with open(path, "rb") as fh:
            return "data:image/jpeg;base64," + base64.b64encode(fh.read()).decode()

    def turntable(view, label, note):
        """Load a rendered rotation. Every frame carries its own traced overlay,
        which is the whole point: the part highlighting stays pixel-exact at every
        angle instead of only at the one angle the asset was authored from."""
        man = json.load(open(os.path.join(aout, f"relay_4pin_{view}_turntable.json"),
                             encoding="utf-8"))
        frames = []
        for fr in man["frames"]:
            frames.append({
                "az": fr["azimuth_deg"],
                "img": b64_jpg(os.path.join(aout, fr["jpg"])),
                "parts": parse_overlay(os.path.join(aout, fr["svg"])),
            })
        return {"kind": "turntable", "label": label, "note": note,
                "frames": frames, "step_deg": man["azimuth_step_deg"]}

    views = {
        "exterior": turntable(
            "exterior", "Exterior",
            "The component as the trainee meets it on the bench. Drag to rotate. "
            "Five of the eight parts are addressable here — the other three are "
            "sealed inside the case."),
        "cutaway": turntable(
            "cutaway", "Cutaway",
            "The same object, sectioned. This is what makes the coil, the armature "
            "and the contacts addressable at all, and it is the whole argument for "
            "an overlay rather than a photograph."),
        "footprint": {
            "kind": "static",
            "img": b64_png(os.path.join(aout, "relay_4pin_footprint.png")),
            "parts": parse_overlay(os.path.join(aout, "relay_4pin_footprint.svg")),
            "label": "Footprint",
            "note": "Bottom view, drawn to scale from the same constants as the 3-D "
                    "model. Type A and Type B relays differ only here, and fitting "
                    "the wrong one damages the circuit.",
        },
    }

    partmaps = {}
    for aid in ("relay_4pin", "relay_5pin", "fuse_blade_std"):
        partmaps[aid] = json.load(open(os.path.join(aout, f"{aid}.parts.json"),
                                      encoding="utf-8"))

    lessons = {}
    for p in glob.glob(os.path.join(HERE, "content", "*.lesson.json")):
        d = json.load(open(p, encoding="utf-8"))
        lessons[d["asset_id"]] = d

    scn_doc = json.load(open(os.path.join(HERE, "scenarios.json"), encoding="utf-8"))
    runs = load_runs()

    # Attach estimated clause timing to every parsed reply.
    for r in runs:
        for it in r["items"]:
            obj = it.get("parsed")
            if not obj:
                continue
            for cl in obj.get("speech", []) or []:
                if isinstance(cl, dict):
                    cl["_est_ms"] = est_ms(cl.get("text"))

    data = {
        "views": views,
        "partmaps": partmaps,
        "lessons": lessons,
        "scenarios": scn_doc,
        "runs": runs,
        "timing": {"chars_per_second": CHARS_PER_SECOND, "min_clause_ms": MIN_CLAUSE_MS},
        "retrieved": json.load(open(os.path.join(HERE, "content", "retrieved_stub.json"),
                                    encoding="utf-8")),
    }

    html = PAGE.replace("/*__DATA__*/", json.dumps(data, ensure_ascii=False))
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"wrote {OUT}  ({os.path.getsize(OUT) / 1024 / 1024:.2f} MB)")
    print(f"  runs embedded: {[r['model'] for r in runs]}")
    for r in runs:
        n = sum(1 for i in r["items"] if "response" in i)
        print(f"    {r['model']}: {n} answered")


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Part-Addressable Infographics</title>
<style>
:root{
  --bg:#f6f7f9; --panel:#ffffff; --ink:#12161c; --muted:#5c6672; --line:#e2e6ec;
  --accent:#0f6f68; --accent-soft:#e6f2f1;
  --amber:#b26a00; --amber-soft:#fdf0dc;
  --red:#b0263c; --red-soft:#fdeaed;
  --blue:#1f5fa8; --blue-soft:#e8f0fa;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --urdu:"Noto Nastaliq Urdu","Jameel Noori Nastaleeq","Noto Naskh Arabic",serif;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --bg:#0e1116; --panel:#161b22; --ink:#e7ecf2; --muted:#9aa6b4; --line:#262d38;
    --accent:#4fd1c5; --accent-soft:#10312f;
    --amber:#e8a33d; --amber-soft:#33260f;
    --red:#f2748c; --red-soft:#3a1a20;
    --blue:#7fb2ee; --blue-soft:#15243a;
  }
}
:root[data-theme="dark"]{
  --bg:#0e1116; --panel:#161b22; --ink:#e7ecf2; --muted:#9aa6b4; --line:#262d38;
  --accent:#4fd1c5; --accent-soft:#10312f;
  --amber:#e8a33d; --amber-soft:#33260f;
  --red:#f2748c; --red-soft:#3a1a20;
  --blue:#7fb2ee; --blue-soft:#15243a;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
  line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:0 20px 90px}
h1,h2,h3{line-height:1.25;margin:0}
h1{font-size:clamp(28px,4.4vw,44px);letter-spacing:-.022em;font-weight:680}
h2{font-size:clamp(20px,2.6vw,27px);letter-spacing:-.016em;font-weight:660}
h3{font-size:15px;font-weight:660;letter-spacing:-.005em}
p{margin:.7em 0}
code,.mono{font-family:var(--mono);font-size:.88em}
a{color:var(--accent)}

header{padding:56px 0 34px;border-bottom:1px solid var(--line);margin-bottom:38px}
.eyebrow{font-family:var(--mono);font-size:11.5px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--accent);font-weight:600}
.lede{font-size:17.5px;color:var(--muted);max-width:74ch;margin-top:16px}
.meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:22px}
.tag{font-family:var(--mono);font-size:11px;padding:4px 9px;border-radius:5px;
  background:var(--panel);border:1px solid var(--line);color:var(--muted)}

section{margin:46px 0}
.shead{display:flex;align-items:baseline;gap:13px;margin-bottom:7px;flex-wrap:wrap}
.snum{font-family:var(--mono);font-size:12px;color:var(--accent);font-weight:700;
  border:1px solid var(--accent);border-radius:4px;padding:2px 7px;flex:none}
.sdesc{color:var(--muted);max-width:76ch;margin-bottom:22px}

.panel{background:var(--panel);border:1px solid var(--line);border-radius:11px}
.pad{padding:20px}

.pipeline{display:flex;align-items:stretch;gap:0;flex-wrap:wrap;
  border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--panel)}
.pstage{flex:1 1 118px;padding:13px 12px;border-right:1px solid var(--line);min-width:118px}
.pstage:last-child{border-right:0}
.pstage.on{background:var(--accent-soft)}
.pstage b{display:block;font-size:12.5px}
.pstage span{font-size:11px;color:var(--muted);font-family:var(--mono)}

.grid2{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:20px}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}

.stage{position:relative;background:var(--panel);border:1px solid var(--line);
  border-radius:11px;overflow:hidden}
.stage img{display:block;width:100%;height:auto;user-select:none;-webkit-user-drag:none}
.stage.spin{cursor:grab}
.stage.spin:active{cursor:grabbing}
.stage svg{position:absolute;inset:0;width:100%;height:100%}
.part{fill:transparent;stroke:transparent;stroke-width:2.5;
  transition:fill .16s ease,stroke .16s ease;cursor:pointer;vector-effect:non-scaling-stroke}
.part:hover{fill:rgba(15,111,104,.20);stroke:var(--accent)}
.part.sel{fill:rgba(15,111,104,.30);stroke:var(--accent)}
.part.live{fill:rgba(232,163,61,.42);stroke:#e8a33d;stroke-width:3.4}
.part.dim{fill:transparent;stroke:transparent}

.viewbtns{display:flex;gap:7px;margin-bottom:13px;flex-wrap:wrap}
.btn{font-family:var(--mono);font-size:11.5px;padding:6px 12px;border-radius:6px;
  border:1px solid var(--line);background:var(--panel);color:var(--muted);cursor:pointer}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn.on{background:var(--accent);border-color:var(--accent);color:#fff}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) .btn.on{color:#08110f}}
:root[data-theme="dark"] .btn.on{color:#08110f}

table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);
  vertical-align:top}
th{font-family:var(--mono);font-size:10.5px;text-transform:uppercase;
  letter-spacing:.08em;color:var(--muted);font-weight:600}
tbody tr:hover{background:var(--accent-soft)}
tbody tr.sel{background:var(--accent-soft)}
.scroll{overflow-x:auto}
.ur{font-family:var(--urdu);direction:rtl;text-align:right;font-size:15px;line-height:2.1}

.promptbox{background:var(--panel);border:1px solid var(--line);border-radius:11px;
  overflow:hidden}
.pblock{border-bottom:1px solid var(--line)}
.pblock:last-child{border-bottom:0}
.pbhead{display:flex;align-items:center;gap:10px;padding:9px 15px;cursor:pointer;
  user-select:none;font-family:var(--mono);font-size:11.5px;font-weight:600}
.pbhead:hover{background:var(--accent-soft)}
.pbhead .cnt{margin-left:auto;color:var(--muted);font-weight:400;font-size:10.5px}
.pbhead .caret{color:var(--muted);transition:transform .15s;flex:none}
.pblock.open .caret{transform:rotate(90deg)}
.pbbody{display:none;padding:0 15px 15px;font-family:var(--mono);font-size:11.7px;
  white-space:pre-wrap;word-break:break-word;line-height:1.65;color:var(--ink)}
.pblock.open .pbbody{display:block}
.swatch{width:9px;height:9px;border-radius:2px;flex:none}

.json{font-family:var(--mono);font-size:11.8px;white-space:pre-wrap;line-height:1.6;
  background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:16px;
  overflow-x:auto}
.jk{color:var(--blue)} .js{color:var(--accent)} .jn{color:var(--amber)}
.jb{color:var(--red)}

.checks{display:grid;grid-template-columns:repeat(auto-fit,minmax(178px,1fr));gap:9px}
.check{border:1px solid var(--line);border-radius:8px;padding:10px 12px;background:var(--panel)}
.check .lab{font-family:var(--mono);font-size:10px;text-transform:uppercase;
  letter-spacing:.07em;color:var(--muted)}
.check .val{font-weight:660;font-size:14px;margin-top:3px}
.ok{color:var(--accent)} .bad{color:var(--red)} .warn{color:var(--amber)}
.check.ok{background:var(--accent-soft);border-color:var(--accent)}
.check.bad{background:var(--red-soft);border-color:var(--red)}
.check.warn{background:var(--amber-soft);border-color:var(--amber)}

.clauses{display:flex;flex-direction:column;gap:7px}
.clause{border:1px solid var(--line);border-radius:8px;padding:10px 13px;
  background:var(--panel);transition:border-color .15s,background .15s}
.clause.live{border-color:var(--amber);background:var(--amber-soft)}
.clause.done{opacity:.5}
.clause .tag{font-family:var(--mono);font-size:10px;color:var(--muted);
  display:flex;justify-content:space-between;gap:10px;margin-bottom:4px}
.clause .tag b{color:var(--amber)}

.transport{display:flex;align-items:center;gap:11px;margin:13px 0;flex-wrap:wrap}
.bar{flex:1;min-width:150px;height:5px;background:var(--line);border-radius:3px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--amber);width:0}

.callout{border-left:3px solid var(--accent);background:var(--accent-soft);
  padding:13px 17px;border-radius:0 8px 8px 0;margin:18px 0}
.callout.warn{border-color:var(--amber);background:var(--amber-soft)}
.callout.bad{border-color:var(--red);background:var(--red-soft)}
.callout p:first-child{margin-top:0}.callout p:last-child{margin-bottom:0}

.scnlist{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px}
.scn{font-family:var(--mono);font-size:11px;padding:5px 10px;border-radius:6px;
  border:1px solid var(--line);background:var(--panel);cursor:pointer;color:var(--muted)}
.scn:hover{border-color:var(--accent);color:var(--accent)}
.scn.on{background:var(--accent);border-color:var(--accent);color:#fff}
.scn.miss{opacity:.42;cursor:not-allowed}
.foot{color:var(--muted);font-size:12.5px;border-top:1px solid var(--line);
  padding-top:22px;margin-top:46px}
.kv{font-family:var(--mono);font-size:11.5px;color:var(--muted)}
.pill{display:inline-block;font-family:var(--mono);font-size:10px;padding:2px 7px;
  border-radius:99px;border:1px solid var(--line);color:var(--muted)}
.pill.ok{color:var(--accent);border-color:var(--accent)}
.pill.bad{color:var(--red);border-color:var(--red)}
</style>
</head>
<body>
<div class="wrap">

<header>
  <div class="eyebrow">VoiceStudio · Stage 1 · visual channel</div>
  <h1>Part-addressable infographics</h1>
  <p class="lede">
    How the tutor turns one student utterance into a spoken answer <em>and</em> a
    synchronised sequence of highlights on a picture of the component — with the
    whole prompt on show, the model's raw JSON on show, and the validator that
    stands between them on show.
  </p>
  <div class="meta">
    <span class="tag">real render, no image model</span>
    <span class="tag">real corpus documents</span>
    <span class="tag">real API calls</span>
    <span class="tag">ground truth written before the run</span>
    <span class="tag">no audio — timings are estimated</span>
  </div>
</header>

<div class="pipeline" id="pipeline"></div>

<section>
  <div class="shead"><span class="snum">01</span><h2>The asset</h2></div>
  <p class="sdesc">
    A 4-pin ISO mini relay, generated by a ray-tracer from a written specification —
    body <span class="mono">25.5 mm</span>, blades <span class="mono">0.8 mm</span> thick,
    coil terminals <span class="mono">4.8 mm</span> wide and load terminals
    <span class="mono">6.3 mm</span>, per DIN 46244. No image-generation model was
    used anywhere. Click any part.
  </p>
  <div class="viewbtns" id="viewbtns"></div>
  <div class="grid2">
    <div>
      <div class="stage" id="stage"></div>
      <div class="transport" style="margin-top:10px">
        <button class="btn" id="spinl">&#8592; rotate</button>
        <button class="btn" id="spinr">rotate &#8594;</button>
        <span class="kv" id="angle"></span>
        <span class="kv" style="margin-left:auto">drag the part to turn it</span>
      </div>
      <p class="kv" id="viewnote" style="margin-top:6px"></p>
    </div>
    <div>
      <div class="panel pad">
        <h3 style="margin-bottom:10px">Part map — the single source of truth</h3>
        <div class="scroll"><table id="partmap"></table></div>
      </div>
      <div class="callout" style="margin-bottom:0">
        <p><b>Why three forms per part.</b> The script contract spells numbers out as
        Urdu words before synthesis, because digits break the speech synthesiser. So
        the tutor says <span class="ur">پچاسی</span> while the id is
        <code>terminal_85</code> and the moulding reads <code>85</code>. Store one
        form and the link between what is said and what is lit silently rots.</p>
      </div>
    </div>
  </div>
  <div class="callout warn">
    <p><b>This is the argument for an overlay instead of a photograph.</b>
    <span id="visstat"></span> The coil, the armature and the contacts are sealed
    inside the case. A photograph of a real relay cannot point at any of them, so
    every lesson clause about how a relay actually works would have nothing to
    highlight.</p>
  </div>
</section>

<section>
  <div class="shead"><span class="snum">02</span><h2>The prompt</h2></div>
  <p class="sdesc">
    Ten student turns, each a different way of referring to a part. Pick one and read
    the exact string that went to the model — nothing here is paraphrased for the slide.
  </p>
  <div class="scnlist" id="scnlist"></div>
  <div class="grid2" style="margin-bottom:18px">
    <div class="panel pad">
      <h3>Student turn — as the speech recogniser delivers it</h3>
      <p class="ur" id="asrtext" style="margin:10px 0"></p>
      <div class="kv" id="asrmeta"></div>
      <p class="kv" id="asrnote" style="margin-top:8px"></p>
    </div>
    <div class="panel pad">
      <h3>What we decided the right answer was — before running anything</h3>
      <div class="kv" id="expect" style="margin-top:8px"></div>
    </div>
  </div>
  <div class="promptbox" id="prompt"></div>
</section>

<section>
  <div class="shead"><span class="snum">03</span><h2>The reply, and the gate it has to pass</h2></div>
  <p class="sdesc">
    The model answers with one JSON object. Our side parses it, checks it against the
    asset's closed part vocabulary and the script contract, and only then lets it near
    the screen. A part id that is not in the vocabulary never reaches the UI.
  </p>
  <div class="viewbtns" id="modelbtns"></div>
  <div class="grid2">
    <div><div class="json" id="raw"></div></div>
    <div>
      <div class="checks" id="checks"></div>
      <div class="callout warn" id="repair" style="display:none"></div>
      <div class="panel pad" style="margin-top:14px">
        <h3 style="margin-bottom:8px">Resolution</h3>
        <p class="kv" id="resolved"></p>
      </div>
    </div>
  </div>
</section>

<section>
  <div class="shead"><span class="snum">04</span><h2>Playback</h2></div>
  <p class="sdesc">
    The parsed clauses run in order. Each one lights its part for the length of that
    clause, and <code>goto</code> moves the picture before the first clause begins.
    There is no audio: clause durations are <b>estimated</b> at
    <span id="rate" class="mono"></span> characters per second of Urdu delivery.
  </p>
  <div class="grid2">
    <div>
      <div class="stage" id="stage2"></div>
      <div class="transport">
        <button class="btn on" id="play">▶ Play</button>
        <button class="btn" id="reset">Reset</button>
        <div class="bar"><i id="prog"></i></div>
        <span class="kv" id="clock">0.0 s</span>
      </div>
    </div>
    <div class="clauses" id="clauses"></div>
  </div>
  <div class="callout">
    <p><b>Why the highlight boundary is the clause boundary.</b> The synthesiser has no
    streaming support upstream, so making the tutor feel responsive already requires
    chunking synthesis at clause level behind a playback queue. The highlight events
    ride on chunk boundaries that have to exist anyway — no forced alignment, no
    word-level timestamps, no vision model at runtime.</p>
  </div>
</section>

<div class="foot" id="foot"></div>
</div>

<script>
const D = /*__DATA__*/;
const $ = s => document.querySelector(s);
const el = (t, c, x) => { const n = document.createElement(t); if (c) n.className = c;
  if (x !== undefined) n.textContent = x; return n; };

/* ---------- pipeline strip ---------- */
const STAGES = [["Microphone","audio in"],["VAD","endpointing"],["ASR","mixed script"],
  ["LLM + content","JSON out"],["Validator","closed vocab"],["TTS","clause queue"],
  ["Screen","highlight"]];
STAGES.forEach((s,i)=>{const d=el("div","pstage"+((i===3||i===4||i===6)?" on":""));
  d.appendChild(el("b",null,s[0])); d.appendChild(el("span",null,s[1]));
  $("#pipeline").appendChild(d);});

/* ---------- asset explorer ---------- */
let curView = "exterior", selPart = null;

// Frame index per stage host, so the two viewers can be spun independently.
const frameOf = {};

function frameCount(view){
  const v = D.views[view];
  return v.kind === "turntable" ? v.frames.length : 1;
}
function frameData(view, i){
  const v = D.views[view];
  return v.kind === "turntable" ? v.frames[((i % v.frames.length) + v.frames.length) % v.frames.length]
                                : {img: v.img, parts: v.parts, az: 0};
}

/* A stage is an <img> plus one <svg> overlay. Rotating swaps the image and
   rewrites the overlay geometry from that frame's own contour trace, so the
   highlight stays registered at every angle. */
function drawStage(host, view, opts){
  opts = opts || {};
  const v = D.views[view];
  host.innerHTML = "";

  const img = new Image();
  img.alt = "4-pin ISO mini relay, " + v.label;
  img.draggable = false;
  host.appendChild(img);

  const svg = document.createElementNS("http://www.w3.org/2000/svg","svg");
  svg.setAttribute("viewBox","0 0 640 512");
  svg.setAttribute("preserveAspectRatio","none");
  host.appendChild(svg);

  // One node per part id in the whole vocabulary; per frame we either give it
  // geometry or hide it.
  const nodes = {};
  for (const p of D.partmaps.relay_4pin.parts){
    const n = document.createElementNS("http://www.w3.org/2000/svg","polygon");
    n.setAttribute("class","part"); n.dataset.part = p.id;
    const t = document.createElementNS("http://www.w3.org/2000/svg","title");
    t.textContent = p.id; n.appendChild(t);
    if (!opts.noclick) n.addEventListener("click", ()=>{ selPart = p.id; paintSel(host); syncPartMap(); });
    svg.appendChild(n); nodes[p.id] = n;
  }

  const st = {host, view, img, svg, nodes, frame: opts.frame || 0};
  setFrame(st, st.frame);

  if (v.kind === "turntable" && !opts.nodrag){
    host.classList.add("grab");
    let dragging=false, lastX=0, acc=0;
    const down = e => { dragging=true; lastX=(e.touches?e.touches[0]:e).clientX; acc=0;
                        host.classList.add("grabbing"); };
    const move = e => {
      if(!dragging) return;
      const x=(e.touches?e.touches[0]:e).clientX;
      acc += x-lastX; lastX=x;
      // ~14 px of drag per 15-degree step feels close to turning the real part
      const steps = Math.trunc(acc/14);
      if(steps){ acc -= steps*14; setFrame(st, st.frame - steps); }
      if(e.cancelable) e.preventDefault();
    };
    const up = () => { dragging=false; host.classList.remove("grabbing"); };
    host.addEventListener("mousedown",down); window.addEventListener("mousemove",move);
    window.addEventListener("mouseup",up);
    host.addEventListener("touchstart",down,{passive:true});
    host.addEventListener("touchmove",move,{passive:false});
    host.addEventListener("touchend",up);
  }
  return st;
}

function setFrame(st, i){
  const n = frameCount(st.view);
  st.frame = ((i % n) + n) % n;
  const fr = frameData(st.view, st.frame);
  st.img.src = fr.img;
  for (const [pid, node] of Object.entries(st.nodes)){
    const g = fr.parts[pid];
    if (!g){ node.setAttribute("points",""); node.style.display="none"; continue; }
    node.style.display="";
    if (g.kind === "poly"){
      node.setAttribute("points", g.points.map(p=>p.join(",")).join(" "));
    } else {
      node.setAttribute("points",
        [[g.x,g.y],[g.x+g.w,g.y],[g.x+g.w,g.y+g.h],[g.x,g.y+g.h]]
        .map(p=>p.join(",")).join(" "));
    }
  }
  if (st.onframe) st.onframe(st);
}

function paintSel(host){
  host.querySelectorAll(".part").forEach(n=>
    n.classList.toggle("sel", n.dataset.part===selPart));
}

function renderViewBtns(){
  const h = $("#viewbtns"); h.innerHTML="";
  for (const k of Object.keys(D.views)){
    const b = el("button","btn"+(k===curView?" on":""), D.views[k].label);
    b.onclick = ()=>{curView=k; selPart=null; render();};
    h.appendChild(b);
  }
  if (D.views[curView].type === "turntable"){
    const hint = el("span","kv","\u2194 drag the part to rotate");
    hint.style.alignSelf = "center"; hint.style.marginLeft = "6px";
    h.appendChild(hint);
  }
}

function renderPartMap(){
  const pm = D.partmaps.relay_4pin;
  const t = $("#partmap"); t.innerHTML="";
  const thead = el("thead"); const tr = el("tr");
  ["id","says","moulded","what it is","in view"].forEach(x=>tr.appendChild(el("th",null,x)));
  thead.appendChild(tr); t.appendChild(thead);
  const tb = el("tbody");
  const vv = D.views[curView];
  const fk = "stage:" + curView;
  const fidx = (frameOf[fk] || 0) % vv.frames.length;
  const inView = new Set(Object.keys(vv.frames[fidx].parts));
  pm.parts.forEach(p=>{
    const r = el("tr", p.id===selPart ? "sel":"");
    r.onclick = ()=>{ selPart = p.id; render(); };
    r.appendChild(el("td","mono",p.id));
    const u = el("td","ur",p.spoken_ur||"—"); r.appendChild(u);
    r.appendChild(el("td","mono",p.written||"—"));
    r.appendChild(el("td",null,p.label_en));
    const c = el("td"); const pill = el("span","pill "+(inView.has(p.id)?"ok":"bad"),
      inView.has(p.id)?"yes":"no"); c.appendChild(pill); r.appendChild(c);
    tb.appendChild(r);
  });
  t.appendChild(tb);
  const total = pm.parts.length, shown = pm.parts.filter(p=>inView.has(p.id)).length;
  const extN = new Set(); D.views.exterior.frames.forEach(f=>Object.keys(f.parts).forEach(p=>extN.add(p)));
  const cutN = new Set(); D.views.cutaway.frames.forEach(f=>Object.keys(f.parts).forEach(p=>cutN.add(p)));
  $("#visstat").textContent =
    `Across the whole rotation the exterior view can point at ${extN.size} of ${total} parts; the sectioned view reaches all ${cutN.size}.`;
}

let stage1 = null;
function syncPartMap(){ renderPartMap(); paintSel($("#stage")); }

function render(){
  renderViewBtns();
  stage1 = drawStage($("#stage"), curView, {frame: stage1 ? stage1.frame : 3});
  stage1.onframe = st => { $("#angle").textContent =
    "angle " + Math.round(frameData(st.view, st.frame).az) + "\u00b0"; };
  stage1.onframe(stage1);
  paintSel($("#stage"));
  $("#viewnote").textContent = D.views[curView].note;
  renderPartMap();
}

/* ---------- scenarios / prompt ---------- */
const BLOCK_COLOUR = {
  "SYSTEM":"var(--accent)",
  "VISUAL CONTEXT":"var(--blue)",
  "LESSON CONTENT (human-authored, PROV-3 applies)":"var(--amber)",
  "ASSETS SHOWN EARLIER IN THIS LESSON (still addressable)":"var(--muted)",
  "RETRIEVED DOCUMENTS (from the corpus, supporting context only)":"var(--red)",
  "SESSION STATE":"var(--muted)",
  "STUDENT TURN — speech recognition output":"var(--accent)",
  "OUTPUT CONTRACT — reply with exactly this JSON shape":"var(--blue)"
};

let curScn = null, curModel = null;

function itemsFor(model){
  const r = D.runs.find(x=>x.model===model);
  return r ? r.items : [];
}
function findItem(model, sid){
  return itemsFor(model).find(i=>i.id===sid);
}
function answeredModels(sid){
  return D.runs.filter(r=>r.items.some(i=>i.id===sid && i.response)).map(r=>r.model);
}

function renderScnList(){
  const h = $("#scnlist"); h.innerHTML="";
  D.scenarios.scenarios.forEach(s=>{
    const has = answeredModels(s.id).length>0;
    const b = el("button","scn"+(s.id===curScn?" on":"")+(has?"":" miss"),
                 s.id+" · "+s.label);
    if (has) b.onclick = ()=>{ curScn=s.id;
      const am = answeredModels(s.id);
      if (!am.includes(curModel)) curModel = am[0];
      renderScenario(); };
    h.appendChild(b);
  });
}

function scnDef(sid){ return D.scenarios.scenarios.find(s=>s.id===sid); }

function renderPrompt(item){
  const box = $("#prompt"); box.innerHTML="";
  if (!item){ box.appendChild(el("div","pad",'No prompt for this scenario.')); return; }
  item.prompt_blocks.forEach((b,i)=>{
    const body = item.prompt.slice(b.start, b.end)
                   .replace(/^\n?#+ .*? #+\n/, "");
    const w = el("div","pblock"+(i===0?"":""));
    const head = el("div","pbhead");
    const sw = el("span","swatch"); sw.style.background = BLOCK_COLOUR[b.title]||"var(--muted)";
    head.appendChild(el("span","caret","▶"));
    head.appendChild(sw);
    head.appendChild(el("span",null,b.title));
    head.appendChild(el("span","cnt", b.chars.toLocaleString()+" chars"));
    head.onclick = ()=> w.classList.toggle("open");
    w.appendChild(head);
    w.appendChild(el("div","pbbody", body.trim()));
    box.appendChild(w);
  });
  const tot = el("div","pbhead");
  tot.style.background="var(--accent-soft)";
  tot.appendChild(el("span",null,"TOTAL PROMPT"));
  const rt = item.response ? item.response.prompt_tokens : null;
  tot.appendChild(el("span","cnt", item.prompt_chars.toLocaleString()+" chars"
    + (rt ? "  ·  "+rt.toLocaleString()+" prompt tokens (measured)" : "")));
  box.appendChild(tot);
}

function syntax(obj){
  // Strip the timing fields this demo attaches; this panel is the model's reply.
  const s = JSON.stringify(obj, (k, v) => k.startsWith("_") ? undefined : v, 2);
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/"([^"]+)":/g,'<span class="jk">"$1"</span>:')
    .replace(/: "([^"]*)"/g,': <span class="js">"$1"</span>')
    .replace(/: (true|false|null)/g,': <span class="jb">$1</span>')
    .replace(/: (-?\d+\.?\d*)/g,': <span class="jn">$1</span>');
}

function renderChecks(item){
  const h = $("#checks"); h.innerHTML="";
  if (!item || !item.findings){ return; }
  const f = item.findings, sc = item.score||{};
  const add=(lab,val,cls)=>{const d=el("div","check "+(cls||""));
    d.appendChild(el("div","lab",lab)); d.appendChild(el("div","val",val)); h.appendChild(d);};
  add("Schema", f.schema_ok?"valid":"INVALID", f.schema_ok?"ok":"bad");
  add("goto emitted first", f.goto_first?"yes":"no", f.goto_first?"ok":"warn");
  add("Part id in vocabulary",
      f.invalid_part_ids.length? f.invalid_part_ids.join(", ") : "all valid",
      f.invalid_part_ids.length?"bad":"ok");
  add("Digits in speech (AC-5)", f.digit_leaks.length===0?"none":f.digit_leaks.length+" leak(s)",
      f.digit_leaks.length?"bad":"ok");
  add("Roman Urdu (banned)", f.roman_urdu.length===0?"none":f.roman_urdu.length+" token(s)",
      f.roman_urdu.length?"bad":"ok");
  add("Resolved part", (sc.got_part===null?"null":sc.got_part)+
      (sc.part_correct?"  ✓":"  ✗ want "+sc.expected_part), sc.part_correct?"ok":"bad");
  if (sc.asset_correct!==undefined)
    add("Resolved asset", sc.got_asset+(sc.asset_correct?"  ✓":"  ✗"), sc.asset_correct?"ok":"bad");
  if (sc.view_correct!==undefined)
    add("Switched view", sc.got_view+(sc.view_correct?"  ✓":"  ✗"), sc.view_correct?"ok":"warn");
  if (sc.grounded_correct!==undefined)
    add("Grounding flag", String(sc.got_grounded)+(sc.grounded_correct?"  ✓":"  ✗"),
        sc.grounded_correct?"ok":"bad");
  if (sc.avoided_invented_ids!==undefined)
    add("Refused invented id", sc.avoided_invented_ids?"yes ✓":"NO ✗",
        sc.avoided_invented_ids?"ok":"bad");
  if (sc.return_to_correct!==undefined)
    add("return_to", sc.return_to_correct?"correct ✓":"wrong ✗", sc.return_to_correct?"ok":"warn");
  const nv = (f.parts_not_in_view||[]).length;
  add("Part visible in chosen view", nv===0?"all visible":nv+" clause(s) blind",
      nv?"bad":"ok");
  add("Clauses", String(f.clause_count));
  if (item.response) add("Latency (free tier)", item.response.latency_s+" s");
  if (item.response && item.response.thinking_tokens)
    add("Thinking tokens", String(item.response.thinking_tokens), "warn");

  const rep = $("#repair");
  if (f.repair){
    rep.style.display="block";
    rep.innerHTML = "<p><b>The validator flags this reply.</b> The model tagged "+
      nv+" clause(s) to parts that do not exist in the <code>"+
      (item.parsed.goto||{}).view+"</code> view it chose, so those highlights fire "+
      "on nothing. Every referenced part is real, so a lossless repair exists — "+
      "promote the view to <code>"+f.repair.promote_view+"</code>.</p>"+
      "<p><b>Playback below runs the response as returned, unrepaired</b>, so the "+
      "failure is visible. The pipeline follows the JSON; the right place to fix "+
      "this is the prompt.</p>";
  } else { rep.style.display="none"; }
}

/* ---------- playback ---------- */
let timer=null, t0=0, playing=false, clauses=[], gotoPart=null, gotoView=null, totalMs=0;
let stage2=null;

function loadPlayback(item){
  const obj = item && item.parsed;
  clauses = (obj && obj.speech) ? obj.speech.filter(c=>c && c.text) : [];
  gotoPart = obj && obj.goto ? obj.goto.part : null;
  // Playback runs the RESPONSE, exactly as returned. The validator's repair is
  // reported as a diagnostic and is deliberately NOT applied here: the whole point
  // of a structured reply is that the pipeline follows it, so a wrong view has to
  // be visible as a wrong view. Fixing it belongs in the prompt, not in the player.
  const wantView = (obj && obj.goto) ? obj.goto.view : "exterior";
  gotoView = D.views[wantView] ? wantView : "exterior";
  totalMs = clauses.reduce((a,c)=>a+(c._est_ms||1000),0);
  stop();
  redrawPlayback();
  paintLive(gotoPart, -1);
  renderClauses(-1);
  $("#prog").style.width="0%"; $("#clock").textContent="0.0 s";
}

// Redraw the playback stage, keeping drag active and restoring whatever highlight
// was showing. Spinning mid-sentence must not drop the highlight.
let lastLivePart = null;
function redrawPlayback(){
  drawStage($("#stage2"), gotoView, {noclick:true, onSpin: () => {
    redrawPlayback();
    paintLive(lastLivePart !== null ? lastLivePart : gotoPart, -1);
  }});
}

function paintLive(part, idx){
  lastLivePart = part;
  const svg = $("#stage2").querySelector("svg"); if(!svg) return;
  svg.querySelectorAll(".part").forEach(n=>{
    n.classList.toggle("live", part && n.dataset.part===part);
  });
}

function renderClauses(active){
  const h=$("#clauses"); h.innerHTML="";
  if (!clauses.length){ h.appendChild(el("div","kv","No clauses.")); return; }
  clauses.forEach((c,i)=>{
    const d=el("div","clause"+(i===active?" live":"")+(i<active?" done":""));
    const t=el("div","tag");
    const b=el("b",null,c.part||"— no highlight —"); t.appendChild(b);
    t.appendChild(el("span",null,((c._est_ms||0)/1000).toFixed(1)+" s (est.)"));
    d.appendChild(t);
    d.appendChild(el("div","ur",c.text));
    h.appendChild(d);
  });
}

function tick(){
  const e = performance.now()-t0;
  let acc=0, active=-1;
  for(let i=0;i<clauses.length;i++){
    const d=clauses[i]._est_ms||1000;
    if(e>=acc && e<acc+d){active=i;break;}
    acc+=d;
  }
  if(e>=totalMs){ stop(); paintLive(gotoPart,-1); renderClauses(-1);
    $("#prog").style.width="100%"; $("#clock").textContent=(totalMs/1000).toFixed(1)+" s";
    $("#play").textContent="▶ Play"; return; }
  paintLive(active>=0?clauses[active].part:gotoPart, active);
  renderClauses(active);
  $("#prog").style.width=(100*e/totalMs)+"%";
  $("#clock").textContent=(e/1000).toFixed(1)+" s";
}
function stop(){ if(timer){clearInterval(timer); timer=null;} playing=false; }
$("#play").onclick=()=>{
  if(playing){ stop(); $("#play").textContent="▶ Play"; return; }
  if(!clauses.length) return;
  t0=performance.now(); playing=true; $("#play").textContent="⏸ Pause";
  timer=setInterval(tick,60);
};
$("#reset").onclick=()=>{ stop(); $("#play").textContent="▶ Play";
  paintLive(gotoPart,-1); renderClauses(-1);
  $("#prog").style.width="0%"; $("#clock").textContent="0.0 s"; };

/* ---------- scenario render ---------- */
function renderModelBtns(){
  const h=$("#modelbtns"); h.innerHTML="";
  const am = answeredModels(curScn);
  am.forEach(m=>{
    const b=el("button","btn"+(m===curModel?" on":""), m);
    b.onclick=()=>{curModel=m; renderScenario();};
    h.appendChild(b);
  });
  if(am.length<2){
    const n=el("span","kv"," · other models were not run on this scenario (quota)");
    n.style.alignSelf="center"; h.appendChild(n);
  }
}

function renderScenario(){
  renderScnList();
  const s = scnDef(curScn);
  const item = findItem(curModel, curScn);
  $("#asrtext").textContent = s.asr.transcript;
  $("#asrmeta").textContent =
    `confidence ${s.asr.confidence}  ·  ${s.asr.audio_ms} ms of audio  ·  ${s.category}`;
  $("#asrnote").textContent = s.asr.note || "";
  const e=s.expect;
  $("#expect").innerHTML =
    `<b>part</b> ${e.goto_part===null?"null (nothing to highlight)":e.goto_part}` +
    (e.goto_asset?`<br><b>asset</b> ${e.goto_asset}`:"") +
    (e.expect_view?`<br><b>view</b> ${e.expect_view}`:"") +
    (e.grounded!==undefined?`<br><b>grounded</b> ${e.grounded}`:"") +
    `<br><br>${e.notes}`;
  renderPrompt(item);
  renderModelBtns();
  $("#raw").innerHTML = item && item.parsed ? syntax(item.parsed)
    : "(no reply — this scenario was not run against this model)";
  renderChecks(item);
  $("#resolved").textContent = item && item.parsed && item.parsed.resolved_as
    ? item.parsed.resolved_as : "—";
  loadPlayback(item);
}

/* ---------- boot ---------- */
$("#rate").textContent = D.timing.chars_per_second;
render();
$("#spinl").onclick = ()=>{ if(stage1) setFrame(stage1, stage1.frame+1); };
$("#spinr").onclick = ()=>{ if(stage1) setFrame(stage1, stage1.frame-1); };
const firstAnswered = D.scenarios.scenarios.find(s=>answeredModels(s.id).length>0);
curScn = firstAnswered ? firstAnswered.id : D.scenarios.scenarios[0].id;
curModel = answeredModels(curScn)[0] || (D.runs[0]||{}).model;
renderScenario();

$("#foot").innerHTML =
  `Render: <code>asset_gen/render3d.py</code> — analytic ray-tracer over a solid model; `+
  `overlay contour-traced from the render's own ID buffer, so it cannot drift. `+
  `Prompt: <code>demo/infographic/prompt_builder.py</code>. `+
  `Battery: <code>demo/infographic/run_battery.py</code>, scenarios and ground truth in `+
  `<code>demo/infographic/scenarios.json</code>. `+
  `Retrieved documents are real chunks from <code>education_data/</code>, frozen as a `+
  `stand-in for the retriever. Lesson content is human-authored and is <b>not yet `+
  `trade-reviewed</b> — PROV-4 sign-off is outstanding.`;
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
