"""Prepare narration once; interrupted runs reuse each completed, verified WAV."""

import base64
import hashlib
import io
import json
import wave
from pathlib import Path

from rahbar.config import Settings
from rahbar.contracts.models import AudioFormat, BakedAudio, PreparedLesson
from rahbar.contracts.ports import TTSPort, TurnContext
from rahbar.engine.audio import InputError, pcm_to_wav
from rahbar.engine.lesson import lesson_speech
from rahbar.engine.runtime import check_cancelled


def cached_pcm(path: Path) -> bytes:
    with wave.open(io.BytesIO(path.read_bytes()), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 24000):
            raise InputError("Invalid baked audio format")
        frames = wav.getnframes()
        pcm = wav.readframes(frames)
        if not 0 < frames <= 24000 * 60 or len(pcm) != frames * 2:
            raise InputError("Invalid baked audio length")
        return pcm


async def bake_lesson(lesson: PreparedLesson, speech: TTSPort, settings: Settings,
                      directory: Path, context: TurnContext) -> PreparedLesson:
    directory.mkdir(parents=True, exist_ok=True)
    sentences = []
    for sentence in lesson.sentences:
        check_cancelled(context)
        text = lesson_speech(sentence, lesson)
        key = hashlib.sha256(json.dumps({
            "text": text.model_dump(), "voice": settings.voice_id, "model": settings.tts_model,
            "provider": "elevenlabs", "language": "ur", "sample_rate": 24000,
        }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        path = directory / f"{key}.wav"
        checksum = directory / f"{key}.sha256"
        reusable = False
        if path.exists() and checksum.exists():
            try:
                cached_pcm(path)
                reusable = hashlib.sha256(path.read_bytes()).hexdigest() == checksum.read_text()
            except (InputError, wave.Error, EOFError):
                pass
        if not reusable:
            pcm = bytearray()
            sequence = 0
            async for chunk in speech.synthesize(text, context):
                check_cancelled(context)
                if (chunk.sample_rate != 24000 or chunk.audio.sentence_id != sentence.sentence_id
                        or chunk.audio.chunk_sequence != sequence):
                    raise InputError("Invalid lesson synthesis chunk")
                pcm.extend(base64.b64decode(chunk.audio.data, validate=True))
                sequence += 1
                if len(pcm) > 24000 * 2 * 60:
                    raise InputError("Lesson sentence exceeds maximum duration")
            if not pcm:
                raise InputError("Empty lesson synthesis")
            check_cancelled(context)
            data = pcm_to_wav(bytes(pcm))
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
            checksum.write_text(hashlib.sha256(data).hexdigest())
        sentences.append(sentence.model_copy(update={"audio": BakedAudio(
            path=f"/dev/lesson-audio/{key}.wav", bake_hash=key,
            format=AudioFormat(sample_rate=24000))}))
    baked = lesson.model_copy(update={"sentences": sentences})
    temporary = directory / "lesson.tmp"
    temporary.write_text(baked.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(directory / "lesson.json")
    return baked
