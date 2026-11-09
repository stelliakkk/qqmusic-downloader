"""Search QQ Music and save the selected recording as a verified original file."""

import asyncio
import base64
import html
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from types import MappingProxyType
from urllib.parse import parse_qs, urljoin, urlsplit

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
    album_mid: str = ""

    @classmethod
    def from_track(cls, track):
        return cls(
            track.mid, html.unescape(track.name or track.title or "未知歌曲"),
            "、".join(html.unescape(s.name) for s in track.singer) or "未知歌手",
            html.unescape(track.album.name) if track.album else "", track.interval or 0,
            track.album.mid if track.album else "",
        )


@dataclass(frozen=True)
class Playlist:
    name: str
    songs: list[Song]
    total: int
    truncated: bool


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
    reused: bool = False


class MusicError(Exception):
    """A message safe to show in the application."""


class LoginRequired(MusicError):
    pass


LOGIN_EXPIRED = (CredentialExpiredError, CredentialInvalidError, LoginAuthExpiredError)
PLAYLIST_HOSTS = {"y.qq.com", "i.y.qq.com", "c.y.qq.com", "u.y.qq.com", "music.qq.com", "c6.y.qq.com"}
PLAYLIST_LIMIT = 500
PLAYLIST_PAGE = 50


class MusicService:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.credential_lock = Lock()

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
        while not self.credential_lock.acquire(blocking=False):
            await asyncio.sleep(0.08)
        try:
            latest = self.storage.load_credential()
            if latest is not None and latest != credential and not latest.is_expired():
                client.credential = latest
            else:
                refreshed = await client.login.refresh_credential()
                if not refreshed.is_valid():
                    raise LoginRequired("QQ 音乐登录已失效，请重新扫码。")
                self.storage.save_credential(refreshed)
        except (*LOGIN_EXPIRED, CredentialRefreshError):
            raise LoginRequired("QQ 音乐登录已失效，请重新扫码。") from None
        finally:
            self.credential_lock.release()
        try:
            return await request()
        except LOGIN_EXPIRED:
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
        return list({track.mid: Song.from_track(track) for track in results.song if track.mid}.values())

    async def playlist(self, text: str) -> Playlist:
        async with asyncio.timeout(55):
            playlist_id = await self._resolve_playlist(text)
            async with self._client() as client:
                async def page_request(page):
                    async def request():
                        return await client.songlist.get_detail(
                            playlist_id, num=PLAYLIST_PAGE, page=page, tag=False, userinfo=False,
                        )
                    try:
                        return await self._with_login(client, request)
                    except RatelimitedError:
                        if self.storage.load_credential() is None:
                            raise LoginRequired("QQ 音乐要求登录后读取歌单。请先扫码。") from None
                        raise

                songs = {}
                total = 0
                for page in range(1, PLAYLIST_LIMIT // PLAYLIST_PAGE + 1):
                    result = await page_request(page)
                    if result.code or result.subcode:
                        raise MusicError("读取歌单失败。请确认链接正确，且当前账号可查看歌单。")
                    if page == 1:
                        name = html.unescape(result.info.title) or f"歌单 {playlist_id}"
                    total = max(total, result.total)
                    for track in result.songs[:PLAYLIST_PAGE]:
                        if track.mid and track.mid not in songs:
                            songs[track.mid] = Song.from_track(track)
                    has_more = bool(result.songs) and (bool(result.hasmore) or page * PLAYLIST_PAGE < total)
                    if not has_more:
                        break
        return Playlist(name, list(songs.values()), max(total, len(songs)), has_more)

    @staticmethod
    async def _resolve_playlist(text):
        current, playlist_id = playlist_reference(text)
        if playlist_id:
            return playlist_id
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8), trust_env=False) as session:
            for _ in range(5):
                async with session.get(current, allow_redirects=False) as reply:
                    location = reply.headers.get("Location")
                    if reply.status not in {301, 302, 303, 307, 308} or not location:
                        break
                    current, playlist_id = playlist_reference(urljoin(current, location))
                    if playlist_id:
                        return playlist_id
        raise MusicError("未能从分享短链识别歌单。请打开链接后复制浏览器里的 QQ 音乐歌单地址。")

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

    async def download(self, song: Song, requested: Quality | None, folder: Path, update, *, candidates=None) -> SavedAudio:
        candidates = candidates or {}
        if requested is not None:
            for entry in candidates.get(requested.file_type.name, ()):
                if cached := entry.reusable():
                    return cached
        update("status", "正在获取下载地址…")
        sources = await self._sources(song)
        if requested is not None:
            sources = [source for source in sources if source.quality == requested]
        if not sources:
            if requested is not None:
                raise MusicError(f"当前账号没有 {requested.name} 音源，可改用自动音质。")
            raise MusicError("当前账号没有这首歌可下载的完整 MP3 或 FLAC 音源。")
        timeout = aiohttp.ClientTimeout(total=None, connect=20, sock_read=25)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for source in sources:
                for entry in candidates.get(source.quality.file_type.name, ()):
                    if cached := entry.reusable():
                        return cached
                try:
                    folder.mkdir(parents=True, exist_ok=True)
                    return await self._download_one(session, song, source, folder, update)
                except (aiohttp.ClientError, TimeoutError, AudioError):
                    if requested is not None:
                        raise MusicError(f"{requested.name} 下载或完整性校验失败。请重试或选择其他音质。") from None
        raise MusicError("可用音质均未能下载完整，请稍后重试。")

    async def attachments(self, song, path, *, cover, lyric):
        warnings = []
        if cover and not path.with_suffix(".jpg").is_file():
            try:
                async with asyncio.timeout(10):
                    album_mid = song.album_mid
                    if not album_mid:
                        async with self._client() as client:
                            async def detail():
                                return await client.song.get_detail(song.mid)
                            album = (await self._with_login(client, detail)).track.album
                        album_mid = album.mid if album else ""
                    if not re.fullmatch(r"[A-Za-z0-9]{1,32}", album_mid):
                        raise MusicError("封面不可用")
                    url = f"https://y.gtimg.cn/music/photo_new/T002R500x500M000{album_mid}.jpg"
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, allow_redirects=False) as reply:
                            if reply.status != 200:
                                raise MusicError("封面不可用")
                            content = bytearray()
                            async for chunk in reply.content.iter_chunked(65536):
                                content.extend(chunk)
                                if len(content) > 2 * 1024 * 1024:
                                    raise MusicError("封面过大")
                    if not content.startswith(b"\xff\xd8\xff") or not content.endswith(b"\xff\xd9"):
                        raise MusicError("封面不是 JPEG")
                    _write_new(path.with_suffix(".jpg"), content)
            except Exception:
                warnings.append("封面未保存")
        if lyric and not path.with_suffix(".lrc").is_file():
            try:
                async with asyncio.timeout(12), self._client() as client:
                    async def request():
                        return await client.lyric.get_lyric(song.mid)
                    result = await self._with_login(client, request)
                _write_new(path.with_suffix(".lrc"), lrc_payload(result.lyric))
            except Exception:
                warnings.append("同步歌词未保存")
        return warnings

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

def playlist_reference(text):
    text = text.strip()
    if re.fullmatch(r"[1-9][0-9]{0,17}", text):
        return "", int(text)
    if len(text) > 4096 or any(ord(char) < 32 for char in text):
        raise MusicError("请输入有效的 QQ 音乐歌单链接或歌单 ID。")
    try:
        url = urlsplit(text)
        host = url.hostname
        if (url.scheme.lower() != "https" or host not in PLAYLIST_HOSTS or url.username is not None
                or url.password is not None or url.port not in {None, 443} or "\\" in text):
            raise ValueError
    except ValueError:
        raise MusicError("请输入有效的 QQ 音乐歌单链接或歌单 ID。") from None
    path = url.path.rstrip("/")
    match = re.fullmatch(r"/(?:n/ryqq(?:_v2)?/)?playlist/([1-9][0-9]{0,17})", path)
    if match:
        return "", int(match[1])
    query = parse_qs(url.query)
    if re.fullmatch(
        r"/(?:(?:n/)?m/detail/taoge(?:\.html|/index\.html)?|w/taoge\.html|"
        r"n2/m/share/details/taoge\.html|"
        r"(?:n/)?yqq/playlist(?:/[0-9]+\.html)?|playlist|"
        r"(?:v8/)?playsquare(?:\.html|/index\.html)?|base/fcgi-bin/u)", path,
    ):
        for key in ("disstid", "dissid", "id"):
            value = query.get(key, [""])[0]
            if re.fullmatch(r"[1-9][0-9]{0,17}", value):
                return "", int(value)
        match = re.fullmatch(r"/n/yqq/playlist/([1-9][0-9]{0,17})\.html", path)
        if match:
            return "", int(match[1])
    if host in {"u.y.qq.com", "c6.y.qq.com"} or path.startswith(("/base/fcgi-bin/u", "/n/m/share/")):
        return text, None
    raise MusicError("该链接不是可识别的 QQ 音乐歌单链接。")


def lrc_payload(value):
    timestamp = r"(?m)^\s*\[\d{1,4}:\d{2}(?:[.:]\d{1,3})?\]"
    if len(value) > 1024 * 1024:
        raise MusicError("歌词过大")
    if not re.search(timestamp, value):
        try:
            value = base64.b64decode(value, validate=True).decode("utf-8-sig")
        except (ValueError, UnicodeError):
            raise MusicError("同步歌词不可用") from None
    if not re.search(timestamp, value):
        raise MusicError("同步歌词不可用")
    return html.unescape(value).strip().encode("utf-8") + b"\n"


def safe_component(value, fallback, limit=100):
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)[:limit].strip(" .") or fallback
    if re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]|CONIN\$|CONOUT\$)", clean.split(".")[0].rstrip()):
        clean = "_" + clean
    return clean


def track_folder(folder, song, layout):
    if layout in {"artist", "artist_album"}:
        folder /= safe_component(song.artists, "未知歌手", 70)
    if layout in {"album", "artist_album"}:
        folder /= safe_component(song.album, "未知专辑", 70)
    return folder


def _write_new(path, payload):
    with NamedTemporaryFile(dir=path.parent, prefix=".qqmusic-", suffix=".part", delete=False) as file:
        pending = Path(file.name)
        try:
            file.write(payload)
            file.close()
            try:
                pending.rename(path)
            except FileExistsError:
                pass
        finally:
            pending.unlink(missing_ok=True)


def _save_without_overwrite(temporary: Path, folder: Path, song: Song, extension: str) -> Path:
    stem = safe_component(f"{song.name} - {song.artists}", "QQ Music", 115)
    index = 1
    while True:
        suffix = f" ({index})" if index > 1 else ""
        target = folder / f"{stem}{suffix}{extension}"
        if any(target.with_suffix(ext).exists() for ext in (".mp3", ".flac", ".jpg", ".lrc")):
            index += 1
            continue
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
