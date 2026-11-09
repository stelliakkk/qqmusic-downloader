"""Import the first version's local account into an extracted personal copy."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from qq_downloader.storage import Storage, atomic_write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="V1 folder containing QQMusicDownloader.exe")
    parser.add_argument("target", type=Path, help="V2 personal folder containing QQMusicDownloader.exe")
    args = parser.parse_args()
    source, target = args.source.resolve(), args.target.resolve()
    if source == target or not all((folder / "QQMusicDownloader.exe").is_file() for folder in (source, target)):
        parser.error("Choose separate, extracted V1 and V2 application folders.")
    old, new = Storage(source / "data"), Storage(target / "data")
    if new.credential_path.exists() or new.settings_path.exists():
        parser.error("The V2 target already has local account data or settings.")
    credential = old.load_credential()
    if credential is None:
        parser.error("No readable V1 login. Start V2 and scan with your own QQ account.")
    new.save_credential(credential)
    for filename in ("qqmusic_device.json", "qqmusic_device.cache.json"):
        src, dst = old.data / filename, new.data / filename
        if src.is_file() and not dst.exists():
            atomic_write(dst, src.read_bytes())
    new.set_download_folder(old.download_folder(source / "downloads"))
    print("Imported local Windows login and download folder.")


if __name__ == "__main__":
    main()
