#!/usr/bin/env python3
"""
Assembles the exact prompt the tutor sends to the LLM for a part-addressable
infographic turn, and validates what comes back.

The prompt is built from real artefacts, not prose written for a slide:

  * the closed part vocabulary and the id / spoken_ur / written triple come from
    `asset_gen/out/relay_4pin.parts.json`, which the renderer emits;
  * the lesson content comes from `content/*.lesson.json`, which is human-authored
    and carries its own provenance block (PROV-3: the model may paraphrase these
    steps, never invent one);
  * the retrieved block comes from `content/retrieved_stub.json` — real chunks
    lifted from `education_data/`, standing in for the retriever.

Every rule in the SYSTEM block traces to a decision already taken and recorded:
the script contract (handoff §9 item 5), the deixis rule (7 Aug §5.1), goto-first
(VIS-3), the closed vocabulary and validator (VIS-6 / defect 3.3b), and the
spoken-form triple (defect 3.3a).
"""

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


def _load(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def load_asset(asset_id):
    parts = _load(os.path.join(ROOT, "asset_gen", "out", f"{asset_id}.parts.json"))
    lesson_path = os.path.join(HERE, "content", f"{asset_id}.lesson.json")
    lesson = _load(lesson_path) if os.path.exists(lesson_path) else None
    return parts, lesson


def load_retrieved():
    return _load(os.path.join(HERE, "content", "retrieved_stub.json"))["chunks"]


def view_parts_map(asset_id="relay_4pin"):
    """Which part ids are actually traceable in each rendered view.

    Read from the generated overlays, so it cannot disagree with what the renderer
    produced. Used both in the prompt (so the model can pick a view that contains
    the parts it means to talk about) and in the validator (to check that it did).
    """
    import glob as _glob
    out = {}
    pat = os.path.join(ROOT, "asset_gen", "out", f"{asset_id}_*.svg")
    for path in _glob.glob(pat):
        m = re.search(rf"{asset_id}_([a-z]+)(?:_\d+)?\.svg$", os.path.basename(path))
        if not m:
            continue
        view = m.group(1)
        ids = re.findall(r'class="part"\s+id="([^"]+)"',
                         open(path, encoding="utf-8").read())
        out.setdefault(view, set()).update(ids)
    return {k: sorted(v) for k, v in out.items()}


# ---------------------------------------------------------------------------
# The SYSTEM block
# ---------------------------------------------------------------------------

SYSTEM = """\
You are the tutor voice of VoiceStudio, teaching the Punjab TEVTA Auto Electrician
trade course to trainees in Pakistan. You speak the way a good ustaad speaks on the
shop floor: warm, direct, one idea at a time, never lecturing.

A picture of the component under discussion is on the screen in front of the student.
You cannot see it. You are given its part list in text, and you steer it by naming
part ids.

=== SCRIPT CONTRACT (binding on every character you emit as speech) ===
S1. Urdu is written in Urdu (Arabic) script. Always.
S2. English technical terms stay in Latin script, spelled as English
    (relay, coil, terminal, load, earth, switch, fuse, circuit, multimeter).
S3. Loanwords fully absorbed into Urdu are written in Urdu script (گاڑی, بیٹری).
S4. Roman Urdu is BANNED. Never write Urdu words in Latin letters.
S5. NEVER write digits. Numbers are spelled out as Urdu words, because the speech
    synthesiser breaks on digits. Use the `spoken_ur` field from the part list for
    any terminal number you say out loud.

=== GROUNDING (safety-critical, non-negotiable) ===
G1. The ordered steps, terminal assignments, torque figures and safety warnings in
    LESSON CONTENT are human-authored. You may paraphrase them, translate them,
    reorder them for teaching, and answer questions about them.
G2. You may NOT invent a step, a terminal assignment, a rating or a safety warning.
    If the answer is not in LESSON CONTENT or RETRIEVED DOCUMENTS, say plainly that
    you do not have that figure and that the trainee should check the vehicle's own
    manual. Set "grounded": false when you do this.
G3. RETRIEVED DOCUMENTS are scraped transcripts. They are supporting context, not
    authority. Where they disagree with LESSON CONTENT, LESSON CONTENT wins.
G4. Never refer to the source medium. Do not say "as shown in the video" — the
    student is listening, not watching a video.

=== THE VISUAL ===
V1. You may only name part ids that appear in PART VOCABULARY, exactly as written.
    Inventing an id is an error. If the student asks about something that is not a
    part of this component, set the clause's "part" to null and say so in speech.
V2. Emit "goto" FIRST in your JSON object, so the picture moves before your voice
    starts.
V2b. THE VIEW MUST CONTAIN THE PARTS YOU TALK ABOUT. The asset has several views and
    they show different parts — the coil, the armature and the contacts are sealed
    inside the case and exist ONLY in the cutaway view. Before you answer, look at
    AVAILABLE VIEWS below, work out every part you are going to name, and set
    "goto".view to a view that contains ALL of them. Choosing a view that does not
    contain a part you highlight means the screen highlights nothing while you talk
    about it, which is worse than not pointing at all.
V3. Break your reply into short clauses. Tag each clause with the ONE part it is
    about, or null. The screen highlights that part while that clause is spoken, so
    a clause tagged with a part must actually be talking about that part.
V4. THE DEIXIS RULE: the speech must carry the whole explanation on its own. A
    student with the screen off must still learn the same thing. You may say
    "یہاں دیکھیں" only when you also name what you are pointing at.
V5. "return_to" is where the picture should settle after this answer.

Reply with ONE JSON object and nothing else. No markdown fence, no commentary.
"""

OUTPUT_SCHEMA = """\
{
  "goto":        {"asset": "<asset_id>", "view": "<exterior|cutaway|footprint>", "part": "<part_id or null>"},
  "resolved_as": "<one short English sentence: which part you decided the student meant, and why>",
  "grounded":    true | false,
  "speech": [
    {"part": "<part_id or null>", "text": "<one clause, script contract applies>"}
  ],
  "return_to":   {"asset": "<asset_id>", "view": "<view>", "part": "<part_id or null>"},
  "used_chunks": ["<chunk_id you actually relied on>"]
}
"""


def render_part_table(parts, view_visibility=None):
    rows = []
    for p in parts["parts"]:
        vis = "yes" if p.get("visible_in_photo") else "no (inside the sealed case)"
        if view_visibility:
            vis = "yes" if p["id"] in view_visibility else "no (not in this view)"
        rows.append(
            f'  {p["id"]:<14} | written: {p["written"] or "—":<4} | '
            f'say: {p["spoken_ur"] or "—":<10} | {p["label_en"]:<26} | '
            f'{p["label_ur"] or "—":<22} | visible: {vis}'
        )
    return "\n".join(rows)


def render_lesson(lesson, procedure_id=None):
    if not lesson:
        return "(no authored lesson content for this asset)"
    out = [f'OVERVIEW (en): {lesson["overview"]["en"]}',
           f'OVERVIEW (ur): {lesson["overview"]["ur"]}',
           "",
           "PER-PART NOTES:"]
    for pid, n in lesson["part_notes"].items():
        out.append(f'  [{pid}] {n["en"]}')
        out.append(f'         {n["ur"]}')
    for proc in lesson.get("procedures", []):
        if procedure_id and proc["procedure_id"] != procedure_id:
            continue
        out.append("")
        out.append(f'PROCEDURE «{proc["procedure_id"]}» — {proc["title_en"]}'
                   f'{"   [SAFETY-CRITICAL]" if proc.get("safety_critical") else ""}')
        for s in proc["steps"]:
            tag = "  <<SAFETY STEP>>" if s.get("is_safety_step") else ""
            out.append(f'  step {s["n"]} [{s["part"]}]{tag}')
            out.append(f'         en: {s["en"]}')
            out.append(f'         ur: {s["ur"]}')
    if lesson.get("common_faults"):
        out.append("")
        out.append("COMMON FAULTS:")
        for f in lesson["common_faults"]:
            out.append(f'  - {f["en"]}')
    return "\n".join(out)


def render_views(vp):
    if not vp:
        return "  (single view)"
    order = ["exterior", "cutaway", "footprint"]
    rows = []
    for v in sorted(vp, key=lambda k: order.index(k) if k in order else 99):
        rows.append(f'  {v:<11} shows: {", ".join(vp[v])}')
    return "\n".join(rows)


def render_retrieved(chunks, max_chars=1100):
    out = []
    for c in chunks:
        txt = c["text"][:max_chars]
        if len(c["text"]) > max_chars:
            txt += " …"
        out.append(f'--- chunk_id: {c["chunk_id"]}\n'
                   f'    topic: {c["topic"][:90]}\n'
                   f'    text: {txt}')
    return "\n".join(out)


def render_session(state):
    lines = [f'  curriculum_node : {state["curriculum_node"]}',
             f'  current_asset   : {state["asset"]}   view: {state["view"]}',
             f'  assets_shown    : {", ".join(state["assets_shown"]) or "(none yet)"}',
             f'  procedure       : {state.get("procedure") or "(none active)"}',
             f'  step_index      : {state.get("step_index", "—")}']
    if state.get("history"):
        lines.append("  recent turns:")
        for h in state["history"]:
            lines.append(f'    {h["role"]}: {h["text"]}')
    return "\n".join(lines)


def build_prompt(scenario, assets, chunks):
    """Returns (prompt_text, block_index) — block_index lets the demo colour and
    measure each section without re-parsing the string."""
    asset_id = scenario["state"]["asset"]
    parts, lesson = assets[asset_id]
    view = scenario["state"]["view"]
    view_vis = scenario.get("view_visibility")

    sel = [c for c in chunks if c["chunk_id"] in scenario["retrieved"]]

    # Assets shown earlier in the lesson stay addressable — that is VIS-8, the
    # two-level address space, and it is the case the 7 Aug slide result did NOT
    # cover. Their vocabularies have to be in the prompt or the model cannot
    # resolve "the fuse you showed me before" to anything.
    prior_blocks = []
    for prev in scenario["state"]["assets_shown"]:
        if prev == asset_id or prev not in assets:
            continue
        pparts, plesson = assets[prev]
        prior_blocks.append(
            f'asset_id : {pparts["asset_id"]}  ({pparts["title_en"]})\n'
            f'PART VOCABULARY: {", ".join(pparts["part_vocabulary"])}\n'
            f'PART MAP:\n{render_part_table(pparts)}\n'
            + (f'NOTES:\n' + "\n".join(
                f'  [{k}] {v["en"]}' for k, v in plesson["part_notes"].items())
               if plesson else "")
        )

    blocks = [
        ("SYSTEM", SYSTEM.strip()),
        ("VISUAL CONTEXT", (
            f'asset_id : {parts["asset_id"]}\n'
            f'title    : {parts["title_en"]}  ({parts["title_ur"]})\n'
            f'view     : {view}\n'
            f'curriculum_node : {parts["curriculum_node"]}\n'
            f'footprint_type  : ISO mini, Type B\n\n'
            f'PART VOCABULARY — closed set, these ids and no others:\n'
            f'  {", ".join(parts["part_vocabulary"])}\n\n'
            f'PART MAP:\n{render_part_table(parts, view_vis)}\n\n'
            f'AVAILABLE VIEWS — set "goto".view to one that contains every part you name:\n'
            f'{render_views(scenario.get("view_parts") or {})}\n'
            f'Blade widths are a diagnostic cue worth teaching: the coil terminals are\n'
            f'the narrow blades, the load terminals are the wide ones.'
        )),
        ("LESSON CONTENT (human-authored, PROV-3 applies)",
         render_lesson(lesson, scenario.get("procedure"))),
        ("ASSETS SHOWN EARLIER IN THIS LESSON (still addressable)",
         "\n\n".join(prior_blocks) if prior_blocks
         else "(none — this is the first asset of the lesson)"),
        ("RETRIEVED DOCUMENTS (from the corpus, supporting context only)",
         render_retrieved(sel)),
        ("SESSION STATE", render_session(scenario["state"])),
        ("STUDENT TURN — speech recognition output", (
            f'  asr_model  : fine-tuned Urdu-English code-switched ASR (stage 1)\n'
            f'  confidence : {scenario["asr"]["confidence"]}\n'
            f'  audio_ms   : {scenario["asr"]["audio_ms"]}\n'
            f'  transcript : {scenario["asr"]["transcript"]}'
            + (f'\n  NOTE       : {scenario["asr"]["note"]}' if scenario["asr"].get("note") else "")
        )),
        ("OUTPUT CONTRACT — reply with exactly this JSON shape", OUTPUT_SCHEMA.strip()),
    ]

    text, index, pos = "", [], 0
    for title, body in blocks:
        header = f"\n########## {title} ##########\n"
        seg = header + body + "\n"
        index.append({"title": title, "start": pos, "end": pos + len(seg),
                      "chars": len(seg)})
        text += seg
        pos += len(seg)
    return text.strip(), index


# ---------------------------------------------------------------------------
# Validation — this is the runtime guard described in VIS-6, run for real.
# ---------------------------------------------------------------------------

DIGITS = re.compile(r"[0-9٠-٩۰-۹]")
ARABIC = re.compile(r"[؀-ۿ]")
LATIN = re.compile(r"[A-Za-z]")

# Roman-Urdu detection.
#
# The first version of this was an ALLOWED_LATIN whitelist of English technical
# terms, flagging any Latin token not on it. That was backwards and it was measured
# wrong on 16 Aug: across 22 replies it produced SIX flags and ALL SIX were false
# positives -- `electromagnet`, `manual`, `wiring`, `element`. Every one of those is
# a legitimate English technical term under contract rule 2. Precision zero.
#
# The flaw is structural, not a missing word: an open-ended whitelist of English
# vocabulary can never be complete, so the detector's precision depends on a list
# nobody can finish.
#
# Detect the violation by what it actually is instead. Contract rule 4 bans URDU
# words written in Latin letters, and Urdu's function words are a small CLOSED
# class. Romanised Urdu almost never appears without them, and an English technical
# noun is never one of them -- so matching these is high-precision in the direction
# that matters.
ROMAN_URDU_MARKERS = set("""
hai hain tha thi thay hein he ho hota hoti hote hue hua hui
ka ki ke ko se mein main par pe
kya kyun kyu kaise kaisay kahan kab kaun kon jo joh woh wo yeh ye
aur ya lekin magar phir bhi to tou hi
nahi nahin na mat
karta karti karte karna karo kar kiya kijiye karen
lagta lagti lagta lagao laga lagana
dekho dekhen dekhna dekhte
wala wali walay wale
acha accha theek thik sahi galat
ap aap tum tumhara aapka apna apni
hum hamara mera meri mujhe tumhe usko iska uska
ek do teen char paanch
abhi ab phele pehle baad andar bahar upar neeche
zyada kam thora thoda bohat bahut buhat
matlab yaani agar warna jab tab
sakta sakti sakte chahiye chahye
""".split())


def validate(obj, parts, allowed_views=("exterior", "cutaway", "footprint"),
             also_valid=(), view_parts=None):
    """Returns a dict of findings. Never raises — a validator that throws is a
    validator that takes the tutor down mid-lesson.

    `also_valid` carries the part maps of assets shown earlier in the lesson, so a
    correct cross-asset answer is not scored as an invented id.
    """
    vocab = set(parts["part_vocabulary"])
    spoken = {p["id"]: p["spoken_ur"] for p in parts["parts"]}
    for other in also_valid:
        vocab |= set(other["part_vocabulary"])
        for p in other["parts"]:
            spoken.setdefault(p["id"], p["spoken_ur"])
    f = {
        "schema_ok": True, "errors": [],
        "goto_first": False, "goto_part_valid": None,
        "invalid_part_ids": [], "clause_count": 0,
        "digit_leaks": [], "roman_urdu": [], "no_urdu_script": [],
        "unknown_view": None, "return_to_ok": False,
    }

    if not isinstance(obj, dict):
        f["schema_ok"] = False
        f["errors"].append("top level is not an object")
        return f

    keys = list(obj.keys())
    f["goto_first"] = bool(keys) and keys[0] == "goto"

    goto = obj.get("goto")
    if not isinstance(goto, dict):
        f["schema_ok"] = False
        f["errors"].append("missing or malformed `goto`")
    else:
        gp = goto.get("part")
        f["goto_part_valid"] = (gp is None) or (gp in vocab)
        if gp is not None and gp not in vocab:
            f["invalid_part_ids"].append(gp)
        if goto.get("view") not in allowed_views:
            f["unknown_view"] = goto.get("view")

    sp = obj.get("speech")
    if not isinstance(sp, list) or not sp:
        f["schema_ok"] = False
        f["errors"].append("missing or empty `speech`")
        sp = []
    f["clause_count"] = len(sp)

    for i, cl in enumerate(sp):
        if not isinstance(cl, dict) or "text" not in cl:
            f["schema_ok"] = False
            f["errors"].append(f"clause {i} malformed")
            continue
        pid = cl.get("part")
        if pid is not None and pid not in vocab:
            f["invalid_part_ids"].append(pid)
        txt = cl["text"] or ""
        if DIGITS.search(txt):
            f["digit_leaks"].append({"clause": i, "text": txt})
        if not ARABIC.search(txt):
            f["no_urdu_script"].append({"clause": i, "text": txt})
        for w in re.findall(r"[A-Za-z][A-Za-z\-']*", txt):
            if w.lower() in ROMAN_URDU_MARKERS:
                f["roman_urdu"].append({"clause": i, "token": w})

    # View visibility. A part id can be perfectly valid and still be impossible to
    # highlight, because the view on screen does not contain it — the coil, the
    # armature and the contacts only exist in the cutaway. Found by experiment on
    # 14 Aug: a model tagged three clauses to internal parts while leaving the view
    # at `exterior`, which would have highlighted nothing at all. Valid vocabulary,
    # wrong view, silent failure — exactly the class VIS-6 was written to stop.
    f["parts_not_in_view"] = []
    if view_parts:
        view = (goto or {}).get("view") if isinstance(goto, dict) else None
        visible = set(view_parts.get(view, []))
        if visible:
            for i, cl in enumerate(sp):
                pid = cl.get("part") if isinstance(cl, dict) else None
                if pid and pid in vocab and pid not in visible:
                    f["parts_not_in_view"].append(
                        {"clause": i, "part": pid, "view": view})

    # Repair rather than reject. Every part the reply refers to is a real part; the
    # only thing wrong is which view is on screen. If some other rendered view holds
    # all of them, promoting to it is lossless and keeps the lesson intact — far
    # better than dropping five highlights and narrating blind.
    f["repair"] = None
    if view_parts and f["parts_not_in_view"]:
        want = {c.get("part") for c in sp
                if isinstance(c, dict) and c.get("part")}
        want |= {(goto or {}).get("part")} if isinstance(goto, dict) and goto.get("part") else set()
        want.discard(None)
        for cand, ids in view_parts.items():
            if want <= set(ids):
                f["repair"] = {"promote_view": cand,
                               "reason": f"all {len(want)} referenced parts exist in `{cand}`"}
                break

    rt = obj.get("return_to")
    f["return_to_ok"] = isinstance(rt, dict) and (
        rt.get("part") is None or rt.get("part") in vocab)
    if isinstance(rt, dict) and rt.get("part") and rt["part"] not in vocab:
        f["invalid_part_ids"].append(rt["part"])

    # Spoken-form check: if a clause names a terminal, the Urdu number word for it
    # should be the one in the part map, not a freshly invented transliteration.
    f["spoken_form_mismatch"] = []
    for i, cl in enumerate(sp):
        pid = cl.get("part") if isinstance(cl, dict) else None
        if pid and spoken.get(pid):
            want = spoken[pid]
            txt = (cl.get("text") or "")
            if pid.startswith("terminal_") and want not in txt:
                f["spoken_form_mismatch"].append({"clause": i, "part": pid, "expected": want})

    f["invalid_part_ids"] = sorted(set(f["invalid_part_ids"]))
    return f


def extract_json(raw):
    """Models wrap JSON in fences even when told not to. Recover it."""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s), None
    except Exception as e:
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            try:
                return json.loads(s[i:j + 1]), f"recovered by brace-slicing ({e})"
            except Exception as e2:
                return None, str(e2)
        return None, str(e)
