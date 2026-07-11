from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from urllib.parse import unquote

import requests

# A browser-like UA -- Google serves a stripped page (and sometimes a consent
# interstitial) to unknown clients.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Media items in a shared-album page are googleusercontent URLs under the /pw/
# path, each carrying an inline size suffix (=w600-h315-p-k). We match the base
# (up to the "=") and download it at original resolution later. Avatars live under
# /a/ and don't match this pattern.
_MEDIA_RE = re.compile(r'https://lh3\.googleusercontent\.com/pw/[\w\-]+')


def is_photos_url(url: str) -> bool:
    return "photos.app.goo.gl" in url or "photos.google.com/share" in url


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(_HEADERS)
    return session


def _extract_media_base_urls(html: str) -> list[str]:
    """Pull the deduped list of media base URLs out of a shared-album page."""
    seen: set[str] = set()
    ordered: list[str] = []
    for m in _MEDIA_RE.finditer(html):
        base = m.group(0)  # base with the size suffix already excluded by the regex
        if base in seen:
            continue
        seen.add(base)
        ordered.append(base)
    return ordered


def fetch_album_bases(url: str, session: requests.Session | None = None) -> list[str]:
    """Load a *public* Google Photos shared-album page and return the list of media
    base URLs. Raises RuntimeError with an actionable message if the album isn't
    reachable (not public, empty, etc.). Does no downloading -- call download_one
    for each returned base."""
    session = session or make_session()
    try:
        page = session.get(url, timeout=30, allow_redirects=True)
        page.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Couldn't load the album page: {e}") from e

    if "accounts.google.com" in page.url or "signin" in page.url.lower():
        raise RuntimeError(
            "Google redirected to sign-in -- this album isn't public. Open it, "
            "choose Share and set it to \"Anyone with the link\", then retry."
        )

    bases = _extract_media_base_urls(page.text)
    if not bases:
        raise RuntimeError(
            "No media found on the album page. Make sure the link is a Google "
            "Photos shared album set to \"Anyone with the link\" and that it isn't empty."
        )
    return bases


def _filename_for(resp: requests.Response, base_url: str, index: int) -> str:
    """Work out the original filename from the download response headers,
    falling back to the URL token + a content-type-derived extension."""
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
    if m:
        name = unquote(m.group(1)).strip()
        if name:
            return Path(name).name

    token = base_url.rstrip("/").split("/")[-1][:24]
    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
    ext = mimetypes.guess_extension(ctype) or ""
    if ext == ".jpe":
        ext = ".jpg"
    return f"gphotos_{index:03d}_{token}{ext}"


def download_one(base: str, dest_dir: Path, index: int,
                 session: requests.Session | None = None) -> Path:
    """Download a single album item (photo or video) at original resolution."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    session = session or make_session()

    # "=dv" returns the original *video* file; on a photo it 404s. So try it first,
    # and fall back to "=d" (the original still image) for photos -- "=d" on a video
    # only yields a poster frame, so the order matters.
    resp = session.get(base + "=dv", timeout=300, stream=True)
    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
    if resp.status_code != 200 or not ctype.startswith("video/"):
        resp.close()
        resp = session.get(base + "=d", timeout=300, stream=True)
    resp.raise_for_status()

    name = _filename_for(resp, base, index)
    dest = dest_dir / name
    with open(dest, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            if chunk:
                fh.write(chunk)
    return dest


def download_photos_album(url: str, dest_dir: Path, on_progress=None) -> list[Path]:
    """Crawl a public Google Photos shared-album link and download every item into
    dest_dir. Returns the downloaded paths. `on_progress`, if given, is called as
    on_progress(done, total, current_name) after enumeration and each download.

    Only works for albums shared as "anyone with the link" -- there is no official
    API for arbitrary shared albums, so this scrapes the public page."""
    session = make_session()
    bases = fetch_album_bases(url, session)
    total = len(bases)
    if on_progress:
        on_progress(0, total, None)

    downloaded: list[Path] = []
    errors: list[str] = []
    for i, base in enumerate(bases):
        try:
            dest = download_one(base, dest_dir, i, session)
            downloaded.append(dest)
            if on_progress:
                on_progress(i + 1, total, dest.name)
        except Exception as e:
            errors.append(f"item {i}: {e}")
            if on_progress:
                on_progress(i + 1, total, None)

    if not downloaded:
        raise RuntimeError(
            "Found media on the page but every download failed. "
            + (errors[0] if errors else "")
        )
    return downloaded
