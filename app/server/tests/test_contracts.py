import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from rahbar.contracts import http, models
from rahbar.contracts.events import AudioInput, parse_event

ROOT = Path(__file__).resolve().parents[3]
CASES = [
    (p.stem, item)
    for p in sorted((ROOT / "contracts/fixtures").glob("*.json"))
    for item in json.loads(p.read_text())
]


@pytest.mark.parametrize("name,record", CASES)
def test_shared_fixture_roundtrip(name, record):
    def parse(data):
        return (
            parse_event(data, record["direction"])
            if record["model"] == "event"
            else (
                getattr(models, record["model"], None) or getattr(http, record["model"])
            ).model_validate(data)
        )

    if record.get("valid", True):
        parsed = parse(record["data"])
        assert parse(json.loads(parsed.model_dump_json())) == parsed
    else:
        with pytest.raises((ValueError, ValidationError)):
            parse(record["data"])


def test_exact_frame_and_version_visibility():
    asset = models.AssetManifest.model_validate(
        json.loads((ROOT / "contracts/fixtures/asset_visibility.json").read_text())[0]["data"]
    )
    target = models.VisualTarget(
        asset_id=asset.asset_id,
        asset_version=asset.asset_version,
        view_id="cutaway",
        frame_id="0",
        part_id="coil",
    )
    assert asset.permits(target)
    for field, value in [
        ("asset_id", "other"),
        ("asset_version", "other"),
        ("view_id", "exterior"),
        ("frame_id", "1"),
        ("frame_id", None),
        ("part_id", "case"),
    ]:
        assert not asset.permits(target.model_copy(update={field: value}))


def test_private_quiz_does_not_expose_answer():
    item = models.PrivateQuizItem(
        item_id="q1", question="Synthetic?", options={"a": "A", "b": "B"}, expected_choice="a"
    )
    assert "expected_choice" not in item.public().model_dump_json()
    with pytest.raises(ValidationError):
        models.PrivateQuizItem(
            item_id="q1", question="?", options={"a": "A", "b": "B"}, expected_choice="c"
        )


def test_private_viva_does_not_expose_key_points():
    result = models.PrivateVivaResult(
        attempt_id="a",
        unit_id="u",
        content_version="v",
        assessment_version="v",
        outcome="incomplete",
        feedback="Repeat",
        item_status={"p": "incomplete"},
        transcript_references=[],
        matched_key_points=[],
        missing_key_points=["secret-key"],
        evaluator_version="v",
    )
    assert "secret-key" not in result.public().model_dump_json()
    assert "item_status" not in result.public().model_dump()


@pytest.mark.parametrize("data", ["?", "AA==", ""])
def test_invalid_pcm(data):
    with pytest.raises(ValidationError):
        AudioInput(stream_id="s", chunk_sequence=0, data=data)


def test_untrusted_session_cannot_supply_reviewed_context():
    with pytest.raises(ValidationError):
        models.SessionCreate(
            course_id="c",
            content_version="v",
            unit_id="u",
            lesson_id="l",
            reviewed_passages=["untrusted"],
        )


def test_wrong_direction_rejected():
    record = json.loads((ROOT / "contracts/fixtures/normal_answer.json").read_text())[0]
    with pytest.raises(ValueError):
        parse_event(record["data"], "client")


def test_approval_and_review_invariants():
    with pytest.raises(ValidationError):
        models.CheckResult(status="approved", reasons=[], evidence_checks=[], output=None)
    with pytest.raises(ValidationError):
        models.Passage(
            passage_id="p",
            source_location="synthetic",
            source_hash="hash",
            canonical_text="test",
            unit_ids=["u"],
            track_ids=["t"],
            content_version="v",
            review_status="reviewed",
            safety="safety",
        )


def test_generated_contracts_are_current_and_deterministic(tmp_path):
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, str(ROOT / "tools/export_contracts.py"), "--output", str(tmp_path)],
        check=True,
        cwd=ROOT,
    )
    for name in ("contracts.schema.json", "events.json", "openapi.json"):
        assert (tmp_path / name).read_bytes() == (ROOT / "contracts" / name).read_bytes()


def test_generated_references_and_operation_boundaries():
    document = json.loads((ROOT / "contracts/openapi.json").read_text())

    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                target = document
                for key in value["$ref"].removeprefix("#/").split("/"):
                    target = target[key]
                assert target
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(document)
    paths = document["paths"]
    for name in ("/api/v1/engine/sessions", "/api/v1/viva-attempts", "/api/v1/quiz-attempts"):
        assert any(
            p["name"] == "Idempotency-Key" and p["required"]
            for p in paths[name]["post"]["parameters"]
        )
    assert paths["/api/v1/viva-attempts/{attempt_id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("/VivaResult")
    assert paths["/webhooks/whatsapp"]["post"]["security"] == [{"webhookSignature": []}]
