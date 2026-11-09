"""Build the share ZIP from a clean executable and committed corresponding source."""

import hashlib
import io
import json
import re
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from release_sources import CACHE, ROOT, sha256

sys.path.insert(0, str(ROOT / "src"))
from qq_downloader import __version__


BUNDLE = ROOT / "build" / "stage-v2" / "QQMusicDownloader"
DIST = ROOT / "dist" / "v2-share"
PREFIX = f"QQMusicDownloader-v{__version__}"
PRIVATE_NAMES = re.compile(r"(?i)(^\.env(?:\.|$)|credential|qqmusic_device|running\.lock|\.(?:dpapi|flac|mp3|part)$)")


def check_member(name: str):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or any(PRIVATE_NAMES.search(part) for part in path.parts):
        raise ValueError(f"Disallowed ZIP member: {name}")


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def put(zip_file, path: str, data: Path | bytes, *, stored=False):
    check_member(path)
    name = f"{PREFIX}/{path}"
    compression = ZIP_STORED if stored else ZIP_DEFLATED
    if isinstance(data, Path):
        if data.is_symlink() or not data.is_file():
            raise ValueError(f"Not a regular release input: {data.name}")
        zip_file.write(data, name, compress_type=compression)
    else:
        zip_file.writestr(name, data, compress_type=compression)


def package():
    if git("status", "--porcelain"):
        raise RuntimeError("Commit the exact release source before packaging.")
    commit = git("rev-parse", "HEAD").decode("ascii").strip()
    manifest = json.loads((CACHE / "manifest.json").read_text(encoding="utf-8"))
    if not manifest or not any(entry["file"].startswith("qqmusic_api_python-0.8.2") for entry in manifest):
        raise ValueError("QQMusicApi corresponding source is missing.")
    for entry in manifest:
        if PurePosixPath(entry["file"]).name != entry["file"] or sha256(CACHE / entry["file"]) != entry["sha256"]:
            raise ValueError("A source archive does not match the manifest.")

    if {item.name for item in BUNDLE.iterdir()} != {"QQMusicDownloader.exe", "_internal"}:
        raise RuntimeError("Use a clean build directory containing only the executable and _internal.")
    runtime = sorted(p for p in (BUNDLE / "_internal").rglob("*") if p.is_file())
    if not runtime or not any(p.name.startswith("_miniaudio") for p in runtime):
        raise ValueError("The bundled decoder is missing.")
    DIST.mkdir(parents=True, exist_ok=True)
    result = DIST / f"{PREFIX}-Windows-x64.zip"
    pending = result.with_suffix(".zip.part")
    try:
        with ZipFile(pending, "w", compression=ZIP_DEFLATED, compresslevel=6) as output:
            put(output, "QQMusicDownloader.exe", BUNDLE / "QQMusicDownloader.exe")
            for path in runtime:
                put(output, path.relative_to(BUNDLE).as_posix(), path)
            put(output, "使用说明.txt", ROOT / "docs" / "portable-quickstart.txt")
            for name in ("LICENSE", "THIRD_PARTY.md"):
                put(output, name, ROOT / name)
            put(output, "source/commit.txt", (commit + "\n").encode("ascii"))
            with ZipFile(io.BytesIO(git("archive", "--format=zip", commit))) as project:
                for name in project.namelist():
                    if not name.endswith("/"):
                        put(output, f"source/project/{name}", project.read(name))
            put(output, "source/upstream/manifest.json", CACHE / "manifest.json")
            for entry in manifest:
                name = entry["file"]
                put(output, f"source/upstream/{name}", CACHE / name, stored=True)
            for line in (ROOT / "requirements.lock").read_text(encoding="ascii").splitlines():
                name, _, _version = line.partition("==")
                if not name:
                    continue
                wheel = distribution(name)
                for entry in wheel.files or []:
                    parts = PurePosixPath(entry).parts
                    if parts and parts[0].endswith(".dist-info") and any(
                        part.lower().startswith(("license", "copying", "notice")) for part in parts[1:]
                    ):
                        put(output, f"licenses/{name}/{'/'.join(parts[1:])}", Path(wheel.locate_file(entry)))

        with ZipFile(pending) as checked:
            if len(set(checked.namelist())) != len(checked.namelist()) or checked.testzip() is not None:
                raise ValueError("Share ZIP did not pass integrity validation.")
        pending.replace(result)
    finally:
        pending.unlink(missing_ok=True)
    checksum = sha256(result)
    result.with_suffix(".zip.sha256").write_text(f"{checksum}  {result.name}\n", encoding="ascii")
    print(json.dumps({"file": str(result), "bytes": result.stat().st_size, "sha256": checksum, "source_commit": commit}))


if __name__ == "__main__":
    package()
