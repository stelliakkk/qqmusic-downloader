"""Validate the original audio without a separate decoder executable."""

import asyncio
import hashlib
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import miniaudio


class AudioError(Exception):
    pass


@dataclass(frozen=True)
class AudioInfo:
    seconds: float
    sample_rate: int
    bits: int


def _flac_header(path: Path):
    with path.open("rb") as file:
        header = file.read(42)
    if len(header) != 42 or header[:4] != b"fLaC" or header[4] & 127 or header[5:8] != b"\x00\x00\x22":
        raise AudioError("无效的 FLAC 文件头。")
    stream = int.from_bytes(header[18:26], "big")
    rate = stream >> 44
    channels = ((stream >> 41) & 7) + 1
    bits = ((stream >> 36) & 31) + 1
    frames = stream & ((1 << 36) - 1)
    if rate <= 0 or not 4 <= bits <= 32:
        raise AudioError("无效的 FLAC 音轨参数。")
    return rate, channels, bits, frames, header[26:42]


def _flac_pcm(samples, bits: int) -> bytes | bytearray:
    """Serialize native FLAC samples in the format used by STREAMINFO MD5."""
    raw = samples.tobytes()
    if bits == 32:
        return raw
    width = (bits + 7) // 8
    if bits % 8:
        return b"".join((sample >> (32 - bits)).to_bytes(width, "little", signed=True) for sample in samples)
    pcm = bytearray(len(samples) * width)
    for byte in range(width):
        pcm[byte::width] = raw[4 - width + byte::4]
    return pcm


async def verify_audio(path: Path, extension: str, expected_seconds: int) -> AudioInfo:
    """Decode the whole file and check its frame count and expected song length."""
    checksum = None
    try:
        if extension == ".flac":
            rate, channels, bits, frames, expected_digest = _flac_header(path)
            if any(expected_digest):
                checksum = hashlib.md5(usedforsecurity=False)
            chunks = miniaudio.stream_file(str(path), miniaudio.SampleFormat.SIGNED32, channels, rate, frames_to_read=8192)
        elif extension == ".mp3":
            metadata = miniaudio.mp3_get_file_info(str(path))
            rate, channels, bits, frames = metadata.sample_rate, metadata.nchannels, 0, metadata.num_frames
            chunks = miniaudio.mp3_stream_file(str(path), frames_to_read=8192)
        else:
            raise AudioError("不支持的音频格式。")

        decoded = 0
        with closing(chunks):
            for index, samples in enumerate(chunks):
                decoded += len(samples) // channels
                if checksum is not None:
                    checksum.update(_flac_pcm(samples, bits))
                if index % 16 == 0:
                    await asyncio.sleep(0)
        if decoded <= 0 or rate <= 0 or (frames and decoded != frames):
            raise AudioError("下载内容缺少音频帧。")
        if checksum is not None and checksum.digest() != expected_digest:
            raise AudioError("FLAC 音频校验失败。")
        seconds = decoded / rate
        if expected_seconds > 0 and abs(seconds - expected_seconds) > 4:
            raise AudioError("下载内容与歌曲时长不符。")
        return AudioInfo(seconds, rate, bits)
    except (miniaudio.MiniaudioError, ValueError, ZeroDivisionError):
        raise AudioError("下载内容无法按所选格式解码。") from None
