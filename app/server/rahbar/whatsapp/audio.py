"""Bounded conversion of WhatsApp voice notes and tutor WAV replies."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from rahbar.engine.audio import validate_wav

from .meta import ChannelError


class AudioCodec:
    def __init__(self, executable: str) -> None:
        self.executable = executable

    async def _convert(
        self, data: bytes, input_format: str, output: list[str], suffix: str
    ) -> bytes:
        with TemporaryDirectory(prefix="rahbar-wa-") as directory:
            source, target = Path(directory) / "source", Path(directory) / ("reply" + suffix)
            source.write_bytes(data)
            process = await asyncio.create_subprocess_exec(
                self.executable,
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-protocol_whitelist",
                "file,pipe",
                "-f",
                input_format,
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                *output,
                str(target),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(process.wait(), 20)
            except BaseException:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                raise
            if process.returncode or not target.is_file() or target.stat().st_size > 4_000_000:
                raise ChannelError("Could not decode voice audio")
            return target.read_bytes()

    async def incoming(self, data: bytes, mime: str) -> bytes:
        formats = {
            "audio/ogg": "ogg",
            "audio/mpeg": "mp3",
            "audio/mp4": "mov",
            "audio/aac": "aac",
            "audio/amr": "amr",
        }
        if mime not in formats:
            raise ChannelError("Unsupported voice format")
        wav = await self._convert(
            data,
            formats[mime],
            ["-t", "31", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le"],
            ".wav",
        )
        validate_wav(wav)
        return wav

    async def outgoing(self, wav: bytes) -> bytes:
        return await self._convert(
            wav,
            "wav",
            ["-t", "61", "-ac", "1", "-ar", "24000", "-acodec", "libopus", "-b:a", "32k"],
            ".ogg",
        )
