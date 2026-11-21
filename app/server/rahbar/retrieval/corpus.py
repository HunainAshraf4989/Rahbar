"""Deterministic, read-only corpus preparation. Outputs are experimental, never reviewed."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

VERSION = "corpus-v2"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(str.maketrans("يك", "یک"))
    return " ".join(text.split())


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def chunks(text: str, limit: int = 150) -> list[str]:
    sentences = re.split(r"(?<=[.!?۔؟])\s+", text)
    result: list[str] = []
    current: list[str] = []
    count = 0
    for sentence in sentences:
        words = len(sentence.split())
        if current and count + words > limit:
            result.append(" ".join(current))
            current, count = [], 0
        current.append(sentence)
        count += words
    if current:
        result.append(" ".join(current))
    return result


def prepare(root: Path, output: Path) -> dict[str, Any]:
    root, output = root.resolve(), output.resolve()
    if output == root or root in output.parents:
        raise ValueError("Output must be outside the read-only corpus")
    inventory: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = []
    passages: dict[str, dict[str, Any]] = {}

    def accept(text: Any, location: str, topic: str, source_hash: str, source_url: Any = None) -> None:
        if not isinstance(text, str) or not text.strip():
            quarantine.append({"location": location, "reason": "empty_or_invalid_text"})
            return
        if "%PDF-" in text or "\x00" in text or "endstream" in text:
            quarantine.append({"location": location, "reason": "binary_text"})
            return
        for index, chunk in enumerate(chunks(normalize(text))):
            identity = digest(chunk)
            passage = passages.setdefault(identity, {
                "passage_id": identity, "canonical_text": chunk, "review_status": "draft",
                "publication_eligible": False, "mapping_status": "unresolved",
                "provenance": [], "topics": [],
            })
            passage["provenance"].append({"location": location, "chunk": index,
                                           "source_hash": source_hash,
                                           "source_url": source_url if isinstance(source_url, str) else None})
            if topic not in passage["topics"]:
                passage["topics"].append(topic)

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        name = str(path.relative_to(root))
        sha = hashlib.sha256(raw).hexdigest()
        entry: dict[str, Any] = {"path": name, "sha256": sha, "bytes": len(raw)}
        inventory.append(entry)
        if path.suffix != ".json":
            entry["disposition"] = "reference_only"
            continue
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            entry["disposition"] = "invalid_json"
            continue
        entry["disposition"] = "parsed"
        groups = data.items() if isinstance(data, dict) else [("", data)]
        for topic, rows in groups:
            if not isinstance(rows, list):
                quarantine.append({"location": f"{name}/{topic}", "reason": "invalid_rows"})
                continue
            for index, row in enumerate(rows):
                location = f"{name}/{topic}/{index}"
                if not isinstance(row, dict):
                    quarantine.append({"location": location, "reason": "invalid_record"})
                    continue
                if "transcript" in row:
                    accept(row["transcript"], location + "/transcript", topic, sha, row.get("url"))
                # Flattened instruction/output records lack original provenance: withhold.
                if "instruction" in row:
                    quarantine.append({"location": location, "reason": "unverified_instruction_source"})
                qa = row.get("qa_pairs", [])
                if not isinstance(qa, list):
                    quarantine.append({"location": location, "reason": "invalid_qa_list"})
                    continue
                for qi, pair in enumerate(qa):
                    ref = f"{location}/qa_pairs/{qi}"
                    if not isinstance(pair, dict):
                        quarantine.append({"location": ref, "reason": "invalid_qa"})
                        continue
                    fields = {str(k).lower(): v for k, v in pair.items()}
                    question, answer = fields.get("question"), fields.get("answer")
                    if not all(isinstance(v, str) and v.strip() for v in (question, answer)):
                        quarantine.append({"location": ref, "reason": "invalid_qa_text"})
                        continue
                    accept(f"{question}\n{answer}", ref, topic, sha, row.get("url"))
    output.mkdir(parents=True, exist_ok=True)
    for name, records in [("inventory", inventory), ("quarantine", quarantine),
                          ("passages", list(passages.values()))]:
        (output / f"{name}.jsonl").write_text("".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records))
    manifest = {"transform_version": VERSION, "files": len(inventory),
                "passages": len(passages), "quarantined": len(quarantine),
                "reviewed": 0, "release_status": "experimental",
                "artifacts": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted(output.glob("*.jsonl"))}}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest
