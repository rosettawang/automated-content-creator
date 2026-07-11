from __future__ import annotations

import re
import subprocess
from pathlib import Path

import gdown


def _extract_drive_id(url: str) -> str | None:
    """Pull the file ID out of the common Google Drive share-link forms:
        https://drive.google.com/file/d/<ID>/view
        https://drive.google.com/open?id=<ID>
        https://drive.google.com/uc?id=<ID>&export=download
    Returns None if the string doesn't look like a Drive link (e.g. it's a bare ID)."""
    patterns = (
        r"/file/d/([\w-]+)",
        r"[?&]id=([\w-]+)",
    )
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


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


def is_folder_url(url: str) -> bool:
    return "/folders/" in url or "/drive/folders/" in url


def download_drive(url: str, dest_dir: Path) -> list[Path]:
    """Download a Drive share link -- either a single file or an entire folder --
    into dest_dir. Always returns a list of the downloaded file paths."""
    if is_folder_url(url):
        return download_drive_folder(url, dest_dir)
    return [download_drive_file(url, dest_dir)]


def download_drive_folder(url: str, dest_dir: Path) -> list[Path]:
    """Download every file in a public Drive folder into dest_dir."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    import contextlib
    import io

    buf = io.StringIO()
    outputs = None
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            # use_cookies=False keeps it fully unauthenticated (public folders only)
            outputs = gdown.download_folder(
                url=url, output=str(dest_dir), quiet=False, use_cookies=False
            )
    except Exception as e:
        detail = (buf.getvalue().strip() + " " + str(e)).strip()
        raise RuntimeError(_diagnose(detail)) from e

    if not outputs:
        detail = buf.getvalue().strip()
        raise RuntimeError(
            _diagnose(detail) if detail else
            "Folder download returned no files -- make sure the folder is shared as "
            "\"Anyone with the link\" and isn't empty."
        )
    return [Path(p) for p in outputs]


def download_drive_file(url: str, dest_dir: Path) -> Path:
    """Download a Google Drive share link into dest_dir, keeping the original filename."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    # gdown >=5 dropped the `fuzzy` kwarg, so parse the file ID ourselves and pass
    # `id=`. Fall back to treating the input as a bare file ID.
    file_id = _extract_drive_id(url) or url.strip()

    # gdown writes the real reason (permission denied, sign-in required, quota
    # exceeded, etc.) to stdout/stderr; capture it so we can surface it instead of a
    # generic message.
    import contextlib
    import io

    buf = io.StringIO()
    output = None
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            output = gdown.download(id=file_id, output=f"{dest_dir}/", quiet=False)
    except Exception as e:  # gdown raises on some permission/quota failures
        detail = (buf.getvalue().strip() + " " + str(e)).strip()
        raise RuntimeError(_diagnose(detail)) from e

    if not output:
        raise RuntimeError(_diagnose(buf.getvalue().strip()))
    return Path(output)


def _diagnose(detail: str) -> str:
    """Turn gdown's raw output into an actionable message."""
    low = detail.lower()
    if "permission" in low or "access denied" in low or "cannot retrieve the public link" in low:
        return (
            "Google denied access. The file is not public -- open it, choose "
            "Share -> General access -> \"Anyone with the link\", then retry. "
            "(Workspace/org accounts sometimes restrict this to your organization only.)"
        )
    if "sign in" in low or "signin" in low or "accounts.google.com" in low:
        return (
            "Google is asking for sign-in, which means the file isn't publicly "
            "shared. Set it to \"Anyone with the link\" and retry."
        )
    if "quota" in low or "too many users" in low:
        return "Google download quota for this file has been exceeded -- try again later."
    base = "Download failed -- set the link to \"Anyone with the link\" and confirm it's a single file."
    return f"{base}\n(gdown said: {detail})" if detail else base
