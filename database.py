"""
database.py
Automated MCAP Analysis and Report Visualization System
SQLite database - matches SDD class diagram.
"""

import os
import sqlite3
import json

# Absolute path — safe regardless of the working directory the process
# was launched from (e.g. gunicorn on Render).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, 'mcap_analysis.db')

EVENT_CLASSIFICATION  = ["Below -4", "-4 to -7", "-7 to -10"]
SAFETY_FUNCTION_STATUS = ["Active", "Inactive", "N/A"]
ANALYSIS_STATUS       = ["Draft", "In Progress", "Completed"]


def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS datasets (
        dataset_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        created_by_name   TEXT,
        original_filename TEXT NOT NULL,
        stored_path       TEXT NOT NULL,
        upload_time       TEXT DEFAULT (datetime('now')),
        status            TEXT DEFAULT 'Processing',
        validation_status TEXT,
        extraction_status TEXT,
        error_message     TEXT,
        created_at        TEXT DEFAULT (datetime('now')),
        updated_at        TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS validation_results (
        validation_id  INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset_id     INTEGER NOT NULL REFERENCES datasets(dataset_id),
        is_valid       INTEGER NOT NULL,
        checked_at     TEXT DEFAULT (datetime('now')),
        missing_topics TEXT,
        notes          TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS extraction_results (
        extraction_id INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset_id    INTEGER NOT NULL REFERENCES datasets(dataset_id),
        extracted_at  TEXT DEFAULT (datetime('now')),
        summary       TEXT,
        result_ref    TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS events (
        event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        extraction_id   INTEGER NOT NULL REFERENCES extraction_results(extraction_id),
        event_type      TEXT,
        start_timestamp INTEGER,
        end_timestamp   INTEGER,
        key_values      TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS report_drafts (
        draft_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset_id       INTEGER NOT NULL REFERENCES datasets(dataset_id),
        auto_filled_data TEXT,
        status           TEXT DEFAULT 'Draft',
        verified_by_name TEXT,
        summary_data     TEXT,
        created_at       TEXT DEFAULT (datetime('now')),
        updated_at       TEXT DEFAULT (datetime('now'))
    )""")
    try:
        c.execute("ALTER TABLE report_drafts ADD COLUMN summary_data TEXT")
    except Exception:
        pass  # Column already exists

    c.execute("""CREATE TABLE IF NOT EXISTS manual_inputs (
        manual_input_id INTEGER PRIMARY KEY AUTOINCREMENT,
        draft_id        INTEGER NOT NULL REFERENCES report_drafts(draft_id),
        field_name      TEXT NOT NULL,
        field_type      TEXT,
        is_required     INTEGER DEFAULT 1,
        field_value     TEXT,
        updated_at      TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS reports (
        report_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        draft_id     INTEGER NOT NULL REFERENCES report_drafts(draft_id),
        dataset_id   INTEGER NOT NULL REFERENCES datasets(dataset_id),
        pdf_filename TEXT,
        pdf_path     TEXT,
        generated_at TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS audit_logs (
        log_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset_id INTEGER,
        action     TEXT NOT NULL,
        timestamp  TEXT DEFAULT (datetime('now')),
        details    TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS technicians (
        technician_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT NOT NULL UNIQUE,
        created_at    TEXT DEFAULT (datetime('now'))
    )""")

    for default_name in ["Ammar Bakhtiar", "Adib", "Fudail"]:
        c.execute("INSERT OR IGNORE INTO technicians (name) VALUES (?)", (default_name,))

    try:
        c.execute("ALTER TABLE datasets ADD COLUMN file_hash TEXT")
    except Exception:
        pass  # Column already exists

    conn.commit()
    conn.close()


def log_action(dataset_id, action, details=None):
    conn = get_db()
    conn.execute(
        "INSERT INTO audit_logs (dataset_id, action, details) VALUES (?,?,?)",
        (dataset_id, action, details)
    )
    conn.commit()
    conn.close()


def create_dataset(original_filename, stored_path, created_by_name=None, file_hash=None):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO datasets (original_filename, stored_path, created_by_name, status, file_hash) VALUES (?,?,?,'Processing',?)",
        (original_filename, stored_path, created_by_name, file_hash)
    )
    dataset_id = c.lastrowid
    conn.commit()
    conn.close()
    log_action(dataset_id, "UPLOAD_COMPLETED", f"File: {original_filename}")
    return dataset_id


def update_dataset_status(dataset_id, status, validation_status=None,
                          extraction_status=None, error_message=None):
    conn = get_db()
    conn.execute("""
        UPDATE datasets SET
            status            = ?,
            validation_status = COALESCE(?, validation_status),
            extraction_status = COALESCE(?, extraction_status),
            error_message     = COALESCE(?, error_message),
            updated_at        = datetime('now')
        WHERE dataset_id = ?
    """, (status, validation_status, extraction_status, error_message, dataset_id))
    conn.commit()
    conn.close()


def get_dataset(dataset_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM datasets WHERE dataset_id=?", (dataset_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def save_validation_result(dataset_id, is_valid, missing_topics=None, notes=None):
    conn = get_db()
    conn.execute(
        "INSERT INTO validation_results (dataset_id, is_valid, missing_topics, notes) VALUES (?,?,?,?)",
        (dataset_id, 1 if is_valid else 0,
         json.dumps(missing_topics) if missing_topics else None, notes)
    )
    conn.commit()
    conn.close()
    log_action(dataset_id, "VALIDATION_COMPLETED", f"Valid: {is_valid}")


def save_extraction_result(dataset_id, extraction_data):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO extraction_results (dataset_id, summary) VALUES (?,?)",
        (dataset_id, json.dumps(extraction_data))
    )
    extraction_id = c.lastrowid
    c.execute(
        "INSERT INTO events (extraction_id, event_type, key_values) VALUES (?,?,?)",
        (extraction_id,
         extraction_data.get("braking_severity", "Unknown"),
         json.dumps({
             "max_braking":       extraction_data.get("max_braking"),
             "velocity_at_event": extraction_data.get("velocity_at_event"),
             "odd":               extraction_data.get("odd"),
             "mrm_triggered":     extraction_data.get("mrm_triggered"),
             "scg":               extraction_data.get("scg"),
         }))
    )
    conn.commit()
    conn.close()
    log_action(dataset_id, "EXTRACTION_COMPLETED",
               f"Braking: {extraction_data.get('max_braking')}")
    return extraction_id


def get_extraction_result(dataset_id):
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM extraction_results WHERE dataset_id=?
        ORDER BY extraction_id DESC LIMIT 1
    """, (dataset_id,)).fetchone()
    conn.close()
    if row:
        r = dict(row)
        r["summary"] = json.loads(r["summary"]) if r["summary"] else {}
        return r
    return None


def create_report_draft(dataset_id, auto_filled_data):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO report_drafts (dataset_id, auto_filled_data, status) VALUES (?,?,'Draft')",
        (dataset_id, json.dumps(auto_filled_data))
    )
    draft_id = c.lastrowid

    for field_name, field_type, is_required in [
        ("event_classification", "dropdown",  1),
        ("vehicle_van",          "text",      1),
        ("event_date",           "date",      1),
        ("event_timestamp",      "datetime",  1),
        ("technician_remarks",   "textarea",  0),
        ("created_by_name",      "dropdown",  1),
    ]:
        c.execute(
            "INSERT INTO manual_inputs (draft_id, field_name, field_type, is_required) VALUES (?,?,?,?)",
            (draft_id, field_name, field_type, is_required)
        )

    conn.commit()
    conn.close()
    log_action(dataset_id, "DRAFT_CREATED", f"Draft ID: {draft_id}")
    return draft_id


def get_report_draft(draft_id):
    conn = get_db()
    draft = conn.execute("SELECT * FROM report_drafts WHERE draft_id=?", (draft_id,)).fetchone()
    if not draft:
        conn.close()
        return None
    draft = dict(draft)
    draft["auto_filled_data"] = json.loads(draft["auto_filled_data"]) if draft["auto_filled_data"] else {}
    inputs = conn.execute("SELECT * FROM manual_inputs WHERE draft_id=?", (draft_id,)).fetchall()
    draft["manual_inputs"] = [dict(i) for i in inputs]
    conn.close()
    return draft


def save_manual_inputs(draft_id, form_data, verified_by_name=None):
    conn = get_db()
    for field_name, field_value in form_data.items():
        conn.execute("""
            UPDATE manual_inputs SET field_value=?, updated_at=datetime('now')
            WHERE draft_id=? AND field_name=?
        """, (field_value, draft_id, field_name))
    if verified_by_name:
        conn.execute("""
            UPDATE report_drafts SET verified_by_name=?, updated_at=datetime('now')
            WHERE draft_id=?
        """, (verified_by_name, draft_id))
        # Also mirror into manual_inputs so is_draft_complete() can see it
        conn.execute("""
            UPDATE manual_inputs SET field_value=?, updated_at=datetime('now')
            WHERE draft_id=? AND field_name='verified_by_name'
        """, (verified_by_name, draft_id))
    conn.commit()
    conn.close()


def update_draft_status(draft_id, new_status):
    conn = get_db()
    conn.execute(
        "UPDATE report_drafts SET status=?, updated_at=datetime('now') WHERE draft_id=?",
        (new_status, draft_id)
    )
    draft = conn.execute("SELECT dataset_id FROM report_drafts WHERE draft_id=?", (draft_id,)).fetchone()
    conn.commit()
    conn.close()
    if draft:
        log_action(draft["dataset_id"], "STATUS_UPDATED", f"New status: {new_status}")


def is_draft_complete(draft_id):
    conn = get_db()
    missing = conn.execute("""
        SELECT COUNT(*) as cnt FROM manual_inputs
        WHERE draft_id=? AND is_required=1
          AND (field_value IS NULL OR field_value='')
    """, (draft_id,)).fetchone()
    conn.close()
    return missing["cnt"] == 0


def save_summary_data(draft_id, summary_data):
    """Persist user-edited summary fields in the report_drafts.summary_data column."""
    conn = get_db()
    row = conn.execute(
        "SELECT dataset_id FROM report_drafts WHERE draft_id=?", (draft_id,)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE report_drafts SET summary_data=?, updated_at=datetime('now') WHERE draft_id=?",
            (json.dumps(summary_data), draft_id)
        )
        conn.commit()
        dataset_id = row["dataset_id"]
    conn.close()
    if row:
        log_action(dataset_id, "SUMMARY_SAVED", f"Draft ID: {draft_id}")


def save_report(draft_id, dataset_id, pdf_filename, pdf_path, generated_at=None):
    conn = get_db()
    c = conn.cursor()
    if generated_at:
        c.execute(
            "INSERT INTO reports (draft_id, dataset_id, pdf_filename, pdf_path, generated_at)"
            " VALUES (?,?,?,?,?)",
            (draft_id, dataset_id, pdf_filename, pdf_path, generated_at)
        )
    else:
        c.execute(
            "INSERT INTO reports (draft_id, dataset_id, pdf_filename, pdf_path) VALUES (?,?,?,?)",
            (draft_id, dataset_id, pdf_filename, pdf_path)
        )
    report_id = c.lastrowid
    conn.commit()
    conn.close()
    log_action(dataset_id, "REPORT_GENERATED", f"File: {pdf_filename}")
    return report_id


def get_all_reports():
    conn = get_db()
    rows = conn.execute("""
        SELECT r.*, d.original_filename, d.created_by_name,
               rd.status, rd.verified_by_name,
               (SELECT mi.field_value FROM manual_inputs mi
                WHERE mi.draft_id = r.draft_id AND mi.field_name = 'event_classification'
                LIMIT 1) AS event_classification
        FROM reports r
        JOIN datasets d  ON r.dataset_id = d.dataset_id
        JOIN report_drafts rd ON r.draft_id = rd.draft_id
        ORDER BY r.generated_at DESC
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_all_drafts_with_details():
    """Return all report drafts (including Draft/In Progress) for the Reports page."""
    conn = get_db()
    rows = conn.execute("""
        SELECT
            rd.draft_id,
            rd.status,
            rd.created_at,
            d.original_filename,
            d.created_by_name,
            r.report_id,
            r.generated_at,
            (SELECT mi.field_value FROM manual_inputs mi
             WHERE mi.draft_id = rd.draft_id AND mi.field_name = 'event_classification'
             LIMIT 1) AS event_classification
        FROM report_drafts rd
        JOIN datasets d ON rd.dataset_id = d.dataset_id
        LEFT JOIN reports r ON rd.draft_id = r.draft_id
        ORDER BY rd.created_at DESC
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def find_dataset_by_hash(file_hash):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM datasets WHERE file_hash = ?", (file_hash,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_draft_by_dataset(dataset_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM report_drafts WHERE dataset_id = ? ORDER BY draft_id DESC LIMIT 1",
        (dataset_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_audit_logs(limit=200):
    conn = get_db()
    rows = conn.execute("""
        SELECT al.*, d.original_filename
        FROM audit_logs al
        LEFT JOIN datasets d ON al.dataset_id = d.dataset_id
        ORDER BY al.timestamp DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_report(report_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM reports WHERE report_id=?", (report_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# SQLite has no parameter-binding syntax for identifiers (table/column
# names) — only values can use "?" placeholders — so get_count()'s table
# name is validated against this whitelist instead of being interpolated
# unchecked. All current call sites already pass a hardcoded literal, but
# this closes the gap if that ever changes.
_COUNTABLE_TABLES = {"datasets", "report_drafts", "reports"}


def get_count(table_name):
    if table_name not in _COUNTABLE_TABLES:
        raise ValueError(f"Invalid table name: {table_name!r}")
    conn = get_db()
    count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    conn.close()
    return count


def get_technician_count():
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM technicians").fetchone()[0]
    conn.close()
    return count


def get_recent_analyses(limit=5):
    conn = get_db()
    rows = conn.execute("""
        SELECT
            rd.draft_id,
            d.original_filename,
            rd.status,
            rd.created_at,
            mi.field_value AS created_by_name
        FROM report_drafts rd
        JOIN datasets d ON rd.dataset_id = d.dataset_id
        LEFT JOIN manual_inputs mi
            ON rd.draft_id = mi.draft_id
            AND mi.field_name = 'created_by_name'
        ORDER BY rd.created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_technicians():
    conn = get_db()
    rows = conn.execute("SELECT * FROM technicians ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_technician(name):
    conn = get_db()
    try:
        conn.execute("INSERT INTO technicians (name) VALUES (?)", (name,))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def delete_technician(name):
    conn = get_db()
    conn.execute("DELETE FROM technicians WHERE name = ?", (name,))
    conn.commit()
    conn.close()


def delete_draft(draft_id):
    conn = get_db()
    draft = conn.execute(
        "SELECT dataset_id FROM report_drafts WHERE draft_id=?", (draft_id,)
    ).fetchone()
    if not draft:
        conn.close()
        return
    dataset_id = draft["dataset_id"]
    dataset = conn.execute(
        "SELECT stored_path FROM datasets WHERE dataset_id=?", (dataset_id,)
    ).fetchone()
    stored_path = dataset["stored_path"] if dataset else None
    conn.execute("DELETE FROM reports WHERE draft_id=?", (draft_id,))
    conn.execute("DELETE FROM manual_inputs WHERE draft_id=?", (draft_id,))
    conn.execute("DELETE FROM report_drafts WHERE draft_id=?", (draft_id,))
    conn.execute("""DELETE FROM events WHERE extraction_id IN
        (SELECT extraction_id FROM extraction_results WHERE dataset_id=?)""",
        (dataset_id,))
    conn.execute("DELETE FROM extraction_results WHERE dataset_id=?", (dataset_id,))
    conn.execute("DELETE FROM validation_results WHERE dataset_id=?", (dataset_id,))
    conn.execute("DELETE FROM audit_logs WHERE dataset_id=?", (dataset_id,))
    conn.execute("DELETE FROM datasets WHERE dataset_id=?", (dataset_id,))
    conn.commit()
    conn.close()
    if stored_path and os.path.exists(stored_path):
        try:
            os.remove(stored_path)
        except Exception:
            pass


def update_extraction_result(dataset_id, extraction_data):
    conn = get_db()
    conn.execute(
        "UPDATE extraction_results SET summary=?, extracted_at=datetime('now') WHERE dataset_id=?",
        (json.dumps(extraction_data), dataset_id)
    )
    conn.commit()
    conn.close()
    log_action(dataset_id, "REEXTRACTION_COMPLETED",
               f"Braking: {extraction_data.get('max_braking')}")


def update_draft_auto_data(draft_id, auto_filled_data):
    conn = get_db()
    conn.execute(
        "UPDATE report_drafts SET auto_filled_data=?, updated_at=datetime('now') WHERE draft_id=?",
        (json.dumps(auto_filled_data), draft_id)
    )
    conn.commit()
    conn.close()
