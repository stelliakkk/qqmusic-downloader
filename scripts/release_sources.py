"""Cache exact source distributions for the Windows portable release."""

import hashlib
import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "v2-sources"
QT = "https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules"
PYSIDE = "https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.12.0-src"
PYSIDE_MIRROR = "https://mirror.fi.ossplanet.net/qtproject/official_releases/QtForPython/pyside6/PySide6-6.12.0-src"


def open_url(url, *, timeout=45):
    return urlopen(Request(url, headers={"User-Agent": "QQMusicDownloader-source-bundle/2.0"}), timeout=timeout)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(name, url, expected=None):
    path = CACHE / name
    if path.is_file() and (expected is None or sha256(path) == expected):
        return {"file": name, "url": url, "sha256": sha256(path)}
    pending = path.with_name(f"{name}.part")
    for attempt in range(3):
        try:
            with open_url(url) as response, pending.open("wb") as output:
                digest = hashlib.sha256()
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
            actual = digest.hexdigest()
            if expected is not None and actual != expected:
                raise ValueError(f"SHA-256 mismatch for {name}")
            pending.replace(path)
            return {"file": name, "url": url, "sha256": actual}
        except (OSError, URLError, ValueError) as exc:
            pending.unlink(missing_ok=True)
            if attempt == 2 or (isinstance(exc, HTTPError) and exc.code == 404):
                raise
            time.sleep(1 + attempt)


def pypi_source(package, version):
    if package == "miniaudio" and version == "1.72":
        return download("pyminiaudio-v1.72.tar.gz", "https://codeload.github.com/irmen/pyminiaudio/tar.gz/refs/tags/v1.72")
    url = f"https://pypi.org/pypi/{package}/{version}/json"
    with open_url(url) as response:
        release = json.load(response)
    source = next((entry for entry in release["urls"] if entry["packagetype"] == "sdist"), None)
    if source is None:
        raise RuntimeError(f"No PyPI source distribution for {package} {version}")
    return download(source["filename"], source["url"], source["digests"]["sha256"])


def qt_source(name, base, expected=None):
    if expected is None:
        with open_url(f"{base}/{name}.sha256") as response:
            checksum = response.read(8192).decode("ascii").split()[0].lower()
        if not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError(f"Invalid Qt digest for {name}")
        expected = checksum
    try:
        return download(name, f"{base}/{name}", expected)
    except (OSError, URLError):
        if base != PYSIDE:
            raise
        return download(name, f"{PYSIDE_MIRROR}/{name}", expected)


def cached_sources(entries):
    """Re-index sources already downloaded and verified into the local cache."""
    sources = {
        "qtbase-everywhere-src-6.12.0.tar.xz": f"{QT}/qtbase-everywhere-src-6.12.0.tar.xz",
        "qtsvg-everywhere-src-6.12.0.tar.xz": f"{QT}/qtsvg-everywhere-src-6.12.0.tar.xz",
        "pyside-setup-everywhere-src-6.12.0.tar.xz": f"{PYSIDE}/pyside-setup-everywhere-src-6.12.0.tar.xz",
        "Python-3.14.5.tar.xz": "https://www.python.org/ftp/python/3.14.5/Python-3.14.5.tar.xz",
        "pyminiaudio-v1.72.tar.gz": "https://codeload.github.com/irmen/pyminiaudio/tar.gz/refs/tags/v1.72",
        **{
            f"openssl-{version}.tar.gz": f"https://codeload.github.com/openssl/openssl/tar.gz/refs/tags/openssl-{version}"
            for version in ("3.0.20", "4.0.3")
        },
    }
    cached = [path.name for path in CACHE.iterdir() if path.is_file()]
    for name, version in entries:
        key = re.sub(r"[-_.]+", "-", name.lower())
        if key in {"pyside6-essentials", "shiboken6", "miniaudio"}:
            continue
        matches = [filename for filename in cached for suffix in (".tar.gz", ".zip", ".tar.xz")
                   if filename.endswith(f"-{version}{suffix}")
                   and re.sub(r"[-_.]+", "-", filename[: -len(f"-{version}{suffix}")].lower()) == key]
        if len(matches) != 1:
            raise ValueError(f"Expected one previously verified source archive for {name}=={version}")
        sources[matches[0]] = f"https://pypi.org/project/{name}/{version}/#files"
    return [
        {"file": filename, "url": url, "sha256": sha256(CACHE / filename)}
        for filename, url in sources.items()
    ]


def write_manifest(collected):
    collected.sort(key=lambda row: row["file"].lower())
    (CACHE / "manifest.json").write_text(json.dumps(collected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cached", action="store_true", help="Re-index an already verified source cache")
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    entries = [row.split("==", 1) for row in (ROOT / "requirements.lock").read_text(encoding="ascii").splitlines() if row.strip()]
    if args.cached:
        write_manifest(cached_sources(entries))
        return 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [
            pool.submit(pypi_source, package, version) for package, version in entries
            if package.lower().replace("_", "-") not in {"pyside6-essentials", "shiboken6"}
        ]
        jobs.extend((
            pool.submit(qt_source, "qtbase-everywhere-src-6.12.0.tar.xz", QT),
            pool.submit(qt_source, "qtsvg-everywhere-src-6.12.0.tar.xz", QT),
            pool.submit(qt_source, "pyside-setup-everywhere-src-6.12.0.tar.xz", PYSIDE,
                        "099c1a597cf33c1b000e11cf68a0b82d216fb802bf81726efc720394f10c7751"),
            pool.submit(download, "Python-3.14.5.tar.xz", "https://www.python.org/ftp/python/3.14.5/Python-3.14.5.tar.xz"),
        ))
        for version in ("3.0.20", "4.0.3"):
            tag = f"openssl-{version}"
            jobs.append(pool.submit(download, f"{tag}.tar.gz", f"https://codeload.github.com/openssl/openssl/tar.gz/refs/tags/{tag}"))
        collected = []
        failures = []
        for job in jobs:
            try:
                result = job.result()
                collected.append(result)
                print(result["file"], flush=True)
            except Exception as exc:
                failures.append(str(exc))
        if failures:
            print("\n".join(failures), file=sys.stderr, flush=True)
            return 1
    write_manifest(collected)
    return 0


if __name__ == "__main__":
    sys.exit(main())
