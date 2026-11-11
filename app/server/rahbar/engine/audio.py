"""Explicit audio boundary: complete WAV input, mono PCM16 output."""

import io
import wave


class InputError(ValueError):
    pass


def validate_wav(audio: bytes) -> None:
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                raise InputError("Input must be mono PCM16 WAV at 16000 Hz")
            frames = wav.getnframes()
            if not 1600 <= frames <= 480000:
                raise InputError("Input duration must be between 0.1 and 30 seconds")
            if len(wav.readframes(frames)) != frames * 2:
                raise InputError("Truncated WAV input")
    except (wave.Error, EOFError) as exc:
        raise InputError("Invalid WAV input") from exc


def pcm_to_wav(pcm: bytes, sample_rate: int = 24000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()
