from __future__ import annotations
"""Shared state + helpers for the editor backend (see app.py create_app()).
Imported by every blueprint via `from core import *`; must not import blueprints."""

"""
Rudimentary local video editor.

Usage:
    MEDIA_DIR=/path/to/local/pulled/footage python3 editor/app.py

Then open http://127.0.0.1:5001
"""


import hashlib


import json


import os


import queue


import re


import shutil


import sqlite3


import subprocess


import tempfile


import threading


import time


import uuid


import zipfile


from datetime import datetime, timezone


from pathlib import Path


from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parent / ".env")


from flask import Flask, jsonify, request, send_file, render_template, Response


from db import get_conn, init_db, stamp_file_metadata, exiftool_available


from claude_client import (
    generate_rough_cut, suggest_content, analyze_frame, classify_thing_kind,
    infer_campaign_things, campaign_chat, pick_best_frame,
    match_things_in_text, propose_crop, revise_edit, deep_index_clip,
)


from drive_import import download_drive, probe_duration


from photos_import import (
    fetch_album_bases,
    download_one as photos_download_one,
    make_session as photos_make_session,
)


from composio_wrapper import list_toolkit_actions, initiate_connection, execute_action


import semantic


# Shared config/paths/constants live in config (the leaf module). Re-exported so
# `from core import *` in the blueprints keeps seeing these names unchanged.
from config import *  # noqa: F401,F403
from config import (
    MEDIA_DIR_RAW, MEDIA_DIR, ON_DEVICE_VISION_DEFAULT,
    VIDEO_EXTS, IMAGE_EXTS, MEDIA_EXTS, classify_kind,
    REPO_ROOT, CLIPS_OUT, REFERENCE_FRAMES, THUMB_CACHE, FACES_DIR, PROXY_CACHE,
)


def _no_cache_static(resp):
    if request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


# Media file ops (probe/proxy/quality/location/frame-sampling) live in media_files
# (leaf: config + db + jobs_runtime only). Re-exported so `from core import *` in
# the blueprints keeps seeing these names unchanged.
from media_files import *  # noqa: F401,F403
from media_files import (
    _proxy_jobs, _proxy_lock, _proxy_path_for, _generate_proxy, _ensure_proxy_async,
    _probe_dims, _laplacian_variance, _measure_quality, find_media_file, _media_roots,
    clip_media_status, _walk_media_files, _set_media, _run_media_verify_job,
    find_reference_frame, _hash_file, _scene_change_times, _motion_times,
    _interest_times, _sample_frames, _frame_at, _source_dims, _probe_fps,
    _has_audio, _display_dims,
)


# Background-job registry lives in jobs_runtime (leaf module: db + stdlib only).
# Re-exported so `from core import *` in the blueprints keeps seeing these names.
from jobs_runtime import *  # noqa: F401,F403
from jobs_runtime import (  # explicit: underscore names for clarity/linters
    _jobs, _jobs_lock, _JOB_FLUSH_INTERVAL, _JOB_FLUSH_KEYS, _job_flush, _new_job,
    _update_job, _job_row_snapshot, _job_snapshot, _job_set_proc, _job_is_cancelled,
    _run_cancellable,
)


# Settings + provenance memory (on-device toggle, remembered Photos albums) live
# in settings (leaf: db + config). Re-exported for `from core import *`.
from settings import *  # noqa: F401,F403
from settings import (
    _get_setting, _set_setting, _use_on_device, _photos_albums,
    _remember_photos_albums, _read_album_urls_from_xlsx,
)


# Timeline serialization + social export/reframe live in export (leaf: db, config,
# media_files, jobs_runtime). Re-exported for `from core import *`.
from export import *  # noqa: F401,F403
from export import (
    _EDIT_LIST_COLS, _serialize_timeline, _snapshot_edit, _replace_timeline,
    ASPECT_DIMS, _kb_keys, _clamp_rect, _auto_crop_from_regions, _reframe_filter,
    EXPORT_FPS, _even, _social_bitrate_args, _slugify, _unique_output_path,
    _run_export_job, _frame_item_from_regions, _apply_auto_framing, _window_dims,
    _apply_framing_edits,
)


# Content-understanding pipeline (vision/whisper/deep-index, things/regions,
# faces, motion, embeddings) + its job workers live in indexing. Re-exported so
# `from core import *` in the blueprints keeps seeing these names unchanged.
from indexing import *  # noqa: F401,F403
from indexing import (
    _indexing, _indexing_lock, _active_watchlist, _category_vocab, _frame_analysis,
    _norm_thing_name, _record_thing_matches, _record_regions, _pick_thing_thumbnail,
    _keyframe_bytes, _clip_timeline_texts, _thing_matches_text, _run_thing_scan_job,
    _run_region_scan_job, _person_centroids, _recluster_unnamed, _run_face_detect_job,
    _run_motion_job, _apply_hemisphere, _extract_gps, _index_clip_background,
    _clip_search_text, _embed_clip, _embed_worker, enqueue_embed, _run_embed_job,
    _quality_intent, get_whisper_model, _store_scene_segments, _deep_index_one,
    _store_deep_index, _transcript_segs, _run_deep_index_batch_job, _deep_index_todo,
    _run_deep_index_job, _store_speech_segments, _maybe_stamp, _upsert_thing,
    METADATA_FIELDS,
)


# The AI-facing view of the library (clip decoration, campaign membership, the
# generation pool + moments, campaign-context prompting) lives in catalog.
# Re-exported so `from core import *` in the blueprints keeps seeing these names.
from catalog import *  # noqa: F401,F403
from catalog import (
    _can_redownload, _decorate_clips, _campaign_membership, _pool_for_generation,
    _usable_for_generation, _attach_moments, _prompt_with_campaign_context,
)


# Media ingestion (register file -> clip + dedup + kick indexing, unzip, Drive/
# Photos import jobs, startup provenance backfill) lives in ingest. Re-exported
# so `from core import *` in the blueprints keeps seeing these names unchanged.
from ingest import *  # noqa: F401,F403
from ingest import (
    _run_drive_job, _run_photos_job, _clips_table_exists, _backfill_clip_sources,
    register_clip_file, _unique_dest, extract_media_from_zip, _backfill_proxies,
)


STATIC_DIR = Path(__file__).resolve().parent / "static"


PANEL_BUNDLES = {
    "editor": ["app.js", "chat.js", "crop.js"],
    "library": ["library.js", "map.js", "things.js", "faces.js", "motion.js", "cuts.js"],
    "campaigns": ["campaigns.js"],
}


# Re-export everything (including _underscore helpers/state) so blueprints can
# `from core import *` without rewriting any route references.
__all__ = [n for n in dir() if not n.startswith("__")]
