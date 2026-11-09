"""Local queue and verified-download history; mutated by the GUI thread."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .music import QUALITIES, Quality, SavedAudio, Song
from .storage import Storage


QUALITY_KEYS = {quality.file_type.name: quality for quality in QUALITIES}
LAYOUTS = {"flat": "集中保存", "artist": "按歌手", "album": "按专辑", "artist_album": "歌手 / 专辑"}
WAITING, RUNNING, DONE, EXISTS, FAILED, CANCELLED = "waiting", "running", "done", "exists", "failed", "cancelled"
STATES = {WAITING: "等待", RUNNING: "处理中", DONE: "已完成", EXISTS: "已存在", FAILED: "失败", CANCELLED: "已取消"}
RETRYABLE = {FAILED, CANCELLED}


def quality_key(quality: Quality | None) -> str:
    return quality.file_type.name if quality else ""


@dataclass(frozen=True)
class Options:
    cover: bool = False
    lyric: bool = False
    layout: str = "flat"
    dedupe: bool = True

    @classmethod
    def read(cls, data):
        if not isinstance(data, dict):
            return cls()
        layout = data.get("layout")
        return cls(
            data.get("cover") is True, data.get("lyric") is True,
            layout if isinstance(layout, str) and layout in LAYOUTS else "flat", data.get("dedupe") is not False,
        )


@dataclass
class Job:
    song: Song
    quality: str
    folder: str
    options: Options
    key: str = field(default_factory=lambda: uuid4().hex)
    state: str = WAITING
    detail: str = ""
    path: str = ""

    @property
    def requested(self):
        return QUALITY_KEYS.get(self.quality)


@dataclass(frozen=True)
class HistoryEntry:
    song: Song
    quality: str
    path: str
    size: int
    seconds: float
    sample_rate: int
    bits: int
    modified_ns: int
    date: str

    @classmethod
    def from_saved(cls, song, saved):
        return cls(song, quality_key(saved.quality), str(saved.path), saved.size, saved.seconds,
                   saved.sample_rate, saved.bits, saved.path.stat().st_mtime_ns,
                   datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def reusable(self) -> SavedAudio | None:
        path = Path(self.path)
        try:
            stat = path.stat()
            quality = QUALITY_KEYS[self.quality]
            if (path.is_file() and stat.st_size == self.size and stat.st_mtime_ns == self.modified_ns
                    and path.suffix.lower() == quality.file_type.e.lower()):
                return SavedAudio(path, quality, self.size, self.seconds, self.sample_rate, self.bits, reused=True)
        except (OSError, KeyError, ValueError):
            pass
        return None


def _song(data):
    song = Song(**data)
    if (not all(isinstance(value, str) for value in (song.mid, song.name, song.artists, song.album, song.album_mid))
            or not song.mid or type(song.seconds) is not int or song.seconds < 0):
        raise ValueError("invalid song")
    return song


def _objects(data, key):
    values = data.get(key)
    return values if isinstance(values, list) else []


class Workspace:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.jobs: list[Job] = []
        self.history: list[HistoryEntry] = []
        data = storage.read_object(storage.workspace_path)
        keys = set()
        for raw in _objects(data, "queue"):
            try:
                entry = Job(**(raw | {"song": _song(raw["song"]), "options": Options.read(raw.get("options"))}))
                if (entry.state not in STATES or not Path(entry.folder).is_absolute()
                        or not isinstance(entry.key, str) or entry.key in keys
                        or not isinstance(entry.detail, str) or not isinstance(entry.path, str)
                        or entry.quality and entry.quality not in QUALITY_KEYS):
                    continue
                if entry.state == RUNNING:
                    entry.state, entry.detail, entry.path = WAITING, "上次未完成", ""
                keys.add(entry.key)
                self.jobs.append(entry)
            except (TypeError, ValueError, KeyError, AttributeError):
                continue
        for raw in _objects(data, "history"):
            try:
                entry = HistoryEntry(**(raw | {"song": _song(raw["song"])}))
                if (entry.quality in QUALITY_KEYS and Path(entry.path).is_absolute()
                        and Path(entry.path).suffix.lower() == QUALITY_KEYS[entry.quality].file_type.e.lower()
                        and all(type(value) is int and value >= 0 for value in
                                (entry.size, entry.sample_rate, entry.bits, entry.modified_ns))
                        and type(entry.seconds) in {float, int} and entry.seconds >= 0
                        and isinstance(entry.date, str)):
                    self.history.append(entry)
            except (TypeError, ValueError, KeyError):
                continue

    def save(self):
        self.storage.write_object(self.storage.workspace_path, {
            "queue": [asdict(job) for job in self.jobs],
            "history": [asdict(item) for item in self.history],
        })

    def add(self, songs, quality, folder, options):
        pending = {(job.song.mid, job.quality) for job in self.jobs if job.state in {WAITING, RUNNING}}
        added = []
        for song in songs:
            key = (song.mid, quality_key(quality))
            if key not in pending:
                job = Job(song, key[1], str(folder), options)
                self.jobs.append(job)
                added.append(job)
                pending.add(key)
        return added

    def record(self, song, saved):
        if saved.reused:
            return
        entry = HistoryEntry.from_saved(song, saved)
        self.history = [entry] + [old for old in self.history if old.path != entry.path]

    def candidates(self, job):
        if not job.options.dedupe:
            return {}
        items = {}
        for entry in self.history:
            if entry.song.mid == job.song.mid:
                items.setdefault(entry.quality, []).append(entry)
        return items
