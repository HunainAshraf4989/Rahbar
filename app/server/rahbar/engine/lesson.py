"""Draft relay demonstration assembled from retained facts and rendered assets."""

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

from rahbar.contracts.models import (
    AssetManifest,
    AssetView,
    LessonSentence,
    Model,
    Part,
    Passage,
    PreparedLesson,
    SpeechText,
    VisualTarget,
)
from rahbar.engine.audio import InputError

LESSON_NORMALIZER = "relay-lesson-authored-v1"


class Source(Model):
    file: str
    topic: str
    row: int
    url: str
    quote: str
    transcript_sha256: str


class Entry(Model):
    passage_id: str
    text: str
    cue: str
    view: str
    part: str
    source: Source


class LessonContent(Model):
    title: str
    review_status: Literal["draft"]
    scope: str
    source_file_sha256: str
    entries: list[Entry]


def relay_asset(root: Path) -> AssetManifest:
    directory = root / "asset_gen/out"
    raw = json.loads((directory / "relay_4pin_views.json").read_text())
    parts = json.loads((directory / "relay_4pin.parts.json").read_text())
    digest = hashlib.sha256()
    views = []
    for name, view in raw["views"].items():
        image, overlay = directory / view["png"], directory / view["svg"]
        digest.update(image.read_bytes())
        digest.update(overlay.read_bytes())
        svg = ET.parse(overlay).getroot()
        visible = {node.attrib["id"] for node in svg if node.attrib.get("class") == "part"}
        if visible != set(view["parts"]):
            raise InputError("Asset visibility does not match retained overlay")
        _, _, width, height = map(int, svg.attrib["viewBox"].split())
        views.append(AssetView(view_id=name, image=f"/dev/assets/{image.name}",
                               overlay=f"/dev/assets/{overlay.name}", width=width,
                               height=height, visible_parts=sorted(visible)))
    return AssetManifest(
        asset_id=raw["asset_id"], asset_version=digest.hexdigest(), variant="4-pin relay",
        sources=raw["sources"], review_status="draft", views=views,
        parts=[Part(part_id=p["id"], label_en=p["label_en"],
                    label_ur=p["label_ur"] or p["label_en"],
                    spoken_ur=p["spoken_ur"] or p["label_ur"] or p["label_en"])
               for p in parts["parts"]],
    )


def relay_content(root: Path) -> LessonContent:
    return LessonContent.model_validate_json((root / "content/relay/lesson-source.json").read_text())


def relay_passages(root: Path) -> list[Passage]:
    data = relay_content(root)
    passages = []
    for entry in data.entries:
        source = entry.source
        passages.append(Passage(
            passage_id=entry.passage_id, canonical_text=entry.text,
            source_location=f'{source.file}#topic={source.topic}&row={source.row}',
            source_hash=hashlib.sha256(entry.text.encode()).hexdigest(),
            unit_ids=["relay"], track_ids=["electrical-devices"],
            content_version="relay-demo-v1", review_status="draft", safety="ordinary"))
    return passages


def relay_lesson(asset: AssetManifest, root: Path) -> PreparedLesson:
    data = relay_content(root)
    sentences = []
    for entry in data.entries:
        target = VisualTarget(asset_id=asset.asset_id, asset_version=asset.asset_version,
                              view_id=entry.view, part_id=entry.part)
        if not asset.permits(target):
            raise InputError("Lesson targets an unavailable part")
        text = entry.cue + " " + entry.text
        if re.search(r"[\d\u0900-\u097f()\[\]]", text):
            raise InputError("Lesson requires unsupported speech normalization")
        sentences.append(LessonSentence(sentence_id=entry.passage_id, canonical_text=text,
                                        evidence_ids=[entry.passage_id], visual_target=target))
    if len({s.sentence_id for s in sentences}) != len(sentences):
        raise InputError("Duplicate lesson sentence")
    version = hashlib.sha256("".join(s.model_dump_json() for s in sentences).encode()).hexdigest()
    return PreparedLesson(lesson_id="relay-demo", lesson_version=version, unit_id="relay",
                          content_version="relay-demo-v1", sentences=sentences)


def lesson_speech(sentence: LessonSentence, lesson: PreparedLesson) -> SpeechText:
    if sentence not in lesson.sentences:
        raise InputError("Sentence is not in the authored lesson")
    if re.search(r"[\d\u0900-\u097f()\[\]]", sentence.canonical_text):
        raise InputError("Lesson requires unsupported speech normalization")
    # This small draft contains no digits, units, brackets or procedural instructions.
    return SpeechText(sentence_id=sentence.sentence_id, text=sentence.canonical_text,
                      normalizer_version=LESSON_NORMALIZER)
