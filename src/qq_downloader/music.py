"""Search QQ Music and save the selected recording as a verified original file."""

import asyncio
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import MappingProxyType
from urllib.parse import urljoin

import aiohttp
from qqmusic_api import Client
from qqmusic_api.core import executor, response
from qqmusic_api.core.exceptions import (
    CredentialExpiredError, CredentialInvalidError, CredentialRefreshError,
    LoginAuthExpiredError, NetworkError, RatelimitedError,
)
from qqmusic_api.core.transport import NiquestsTransport
from qqmusic_api.models.login import QRCodeLoginEvents, QRLoginType
from qqmusic_api.modules.search import SearchType
from qqmusic_api.modules.song import SongFileInfo, SongFileType

from .audio import AudioError, verify_audio
from .storage import Storage


def _snapshot(raw):
    # niquests may return a named cookie with no value during QQ QR confirmation.
    return response.RawPayload(
        status_code=raw.status_code, url=raw.url, headers=MappingProxyType(raw.headers),
        cookies={name: value or "" for name, value in raw.cookies.items()},
        content=raw.content or b"", text=raw.text or "",
    )


response.snapshot_payload = executor.snapshot_payload = _snapshot


@dataclass(frozen=True)
class Song:
    mid: str
    name: str
    artists: str
    album: str
    seconds: int


@dataclass(frozen=True)
class Quality:
    file_type: SongFileType
    name: str


QUALITIES = (
    Quality(SongFileType.MASTER, "臻品母带 FLAC"),
    Quality(SongFileType.ATMOS_2, "臻品音质 FLAC"),
    Quality(SongFileType.FLAC, "无损 FLAC"),
    Quality(SongFileType.MP3_320, "高品质 MP3 · 320 kbps"),
    Quality(SongFileType.MP3_128, "标准 MP3 · 128 kbps"),
)


@dataclass(frozen=True)
class Source:
    quality: Quality
    url: str


@dataclass(frozen=True)
class SavedAudio:
    path: Path
    quality: Quality
    size: int
    seconds: float
    sample_rate: int
    bits: int


class MusicError(Exception):
    """A message safe to show in the application."""


class LoginRequired(MusicError):
    pass


LOGIN_EXPIRED = (CredentialExpiredError, CredentialInvalidError, LoginAuthExpiredError)


class MusicService:
    def __init__(self, storage: Storage):
        self.storage = storage

    @asynccontextmanager
    async def _client(self, *, required=False):
        self.storage.data.mkdir(parents=True, exist_ok=True)
        credential = self.storage.load_credential()
        if required and credential is None:
            raise LoginRequired("请扫码登录 QQ 音乐后查询可用音质。")
        async with Client(credential=credential, device_path=str(self.storage.device_path)) as client:
            yield client

    async def _with_login(self, client, request):
        credential = client.credential
        if not (credential.is_valid() and credential.is_expired()):
            try:
                return await request()
            except LOGIN_EXPIRED:
                if not credential.is_valid():
                    raise LoginRequired("请扫码登录 QQ 音乐。") from None
        try:
            refreshed = await client.login.refresh_credential()
            if not refreshed.is_valid():
                raise LoginRequired("QQ 音乐登录已失效，请重新扫码。")
            self.storage.save_credential(refreshed)
            return await request()
        except (*LOGIN_EXPIRED, CredentialRefreshError):
            raise LoginRequired("QQ 音乐登录已失效，请重新扫码。") from None

    async def search(self, keyword: str) -> list[Song]:
        async with asyncio.timeout(25), self._client() as client:
            async def request():
                return await client.search.search_by_type(keyword=keyword, num=20, search_type=SearchType.SONG)

            try:
                results = await self._with_login(client, request)
            except RatelimitedError:
                if self.storage.load_credential() is None:
                    raise LoginRequired("QQ 音乐要求登录后搜索。请先扫码。") from None
                raise
        songs = {}
        for track in results.song:
            if track.mid and track.mid not in songs:
                songs[track.mid] = Song(
                    track.mid, track.name or track.title, "、".join(s.name for s in track.singer) or "未知歌手",
                    track.album.name if track.album else "", track.interval or 0,
                )
        return list(songs.values())

    async def _sources(self, song: Song) -> list[Source]:
        async with asyncio.timeout(30), self._client(required=True) as client:
            async def request():
                track = (await client.song.get_detail(song.mid)).track
                media_mid = getattr(track.file, "media_mid", None)
                requests = [
                    SongFileInfo(mid=song.mid, media_mid=media_mid, song_type=track.type, file_type=q.file_type)
                    for q in QUALITIES
                ]
                result = await client.song.get_song_urls(requests)
                authorized = {
                    item.filename: item.purl for item in result.data or []
                    if item.mid == song.mid and item.result == 0 and item.purl
                }
                if not authorized:
                    return []
                cdn = await client.song.get_cdn_dispatch()
                if not cdn.sip:
                    raise MusicError("QQ 音乐暂未返回下载服务器，请稍后重试。")
                sources = []
                for quality in QUALITIES:
                    kind = quality.file_type
                    filename = f"{kind.s}{media_mid or song.mid * 2}{kind.e}"
                    if filename in authorized:
                        sources.append(Source(quality, urljoin(cdn.sip[0], authorized[filename])))
                return sources

            return await self._with_login(client, request)

    async def qualities(self, song: Song) -> list[Quality]:
        return [source.quality for source in await self._sources(song)]

    async def qr_login(self, update, refresh, stop):
        self.storage.data.mkdir(parents=True, exist_ok=True)
        transport = NiquestsTransport(timeout=(5, 8))
        async with Client(device_path=str(self.storage.device_path), transport=transport) as client:
            while not stop.is_set():
                refresh.clear()
                qr = await client.login.get_qrcode(QRLoginType.QQ)
                if stop.is_set():
                    break
                update("qr", qr.data)
                expires = time.monotonic() + 180
                poll_at = time.monotonic() + 2
                while not refresh.is_set() and not stop.is_set():
                    now = time.monotonic()
                    if now >= expires:
                        raise MusicError("二维码已过期，请刷新二维码。")
                    if now < poll_at:
                        await asyncio.sleep(0.1)
                        continue
                    try:
                        result = await client.login.check_qrcode(qr)
                    except NetworkError:
                        if refresh.is_set() or stop.is_set():
                            break
                        raise
                    if refresh.is_set() or stop.is_set():
                        break
                    if result.event == QRCodeLoginEvents.DONE:
                        if result.credential is None or not result.credential.is_valid():
                            raise MusicError("QQ 音乐未返回有效登录，请刷新二维码重试。")
                        self.storage.save_credential(result.credential)
                        return True
                    if result.event == QRCodeLoginEvents.TIMEOUT:
                        raise MusicError("二维码已过期，请刷新二维码。")
                    if result.event == QRCodeLoginEvents.REFUSE:
                        raise MusicError("已在手机上取消登录，可以刷新二维码重试。")
                    update("scan", result.event == QRCodeLoginEvents.CONF)
                    poll_at = time.monotonic() + 2.5
        return False

    async def download(self, song: Song, requested: Quality | None, folder: Path, update) -> SavedAudio:
        update("status", "正在获取下载地址…")
        sources = await self._sources(song)
        if requested is not None:
            sources = [source for source in sources if source.quality == requested]
        if not sources:
            raise MusicError("所选音质当前不可用，请重新查看音质列表。")
        folder.mkdir(parents=True, exist_ok=True)
        timeout = aiohttp.ClientTimeout(total=None, connect=20, sock_read=25)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for source in sources:
                try:
                    return await self._download_one(session, song, source, folder, update)
                except (aiohttp.ClientError, TimeoutError, AudioError):
                    if requested is not None:
                        raise MusicError(f"{requested.name} 下载或完整性校验失败。请重试或选择其他音质。") from None
        raise MusicError("可用音质均未能下载完整，请稍后重试。")

    async def _download_one(self, session, song, source, folder, update):
        quality = source.quality
        update("status", f"正在下载 {quality.name}…")
        update("progress", (0, 0))
        with NamedTemporaryFile(dir=folder, prefix=".qqmusic-", suffix=".part", delete=False) as output:
            temporary = Path(output.name)
            try:
                async with session.get(source.url) as reply:
                    reply.raise_for_status()
                    size = 0
                    total = reply.content_length or 0
                    last_update = 0.0
                    async for chunk in reply.content.iter_chunked(256 * 1024):
                        output.write(chunk)
                        size += len(chunk)
                        if time.monotonic() - last_update >= 0.12:
                            update("progress", (size, total))
                            last_update = time.monotonic()
                output.close()
                update("progress", (size, total or size))
                update("status", "正在校验音频完整性…")
                audio = await verify_audio(temporary, quality.file_type.e, song.seconds)
                target = _save_without_overwrite(temporary, folder, song, quality.file_type.e)
                return SavedAudio(target, quality, size, audio.seconds, audio.sample_rate, audio.bits)
            finally:
                output.close()
                temporary.unlink(missing_ok=True)

def _save_without_overwrite(temporary: Path, folder: Path, song: Song, extension: str) -> Path:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", f"{song.name} - {song.artists}")[:135].strip(" .") or "QQ Music"
    index = 1
    while True:
        suffix = f" ({index})" if index > 1 else ""
        target = folder / f"{stem}{suffix}{extension}"
        try:
            temporary.rename(target)
            return target
        except FileExistsError:
            index += 1


def error_message(exc: Exception) -> str:
    if isinstance(exc, MusicError):
        return str(exc)
    if isinstance(exc, RatelimitedError):
        return "QQ 音乐暂时限制了请求，请稍后重试。也可通过右上角重新扫码登录。"
    if isinstance(exc, (NetworkError, aiohttp.ClientError, TimeoutError)):
        return "连接 QQ 音乐超时或中断，请检查网络后重试。"
    if isinstance(exc, OSError):
        return "无法读写本地文件，请检查目录权限和剩余空间。"
    return f"QQ 音乐请求未能完成，请重试。（{type(exc).__name__}）"
