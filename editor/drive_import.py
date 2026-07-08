from __future__ import annotations

import subprocess
from pathlib import Path

import gdown


def probe_duration(path: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, check=True,
        )
        return round(float(result.stdout.strip()), 1)
    except Exception:
        return None


def download_drive_file(url: str, dest_dir: Path) -> Path:
    """Download a Google Drive share link into dest_dir, keeping the original filename."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    output = gdown.download(url=url, output=f"{dest_dir}/", fuzzy=True, quiet=True)
    if not output:
        raise RuntimeError(
            "Download failed -- check the link is set to \"anyone with the link\" "
            "and points at a single file (not a folder)."
        )
    return Path(output)
