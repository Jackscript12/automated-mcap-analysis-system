"""
app.py
Automated MCAP Analysis and Report Visualization System
All 9 use cases from SRS implemented.
"""

from dotenv import load_dotenv
import os
import secrets
load_dotenv()

import time
import uuid
import base64
import json
import hashlib
from datetime import datetime, timezone, timedelta
from flask import (
    Flask, render_template, request, redirect, url_for,
    send_file, send_from_directory, abort, jsonify, session,
)
from flask_wtf.csrf import CSRFProtect

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
    Image as RLImage,
)

from database import (
    get_db,
    init_db,
    log_action,
    create_dataset,
    update_dataset_status,
    get_dataset,
    save_validation_result,
    save_extraction_result,
    get_extraction_result,
    create_report_draft,
    get_report_draft,
    save_manual_inputs,
    update_draft_status,
    is_draft_complete,
    save_report,
    get_all_drafts_with_details,
    find_dataset_by_hash,
    get_draft_by_dataset,
    get_audit_logs,
    get_report,
    get_count,
    get_technician_count,
    get_recent_analyses,
    get_all_technicians,
    add_technician,
    delete_technician,
    delete_draft,
    update_extraction_result,
    update_draft_auto_data,
    EVENT_CLASSIFICATION,
    SAFETY_FUNCTION_STATUS,
    ANALYSIS_STATUS,
)
from extraction import extract_mcap_data, validate_mcap

# ─────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get(
    'FLASK_SECRET_KEY',
    secrets.token_hex(32)  # fallback if .env missing
)
csrf = CSRFProtect(app)

_MYT = timezone(timedelta(hours=8))


def to_myt(dt_str):
    """Convert a UTC SQLite datetime string to a formatted MYT display string."""
    if not dt_str:
        return "N/A"
    s = str(dt_str).strip()
    if "MYT" in s:
        try:
            dt = datetime.fromisoformat(s.replace(" MYT", "").strip())
            return dt.strftime("%d %b %Y, %H:%M MYT")
        except Exception:
            return s
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_MYT).strftime("%d %b %Y, %H:%M MYT")
    except Exception:
        return s


@app.template_filter("to_myt")
def to_myt_filter(value):
    return to_myt(value)

UPLOAD_FOLDER      = "uploads"
REPORTS_FOLDER     = "reports"
SCREENSHOTS_FOLDER = os.path.join(os.path.dirname(__file__), "screenshots")
ALLOWED_EXTENSIONS = {"mcap"}

app.config["UPLOAD_FOLDER"]  = UPLOAD_FOLDER
app.config["REPORTS_FOLDER"] = REPORTS_FOLDER

os.makedirs(UPLOAD_FOLDER,      exist_ok=True)
os.makedirs(REPORTS_FOLDER,     exist_ok=True)
os.makedirs(SCREENSHOTS_FOLDER, exist_ok=True)

# Expose is_draft_complete so analysis.html can call it directly in Jinja
app.jinja_env.globals["is_draft_complete"] = is_draft_complete

with app.app_context():
    init_db()


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def _allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _get_report_by_draft(draft_id):
    """Return the most recent report row for a given draft, or None."""
    conn = get_db()
    row  = conn.execute(
        "SELECT * FROM reports WHERE draft_id=? ORDER BY report_id DESC LIMIT 1",
        (draft_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def compute_file_hash(filepath):
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()


def _safe_remove(filepath, retries=5, delay=0.5):
    """
    Attempt to remove a file with retries on
    Windows file-lock errors (WinError 32).
    """
    for attempt in range(retries):
        try:
            os.remove(filepath)
            return True
        except PermissionError:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                return False  # give up silently
    return False


def _upload_error(message):
    return render_template(
        "upload.html",
        message=message,
        message_type="error",
        technicians=get_all_technicians(),
    )


# ─────────────────────────────────────────────
# UCD000 — Home
# ─────────────────────────────────────────────
@app.route("/")
def home():
    uploaded_files    = get_count("datasets")
    analyzed_events   = get_count("report_drafts")
    generated_reports = get_count("reports")
    active_technicians = get_technician_count()
    recent_analyses   = get_recent_analyses()
    for item in recent_analyses:
        item["created_at"] = to_myt(item["created_at"])
    return render_template("index.html",
        uploaded_files=uploaded_files,
        analyzed_events=analyzed_events,
        generated_reports=generated_reports,
        active_technicians=active_technicians,
        recent_analyses=recent_analyses,
    )


# ─────────────────────────────────────────────
# Analysis index — redirect to latest draft or upload
# ─────────────────────────────────────────────
@app.route("/analysis")
def analysis_index():
    """Redirect navbar '/analysis' link to the most recent draft, or upload if none exists."""
    conn = get_db()
    row  = conn.execute(
        "SELECT draft_id FROM report_drafts ORDER BY draft_id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row:
        return redirect(url_for("view_analysis", draft_id=row["draft_id"]))
    return redirect(url_for("upload"))


# ─────────────────────────────────────────────
# UCD001 — Upload MCAP file
# UCD002 — Validate MCAP file  (automatic)
# UCD003 — Extract event data  (automatic)
# ─────────────────────────────────────────────
@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "GET":
        return render_template("upload.html", message=None, technicians=get_all_technicians())

    # ── Validate form inputs ──────────────────────────────────────────────
    if "mcapFile" not in request.files:
        return _upload_error("No file part found.")

    file            = request.files["mcapFile"]
    created_by_name = request.form.get("created_by_name", "").strip()

    if file.filename == "":
        return _upload_error("Please select a file first.")

    if not created_by_name:
        return _upload_error("Please select a technician before uploading.")

    if not _allowed_file(file.filename):
        return _upload_error("Invalid file type. Please upload an .mcap file only.")

    # ── Save file ────────────────────────────────────────────────────────
    save_path = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
    file.save(save_path)

    # ── Duplicate detection ───────────────────────────────────────────────
    file_hash = compute_file_hash(save_path)
    existing  = find_dataset_by_hash(file_hash)
    if existing:
        existing_draft = get_draft_by_dataset(existing["dataset_id"])
        if existing_draft:
            _safe_remove(save_path)
            return redirect(url_for("view_analysis",
                                    draft_id=existing_draft["draft_id"],
                                    duplicate_notice="true",
                                    original_filename=existing["original_filename"]))

    # ── Create dataset record ─────────────────────────────────────────────
    dataset_id = create_dataset(file.filename, save_path, created_by_name, file_hash)

    # ── UCD002 — Validate ─────────────────────────────────────────────────
    try:
        is_valid, val_msg, missing = validate_mcap(save_path)
    except Exception as e:
        _safe_remove(save_path)
        return _upload_error("An unexpected error occurred while reading the file. Please verify it is a valid MCAP recording.")

    save_validation_result(dataset_id, is_valid, missing, val_msg)

    if not is_valid:
        update_dataset_status(dataset_id, "Invalid",
                              validation_status="Invalid",
                              error_message=val_msg)
        _safe_remove(save_path)
        return _upload_error(f"Validation failed: {val_msg}")

    update_dataset_status(dataset_id, "Extracting", validation_status="Valid")

    # ── UCD003 — Extract ──────────────────────────────────────────────────
    try:
        extracted = extract_mcap_data(save_path)
    except Exception as e:
        err_str = str(e).lower()
        if "descriptor" in err_str or "protobuf" in err_str:
            msg = "Unable to decode this file as a valid MCAP recording. The file may be corrupted or in an unsupported format."
        elif "topic" in err_str:
            msg = "This MCAP file is missing required data topics (HMI State, AD State). It may be an incomplete recording."
        else:
            msg = "An unexpected error occurred while processing this file. Please verify it is a valid MCAP recording."
        update_dataset_status(dataset_id, "Failed", extraction_status="Failed", error_message=msg)
        return _upload_error(msg)

    save_extraction_result(dataset_id, extracted)

    if "Error" in extracted.get("status", ""):
        update_dataset_status(dataset_id, "Failed",
                              extraction_status="Failed",
                              error_message=extracted.get("status"))
        return _upload_error(f"Extraction failed: {extracted.get('status')}")

    update_dataset_status(dataset_id, "Ready", extraction_status="Completed")

    draft_id = create_report_draft(dataset_id, extracted)
    return redirect(url_for("view_analysis", draft_id=draft_id))


# ─────────────────────────────────────────────
# UCD004 — View event analysis
# ─────────────────────────────────────────────
@app.route("/analysis/<int:draft_id>")
def view_analysis(draft_id):
    draft = get_report_draft(draft_id)
    if not draft:
        abort(404)

    dataset      = get_dataset(draft["dataset_id"])
    extraction   = get_extraction_result(draft["dataset_id"])
    report       = _get_report_by_draft(draft_id)
    manual       = {i["field_name"]: i for i in draft["manual_inputs"]}
    saved         = request.args.get("saved", False)
    summary_saved = json.loads(draft.get("summary_data", "{}")) if draft.get("summary_data") else {}
    extra_lines_saved  = summary_saved.get("extra_lines", [])
    screenshots_saved  = summary_saved.get("screenshots", [])

    # One-shot flash from the re-extract route — read then clear so it
    # only surfaces on the page load immediately after re-extraction.
    reextract_changes   = session.pop("reextract_changes", None)
    reextract_no_change = session.pop("reextract_no_change", False)

    return render_template(
        "analysis.html",
        draft=draft,
        dataset=dataset,
        extraction=extraction["summary"] if extraction else {},
        manual=manual,
        report=report,
        saved=saved,
        summary_saved=summary_saved,
        extra_lines_saved=extra_lines_saved,
        screenshots_saved=screenshots_saved,
        technicians=get_all_technicians(),
        event_classifications=EVENT_CLASSIFICATION,
        safety_statuses=SAFETY_FUNCTION_STATUS,
        analysis_statuses=ANALYSIS_STATUS,
        reextract_changes=reextract_changes,
        reextract_no_change=reextract_no_change,
    )


# ─────────────────────────────────────────────
# UCD005 — Complete event analysis form
# ─────────────────────────────────────────────
@app.route("/analysis/<int:draft_id>/form", methods=["POST"])
def complete_form(draft_id):
    draft = get_report_draft(draft_id)
    if not draft:
        abort(404)

    if draft["status"] == "Draft":
        return redirect(url_for("view_analysis", draft_id=draft_id) + "#complete-form")

    form_fields = [
        "event_classification", "vehicle_van", "event_date", "event_timestamp",
        "technician_remarks", "created_by_name",
    ]
    form_data = {f: request.form.get(f, "") for f in form_fields}

    save_type = request.form.get("save_type", "draft")
    save_manual_inputs(draft_id, form_data)
    log_action(draft["dataset_id"], "FORM_SAVED", f"Draft ID: {draft_id}")

    if save_type == "save" and draft["status"] == "In Progress" and is_draft_complete(draft_id):
        update_draft_status(draft_id, "Completed")

    return redirect(url_for("view_analysis", draft_id=draft_id, saved="true") + "#complete-form")


# ─────────────────────────────────────────────
# Screenshot upload (AJAX)
# ─────────────────────────────────────────────
@csrf.exempt
@app.route("/analysis/<int:draft_id>/screenshot", methods=["POST"])
def upload_screenshot(draft_id):
    data  = request.json.get("data", "")
    title = request.json.get("title", "")
    if not data:
        return jsonify({"error": "No image data"}), 400
    try:
        _, img_b64  = data.split(",", 1)
        img_bytes   = base64.b64decode(img_b64)
        filename    = f"{uuid.uuid4().hex}.png"
        filepath    = os.path.join(SCREENSHOTS_FOLDER, filename)
        with open(filepath, "wb") as fh:
            fh.write(img_bytes)
        return jsonify({"filename": filename, "title": title})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/screenshots/<filename>")
def serve_screenshot(filename):
    return send_from_directory(SCREENSHOTS_FOLDER, filename)


# ─────────────────────────────────────────────
# Save analysis summary
# ─────────────────────────────────────────────
@app.route("/analysis/<int:draft_id>/summary", methods=["POST"])
def save_summary(draft_id):
    draft = get_report_draft(draft_id)
    if not draft:
        abort(404)

    summary_fields = [
        "traffic_condition", "summary_odd_pre_event",
        "summary_cause_takeover", "summary_driver_input",
        "summary_max_braking", "summary_off_state_braking", "haptic_signal", "summary_scg",
        "summary_driver_takeover", "summary_no_abnormalities",
        "summary_conclusion", "braking_assessment",
    ]
    summary_data = {f: request.form.get(f, "") for f in summary_fields}

    extra_lines = []
    for key in request.form:
        if key.startswith("extra_line_"):
            val = request.form.get(key, "").strip()
            if val:
                extra_lines.append(val)
    summary_data["extra_lines"] = extra_lines

    screenshots = []
    i = 0
    while f"screenshot_filename_{i}" in request.form:
        filename = request.form.get(f"screenshot_filename_{i}", "")
        title    = request.form.get(f"screenshot_title_display_{i}", "")
        if filename:
            screenshots.append({"title": title, "filename": filename})
        i += 1
    summary_data["screenshots"] = screenshots

    conn = get_db()
    conn.execute(
        "UPDATE report_drafts SET summary_data=?, updated_at=datetime('now') WHERE draft_id=?",
        (json.dumps(summary_data), draft_id)
    )
    conn.commit()
    conn.close()

    if draft["status"] == "Draft":
        update_draft_status(draft_id, "In Progress")

    return redirect(url_for("view_analysis", draft_id=draft_id, saved="true") + "#analysis-summary")


# ─────────────────────────────────────────────
# UCD006 — Update event analysis status
# ─────────────────────────────────────────────
@app.route("/analysis/<int:draft_id>/status", methods=["POST"])
def update_status(draft_id):
    draft = get_report_draft(draft_id)
    if not draft:
        abort(404)

    new_status = request.form.get("status", "")
    if new_status in ANALYSIS_STATUS:
        update_draft_status(draft_id, new_status)

    return redirect(url_for("view_analysis", draft_id=draft_id))


# ─────────────────────────────────────────────
# UCD007 — Generate event report (PDF)
# ─────────────────────────────────────────────
@app.route("/analysis/<int:draft_id>/generate", methods=["POST"])
def generate_report(draft_id):
    draft = get_report_draft(draft_id)
    if not draft:
        abort(404)

    if draft["status"] != "Completed" or not is_draft_complete(draft_id):
        return redirect(url_for("view_analysis", draft_id=draft_id))

    dataset  = get_dataset(draft["dataset_id"])
    ext_row  = get_extraction_result(draft["dataset_id"])
    ext_data = ext_row["summary"] if ext_row else {}
    summary  = json.loads(draft.get("summary_data", "{}")) if draft.get("summary_data") else {}

    pdf_filename = f"report_draft{draft_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    pdf_path     = os.path.join(app.config["REPORTS_FOLDER"], pdf_filename)

    _build_pdf(pdf_path, draft, dataset, ext_data, summary)

    myt = timezone(timedelta(hours=8))
    generated_at_myt = datetime.now(myt).strftime("%Y-%m-%d %H:%M:%S MYT")
    save_report(draft_id, draft["dataset_id"], pdf_filename, pdf_path, generated_at_myt)
    return redirect(url_for("view_reports"))


def _build_pdf(pdf_path, draft, dataset, ext_data, summary):
    """Assemble and write the analysis report PDF to pdf_path."""
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ReportTitle", parent=styles["Heading1"],
        fontSize=16, spaceAfter=4, textColor=colors.HexColor("#1a1a2e"),
    )
    subtitle_style = ParagraphStyle(
        "ReportSubtitle", parent=styles["Normal"],
        fontSize=11, spaceAfter=4, textColor=colors.HexColor("#444444"),
    )
    footer_style = ParagraphStyle(
        "ReportFooter", parent=styles["Normal"],
        fontSize=8, textColor=colors.grey,
    )
    b1_style = ParagraphStyle(
        "Bullet1", parent=styles["Normal"],
        fontSize=10, leftIndent=0, spaceAfter=3,
    )
    b2_style = ParagraphStyle(
        "Bullet2", parent=styles["Normal"],
        fontSize=10, leftIndent=20, spaceAfter=3,
    )
    b3_style = ParagraphStyle(
        "Bullet3", parent=styles["Normal"],
        fontSize=10, leftIndent=40, spaceAfter=3,
    )
    cell_style = ParagraphStyle(
        "CellStyle", fontSize=8, alignment=1, leading=12,
    )
    label_style = ParagraphStyle(
        "LabelStyle", fontSize=8, alignment=0, leading=12,
        fontName="Helvetica-Bold",
    )
    header_style = ParagraphStyle(
        "HeaderStyle", fontSize=8, alignment=1, leading=12,
        fontName="Helvetica-Bold", textColor=colors.white,
    )
    normal_style = ParagraphStyle(
        "NormalText", parent=styles["Normal"],
        fontSize=10, spaceAfter=3,
    )

    # 1cm margins → 19cm usable width, matching [3+4+4+4+4] column spec
    doc = SimpleDocTemplate(pdf_path, pagesize=A4,
                            leftMargin=1*cm, rightMargin=1*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    story = []

    # ── SECTION 1: Header + Metadata ─────────────────────────────────────
    story.append(Paragraph("ADAS Level 3 Event Analysis Report", title_style))
    story.append(Paragraph("EDAG Holding Sdn. Bhd. — Fleet Monitoring", subtitle_style))
    story.append(Spacer(1, 0.3*cm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1a1a2e")))
    story.append(Spacer(1, 0.4*cm))

    manual_dict     = {i["field_name"]: i.get("field_value", "N/A") for i in draft["manual_inputs"]}
    created_by      = manual_dict.get("created_by_name")  or "-"
    event_date      = manual_dict.get("event_date")        or "-"
    event_timestamp = manual_dict.get("event_timestamp")   or "-"
    vehicle_van     = manual_dict.get("vehicle_van")       or "-"

    meta_table = Table(
        [
            ["Report Generated", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
            ["MCAP File",        (dataset["original_filename"] if dataset else None) or "-"],
            ["Created By",       created_by],
            ["Event Date",       event_date],
            ["Event Timestamp",  event_timestamp],
            ["Vehicle/Van",      vehicle_van],
            ["Analysis Status",  draft["status"]],
        ],
        colWidths=[5*cm, 14*cm],
    )
    meta_table.setStyle(TableStyle([
        ("FONTSIZE",       (0, 0), (-1, -1), 9),
        ("FONTNAME",       (0, 0), (0, -1),  "Helvetica-Bold"),
        ("TEXTCOLOR",      (0, 0), (0, -1),  colors.HexColor("#16213e")),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#f8f9fa"), colors.white]),
        ("GRID",           (0, 0), (-1, -1), 0.5, colors.HexColor("#dee2e6")),
        ("PADDING",        (0, 0), (-1, -1), 6),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 0.6*cm))

    # ── SECTION 2: Foxglove Analysis ─────────────────────────────────────
    def _b(text, level=1):
        prefix = {1: "• ", 2: "○ ", 3: "■ "}[level]
        style  = {1: b1_style, 2: b2_style, 3: b3_style}[level]
        story.append(Paragraph(prefix + text, style))

    _b("Foxglove Analysis incl. Screenshots")
    _b("Summary:", 2)
    _b(f"Based on the MCAP, observed there was a {summary.get('traffic_condition', 'N/A')}.", 3)
    for key in ["summary_odd_pre_event", "summary_cause_takeover", "summary_driver_input"]:
        val = summary.get(key, "")
        if val:
            _b(val, 3)
    brake = summary.get("brake_pedal_input", "")
    if brake:
        _b(brake, 3)
    val = summary.get("summary_max_braking", "")
    if val:
        _b(val, 3)
    val = summary.get("summary_off_state_braking", "")
    if val:
        _b(val, 3)
    haptic = summary.get("haptic_signal", "")
    if haptic:
        _b(haptic, 3)
    for key in ["summary_scg", "summary_driver_takeover"]:
        val = summary.get(key, "")
        if val:
            _b(val, 3)
    no_abn = summary.get("summary_no_abnormalities", "")
    if no_abn:
        _b(no_abn, 3)
    for extra in summary.get("extra_lines", []):
        if extra:
            _b(extra, 3)
    conclusion = f"{summary.get('summary_conclusion', '')} {summary.get('braking_assessment', '')}.".strip()
    if conclusion != ".":
        _b(conclusion, 3)

    odd_code        = ext_data.get("event_odd_code", "N/A")
    odd_name        = ext_data.get("odd_event_name", "N/A")
    not_in_sm       = ext_data.get("odd_code_not_in_state_machine", False)
    _b(f"ODD Event: {odd_code} - {odd_name}", 2)
    if not_in_sm:
        _b(
            f"Event triggered: {odd_code} - {odd_name} "
            f"(StateAutonomousDriving__adSmTimeAndReasonToMrm"
            f" / Not Available In Any State Machine)",
            3,
        )
    else:
        _b(f"Event triggered: {odd_code}/StateAutonomousDriving__adSmTimeAndReasonToMrm", 3)

    story.append(Spacer(1, 0.6*cm))

    # ── SECTION 3: Event Phase Table (transposed — fields as rows, phases as cols) ──
    # PDF report shows core rows only — extended (oscillating/ramp-up) rows
    # are for the interactive Event Timeline view, not the printed report.
    event_phases = [
        row for row in ext_data.get("event_table", [])
        if not row.get("is_extended", False)
    ]

    if event_phases:
        et_hdr_style  = ParagraphStyle(
            "ETHdr", fontSize=7, alignment=1, leading=9,
            fontName="Helvetica-Bold", textColor=colors.white,
        )
        et_lbl_style  = ParagraphStyle(
            "ETLbl", fontSize=7, alignment=0, leading=9,
            fontName="Helvetica-Bold", textColor=colors.HexColor("#1b3a6b"),
        )
        et_cell_style = ParagraphStyle("ETCell", fontSize=7, alignment=1, leading=9)

        def _ph(text, style):
            return Paragraph(str(text) if text is not None else "N/A", style)

        def _odd_text(row):
            code = row.get("odd_code")
            name = row.get("odd_triggered", "N/A")
            return f"{code} - {name}" if code is not None else (name or "N/A")

        def _time_text(row):
            dur = row.get("duration_seconds")
            return f"{dur}s" if dur is not None else "N/A"

        header_row = [_ph("", et_hdr_style)] + [
            _ph(row.get("phase", "Pre-Event"), et_hdr_style)
            for row in event_phases
        ]
        odd_row = [_ph("ODD Event", et_lbl_style)] + [
            _ph(_odd_text(row), et_cell_style) for row in event_phases
        ]
        ad_row = [_ph("AD State", et_lbl_style)] + [
            _ph(row.get("ad_state", "N/A"), et_cell_style) for row in event_phases
        ]
        hmi_row = [_ph("HMI State", et_lbl_style)] + [
            _ph(f"{row.get('hmi_value', 'N/A')} / {row.get('hmi_state', 'N/A')}", et_cell_style)
            for row in event_phases
        ]
        time_row = [_ph("Time(s)", et_lbl_style)] + [
            _ph(_time_text(row), et_cell_style) for row in event_phases
        ]

        table_data = [header_row, odd_row, ad_row, hmi_row, time_row]

        num_cols = 1 + len(event_phases)
        col_w = 19 * cm / num_cols
        phase_table = Table(
            table_data,
            colWidths=[col_w] * num_cols,
            repeatRows=1,
        )
        phase_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0),  colors.HexColor("#1b3a6b")),
            ("BACKGROUND", (0, 1), (0, -1),  colors.HexColor("#f0f4f9")),
            ("GRID",       (0, 0), (-1, -1), 0.5, colors.HexColor("#c0c8d8")),
            ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN",      (1, 0), (-1, -1), "CENTER"),
            ("ALIGN",      (0, 0), (0, -1),  "LEFT"),
            ("LEFTPADDING",  (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ]))
        story.append(phase_table)
        story.append(Spacer(1, 0.6*cm))

    # ── Screenshots ───────────────────────────────────────────────────────
    for ss in summary.get("screenshots", []):
        if ss.get("title"):
            story.append(Paragraph(ss["title"], normal_style))
            story.append(Spacer(1, 0.2*cm))
        if ss.get("filename"):
            filepath = os.path.join(SCREENSHOTS_FOLDER, ss["filename"])
            if os.path.exists(filepath):
                try:
                    ir             = ImageReader(filepath)
                    orig_w, orig_h = ir.getSize()
                    max_w, max_h   = 15 * cm, 10 * cm
                    ratio          = orig_h / orig_w
                    img_w          = min(max_w, orig_w)
                    img_h          = img_w * ratio
                    if img_h > max_h:
                        img_h = max_h
                        img_w = img_h / ratio
                    img = RLImage(filepath, width=img_w, height=img_h)
                    story.append(img)
                    story.append(Spacer(1, 0.3*cm))
                except Exception:
                    pass

    # ── Footer ────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        "This report was generated automatically by the "
        "Automated MCAP Analysis and Report Visualization System.",
        footer_style,
    ))

    doc.build(story)


# ─────────────────────────────────────────────
# Technician management
# ─────────────────────────────────────────────
@csrf.exempt
@app.route("/technicians/add", methods=["POST"])
def add_technician_route():
    data = request.json or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "Name cannot be empty."}), 400
    if add_technician(name):
        return jsonify({"success": True, "name": name})
    return jsonify({"success": False, "error": f'"{name}" already exists.'}), 409


@csrf.exempt
@app.route("/technicians/delete", methods=["POST"])
def delete_technician_route():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "Name cannot be empty."}), 400
    try:
        delete_technician(name)
        return jsonify({"success": True})
    except Exception:
        return jsonify({"success": False, "error": "Failed to delete technician."}), 500


# ─────────────────────────────────────────────
# Delete analysis draft
# ─────────────────────────────────────────────
@app.route("/analysis/<int:draft_id>/delete", methods=["POST"])
def delete_analysis(draft_id):
    delete_draft(draft_id)
    return redirect(url_for("home"))


# ─────────────────────────────────────────────
# Re-extract from original MCAP file
# ─────────────────────────────────────────────
@csrf.exempt
@app.route("/analysis/<int:draft_id>/reextract", methods=["POST"])
def reextract(draft_id):
    draft = get_report_draft(draft_id)
    if not draft:
        abort(404)
    dataset = get_dataset(draft["dataset_id"])
    if not dataset or not os.path.exists(dataset["stored_path"]):
        return jsonify({"status": "error", "message": "Original MCAP file not found."}), 404

    old_result = get_extraction_result(draft["dataset_id"])
    old = old_result["summary"] if old_result else {}

    extracted = extract_mcap_data(dataset["stored_path"])
    update_extraction_result(draft["dataset_id"], extracted)
    update_draft_auto_data(draft_id, extracted)

    # (session key, raw field compared for equality, formatted field shown to the user, label)
    compare_fields = [
        ("max_braking",         "max_braking_raw",        "max_braking",         "Max Braking"),
        ("velocity_v1",         "velocity_at_trigger_kmh", "velocity_at_trigger", "Velocity (v1)"),
        ("velocity_v2",         "min_velocity_after_kmh",  "min_velocity_after",  "Velocity (v2)"),
        ("scg_reason",          "scg_reason_text",         "scg_reason_text",     "SCG Reason"),
        ("event_triggered_odd", "event_odd_code",          "odd_trigger",         "ODD Triggered"),
    ]
    changes = {}
    for key, cmp_field, disp_field, label in compare_fields:
        old_val = old.get(cmp_field)
        new_val = extracted.get(cmp_field)
        if old_val != new_val:
            changes[key] = {
                "label": label,
                "old":   old.get(disp_field, "N/A"),
                "new":   extracted.get(disp_field, "N/A"),
            }

    session["reextract_changes"]   = changes
    session["reextract_no_change"] = len(changes) == 0

    return jsonify({"status": "success"})


# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# UCD008 — View reports list
# ─────────────────────────────────────────────
@app.route("/reports")
def view_reports():
    reports = get_all_drafts_with_details()
    for r in reports:
        # Raw DB value starts with a clean ISO date (e.g. "2026-08-06 09:54:46 MYT")
        # — capture that BEFORE to_myt() reformats it to "06 Aug 2026, 09:54 MYT"
        # for display, so the date filter has a real ISO date to compare against.
        r["generated_at_date"] = r["generated_at"][:10] if r["generated_at"] else ""
        r["generated_at"]      = to_myt(r["generated_at"])
    return render_template("reports.html", reports=reports)


# ─────────────────────────────────────────────
# Audit Log viewer
# ─────────────────────────────────────────────
@app.route("/audit-logs")
def audit_logs_page():
    logs = get_audit_logs(limit=200)
    for log in logs:
        log["timestamp"] = to_myt(log["timestamp"])
    return render_template("audit_logs.html", logs=logs)


# ─────────────────────────────────────────────
# UCD009 — Download report
# ─────────────────────────────────────────────
@app.route("/reports/<int:report_id>/download")
def download_report(report_id):
    report = get_report(report_id)
    if not report or not os.path.exists(report["pdf_path"]):
        abort(404)

    log_action(report["dataset_id"], "REPORT_DOWNLOADED", f"Report ID: {report_id}")
    return send_file(
        report["pdf_path"],
        as_attachment=True,
        download_name=report["pdf_filename"],
    )


if __name__ == '__main__':
    debug_mode = os.environ.get(
        'FLASK_DEBUG', 'False'
    ).lower() == 'true'
    app.run(debug=debug_mode)
