"""Case 8: XLSX round-trip. The importer (migrate_xlsx) is the risky real code path;
drive it against a temp workbook and assert description/category survive into the DB.

(Note: the current importer maps File/Dur/Category/What's in it/Status — it does not
read a Context column, so context isn't asserted on the import side.)"""
import openpyxl

import db
import migrate_xlsx


def test_xlsx_import_preserves_description_and_category(monkeypatch, tmp_path, app):
    # Build a workbook shaped like content_intake_log.xlsx's "Video Index (A2)" sheet.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = migrate_xlsx.SHEET_NAME
    headers = ["File", "Dur (s)", "Category", "What's in it", "Status"]
    ws.append(headers)
    ws.append(["RT_CLIP", 12.5, "Wildlife", "a swallowtail on pipevine", "indexed"])
    xlsx = tmp_path / "log.xlsx"
    wb.save(xlsx)

    monkeypatch.setattr(migrate_xlsx, "LOG_PATH", xlsx)
    # RT_CLIP has no local file; --include-missing imports it regardless (the guard
    # against resurrecting missing rows is exercised in the test below).
    migrate_xlsx.main(["--include-missing"])   # xlsx -> clips table

    conn = db.get_conn()
    row = conn.execute(
        "SELECT category, description, duration_s FROM clips WHERE file_stem = 'RT_CLIP'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["category"] == "Wildlife"
    assert row["description"] == "a swallowtail on pipevine"
    assert row["duration_s"] == 12.5


def test_xlsx_import_skips_rows_with_no_local_file(monkeypatch, tmp_path, app):
    """Default (no --include-missing): a spreadsheet row is imported only if its media
    file exists locally, so a stale xlsx can't resurrect pruned catalog ghosts."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = migrate_xlsx.SHEET_NAME
    ws.append(["File", "Dur (s)", "Category", "What's in it", "Status"])
    ws.append(["PRESENT_CLIP", 3.0, "Wildlife", "has a file", "indexed"])
    ws.append(["GHOST_CLIP", 4.0, "Wildlife", "no file on disk", "indexed"])
    xlsx = tmp_path / "log.xlsx"
    wb.save(xlsx)

    # Only PRESENT_CLIP has a media file in MEDIA_DIR.
    (migrate_xlsx.MEDIA_DIR / "PRESENT_CLIP.mp4").write_bytes(b"\x00")

    monkeypatch.setattr(migrate_xlsx, "LOG_PATH", xlsx)
    migrate_xlsx.main([])   # default: skip missing

    conn = db.get_conn()
    stems = {r["file_stem"] for r in conn.execute("SELECT file_stem FROM clips").fetchall()}
    conn.close()
    assert "PRESENT_CLIP" in stems
    assert "GHOST_CLIP" not in stems   # the ghost is not resurrected
