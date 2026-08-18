"""
demo_seed.py
Populates the database with 8 realistic demo analysis records for a live
presentation. Safe to run multiple times — existing demo records (matched
by original_filename LIKE 'event_2026%') are fully removed via
database.delete_draft() before the new set is inserted.

This script is a demo utility only — it is intentionally excluded from
version control (see .gitignore) and is never imported by app.py.

Run:
    python demo_seed.py
"""

import os
from datetime import datetime, timedelta

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

from database import (
    init_db,
    get_db,
    create_dataset,
    update_dataset_status,
    save_validation_result,
    save_extraction_result,
    create_report_draft,
    save_manual_inputs,
    save_summary_data,
    update_draft_status,
    save_report,
    log_action,
    add_technician,
    get_draft_by_dataset,
    delete_draft,
)
from extraction import (
    ODD_NAMES,
    HMI_STATE_NAMES,
    AD_NAMES,
    MRM_ODD_CODES,
    classify_event_type,
    generate_analysis_summary,
    _classify_braking,
)

UPLOAD_FOLDER  = "uploads"
REPORTS_FOLDER = "reports"
DEMO_MARKER    = "event_2026"  # prefix shared by every demo filename below

TECHNICIANS = ["Ammar Bakhtiar", "Fudail", "Adib"]

# ─────────────────────────────────────────────
# Demo record data (from the presentation script)
# ─────────────────────────────────────────────
RECORDS = [
    {
        "filename": "event_20260412_putrajaya_355.mcap",
        "van": "Van A (MY-001)", "date": "2026-04-12", "time": "14:23:18",
        "technician": "Ammar Bakhtiar", "status": "completed",
        "traffic": "heavy traffic with multiple vehicles ahead on Persiaran Perdana, Putrajaya",
        "max_braking": -4.8440,
        "velocity": (59.14, 58.98),
        "timeline": [
            "Pre Event | HMI=3 | Active(48) | ODD350-DriverEyesNotVisibleWarning | 10.02s",
            "Pre-Event | HMI=9 | Active(48) | ODD351-DriverEyesNotVisibleCritical | 5.16s",
            "Event Triggered | HMI=11 | Active(48) | ODD355-DriverTriesTakeoverByStrongSteering | 1.03s",
            "Post-Event | HMI=1 | Init(2) | ODD361-DriverKickDownAcceleratorPedal | 1.12s",
        ],
        "summary": {
            "summary_odd_pre_event": "ODD355 (DriverTriesTakeoverByStrongSteering) triggered ACTIVE_TAKE_OVER_REQUEST_YELLOW.",
            "summary_cause_takeover": "This caused the driver to take over control through strong steering input and the HMI state changed to ACTIVE_HAF_TAF_TOR.",
            "summary_driver_input": "There is also steering input by the driver showing that the driver take over the steering.",
            "summary_max_braking": "During on state the maximum braking is -4.8440 m/s² during AD state Active and vehicle speed decrease from 59.14 km/h to 58.98 km/h.",
            "summary_scg": "SCG Reason shows No Reason.",
            "summary_conclusion": "Considered as expected behavior as the driver want to take over the steering due to TOR Yellow.",
        },
    },
    {
        "filename": "event_20260415_elite_354.mcap",
        "van": "Van B (MY-002)", "date": "2026-04-15", "time": "09:47:33",
        "technician": "Fudail", "status": "completed",
        "traffic": "normal traffic on ELITE Highway near Putrajaya interchange",
        "max_braking": -7.2830,
        "velocity": (38.00, 30.00),
        "timeline": [
            "Pre Event | HMI=3 | Active(48) | ODD319-TrajectoryVelocityLowThreshold | 14.63s",
            "Pre-Event | HMI=9 | Active(48) | ODD48-NonL3DrivableMapData | 2.47s",
            "Event Triggered | HMI=11 | Active(48) | ODD354-DriverTriesTakeoverByStrongBraking | 1.04s",
            "Post-Event | HMI=1 | Init(2) | ODD290-PreventReadyAfterDeactivation | 7.77s",
        ],
        "summary": {
            "summary_odd_pre_event": "ODD48 (NonL3DrivableMapData) triggered ACTIVE_TAKE_OVER_REQUEST_YELLOW.",
            "summary_cause_takeover": "This caused the driver to take over control through strong braking input and the HMI state changed to ACTIVE_HAF_TAF_TOR.",
            "summary_driver_input": "The maximum braking recorded during the event indicates significant brake pedal input by the driver.",
            "summary_max_braking": "During on state the maximum braking is -7.2830 m/s² during AD state Active and vehicle speed decrease from 38.00 km/h to 30.00 km/h.",
            "summary_scg": "SCG Reason shows No Reason.",
            "summary_conclusion": "Considered as expected behavior as the driver want to take over by strong braking due to TOR Yellow.",
        },
    },
    {
        "filename": "event_20260418_cyberjaya_355.mcap",
        "van": "Van C (MY-003)", "date": "2026-04-18", "time": "16:55:42",
        "technician": "Adib", "status": "completed",
        "traffic": "light traffic on Persiaran APEC, Cyberjaya during evening hours",
        "max_braking": -4.8740,
        "velocity": (42.00, 36.00),
        "timeline": [
            "Pre Event | HMI=3 | Active(48) | ODD104-SpeedAdaptionToNeighboringTraffic | 18.14s",
            "Pre-Event | HMI=9 | Active(48) | ODD355-DriverTriesTakeoverByStrongSteering | 2.59s",
            "Event Triggered | HMI=11 | Active(48) | ODD355-DriverTriesTakeoverByStrongSteering | 1.05s",
            "Post-Event | HMI=1 | Init(2) | ODD361-DriverKickDownAcceleratorPedal | 2.76s",
        ],
        "summary": {
            "summary_odd_pre_event": "ODD355 (DriverTriesTakeoverByStrongSteering) triggered ACTIVE_TAKE_OVER_REQUEST_YELLOW.",
            "summary_cause_takeover": "This caused the driver to take over control through strong steering input and the HMI state changed to ACTIVE_HAF_TAF_TOR.",
            "summary_driver_input": "There is also steering input by the driver showing that the driver take over the steering.",
            "summary_max_braking": "During on state the maximum braking is -4.8740 m/s² during AD state Active and vehicle speed decrease from 42.00 km/h to 36.00 km/h.",
            "summary_scg": "SCG Reason shows No Reason.",
            "summary_conclusion": "Considered as expected behavior as the driver want to take over the steering due to TOR Yellow.",
        },
    },
    {
        "filename": "event_20260421_putrajaya_standstill.mcap",
        "van": "Van A (MY-001)", "date": "2026-04-21", "time": "08:12:05",
        "technician": "Ammar Bakhtiar", "status": "completed",
        "traffic": "heavy traffic jam near Putrajaya roundabout, vehicle near standstill",
        "max_braking": -0.2660,
        "velocity": "standstill",
        "timeline": [
            "Pre Event | HMI=3 | Active(48) | ODD38-LeadingVehicleLostEndOfTrafficJam | 7.80s",
            "Pre-Event | HMI=10 | Active(48) | ODD354-DriverTriesTakeoverByStrongBraking | 2.50s",
            "Event Triggered | HMI=11 | Active(48) | ODD354-DriverTriesTakeoverByStrongBraking | 1.01s",
            "Post-Event | HMI=1 | Init(2) | ODD290-PreventReadyAfterDeactivation | 4.23s",
        ],
        "summary": {
            "summary_odd_pre_event": "ODD354 (DriverTriesTakeoverByStrongBraking) triggered ACTIVE_TAKE_OVER_REQUEST_RED.",
            "summary_cause_takeover": "This caused the driver to take over control through strong braking input and the HMI state changed to ACTIVE_HAF_TAF_TOR.",
            "summary_driver_input": "The vehicle was at near standstill condition when the takeover event was triggered.",
            "summary_max_braking": "During on state the maximum braking is -0.2660 m/s² during AD state Active. Vehicle Standstill detected.",
            "summary_scg": "SCG Reason shows No Reason.",
            "summary_conclusion": "Considered as expected behavior as the driver want to take over by strong braking due to TOR Red.",
        },
    },
    {
        "filename": "event_20260502_lekas_355.mcap",
        "van": "Van B (MY-002)", "date": "2026-05-02", "time": "11:30:27",
        "technician": "Fudail", "status": "in_progress",
        "traffic": "moderate traffic on LEKAS Highway near Kajang interchange",
        "max_braking": -2.3080,
        "velocity": (34.26, 28.51),
        "timeline": [
            "Pre Event | HMI=3 | Active(48) | ODD85-NarrowLane | 12.45s",
            "Pre-Event | HMI=9 | Active(48) | ODD355-DriverTriesTakeoverByStrongSteering | 1.72s",
            "Event Triggered | HMI=11 | Active(48) | ODD355-DriverTriesTakeoverByStrongSteering | 0.63s",
            "Post-Event | HMI=1 | Init(2) | ODD361-DriverKickDownAcceleratorPedal | 0.45s",
        ],
        "summary": {
            "summary_odd_pre_event": "ODD355 (DriverTriesTakeoverByStrongSteering) triggered ACTIVE_TAKE_OVER_REQUEST_YELLOW.",
            "summary_cause_takeover": "This caused the driver to take over control through strong steering input and the HMI state changed to ACTIVE_HAF_TAF_TOR.",
            "summary_driver_input": "There is also steering input by the driver showing that the driver take over the steering.",
            "summary_max_braking": "During on state the maximum braking is -2.3080 m/s² during AD state Active and vehicle speed decrease from 34.26 km/h to 28.51 km/h.",
            "summary_scg": "SCG Reason shows No Reason.",
            "summary_conclusion": "Considered as expected behavior as the driver want to take over the steering due to TOR Yellow.",
        },
    },
    {
        "filename": "event_20260505_maju_354.mcap",
        "van": "Van C (MY-003)", "date": "2026-05-05", "time": "17:44:11",
        "technician": "Adib", "status": "in_progress",
        "traffic": "heavy traffic on Maju Expressway during evening peak hours",
        "max_braking": -3.3900,
        "velocity": (28.89, 22.34),
        "timeline": [
            "Pre Event | HMI=3 | Active(48) | ODD178-InvalidPoseEstimate | 9.03s",
            "Pre-Event | HMI=9 | Active(48) | ODD354-DriverTriesTakeoverByStrongBraking | 0.023s",
            "Event Triggered | HMI=11 | Active(48) | ODD354-DriverTriesTakeoverByStrongBraking | 1.44s",
            "Post-Event | HMI=1 | Init(2) | ODD321-DecelerationExceedsThreshold | 0.41s",
        ],
        "summary": {
            "summary_odd_pre_event": "ODD354 (DriverTriesTakeoverByStrongBraking) triggered ACTIVE_TAKE_OVER_REQUEST_YELLOW.",
            "summary_cause_takeover": "This caused the driver to take over control through strong braking input and the HMI state changed to ACTIVE_HAF_TAF_TOR.",
            "summary_driver_input": "Strong brake pedal input was detected throughout the takeover sequence.",
            "summary_max_braking": "During on state the maximum braking is -3.3900 m/s² during AD state Active and vehicle speed decrease from 28.89 km/h to 22.34 km/h.",
            "summary_scg": "SCG Reason shows No Reason.",
            "summary_conclusion": "Considered as expected behavior as the driver want to take over by strong braking due to TOR Yellow.",
        },
    },
    {
        "filename": "event_20260510_npe_355.mcap",
        "van": "Van A (MY-001)", "date": "2026-05-10", "time": "13:22:49",
        "technician": "Ammar Bakhtiar", "status": "draft",
        "traffic": "(pending review)",
        "max_braking": -1.6550,
        "velocity": (52.34, 44.67),
        "timeline": [
            "Pre Event | HMI=3 | Active(48) | ODD319-TrajectoryVelocityLowThreshold | 8.91s",
            "Pre-Event | HMI=9 | Active(48) | ODD355-DriverTriesTakeoverByStrongSteering | 1.55s",
            "Event Triggered | HMI=11 | Active(48) | ODD355-DriverTriesTakeoverByStrongSteering | 0.30s",
            "Post-Event | HMI=1 | Init(2) | ODD361-DriverKickDownAcceleratorPedal | 1.16s",
        ],
        "summary": None,
    },
    {
        "filename": "event_20260512_duke_354.mcap",
        "van": "Van B (MY-002)", "date": "2026-05-12", "time": "07:58:33",
        "technician": "Fudail", "status": "draft",
        "traffic": "(pending review)",
        "max_braking": -0.1920,
        "velocity": (18.45, 12.33),
        "timeline": [
            "Pre Event | HMI=3 | Active(48) | ODD285-LongitudinalAccelerationExtremelyHigh | 6.72s",
            "Pre-Event | HMI=4 | Active(48) | ODD363-DriverPressesBrakePedal | 1.02s",
            "Event Triggered | HMI=11 | Active(48) | ODD354-DriverTriesTakeoverByStrongBraking | 0.26s",
            "Post-Event | HMI=1 | Init(2) | ODD290-PreventReadyAfterDeactivation | 3.88s",
        ],
        "summary": None,
    },
]


# ─────────────────────────────────────────────
# Parsing helpers
# ─────────────────────────────────────────────
def _parse_row(line):
    """'Pre-Event | HMI=9 | Active(48) | ODD355-DriverTriesTakeoverByStrongSteering | 2.59s' -> dict"""
    phase, hmi_part, ad_part, odd_part, dur_part = [p.strip() for p in line.split("|")]
    hmi_value = int(hmi_part.split("=")[1])
    ad_code = int(ad_part[ad_part.index("(") + 1: ad_part.index(")")])
    odd_code_str, odd_name = odd_part.split("-", 1)
    odd_code = int(odd_code_str.replace("ODD", ""))
    duration = float(dur_part.rstrip("s"))
    return {
        "phase": phase, "hmi_value": hmi_value, "ad_code": ad_code,
        "odd_code": odd_code, "odd_name": odd_name, "duration": duration,
    }


def _ts_str(dt):
    """Match extraction.py's own _ts_str() formatting exactly."""
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " MYT"


def _build_event_table(date_str, time_str, timeline_lines):
    rows = [_parse_row(line) for line in timeline_lines]
    et_idx = next(i for i, r in enumerate(rows) if r["phase"] == "Event Triggered")
    base_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")

    starts = [None] * len(rows)
    starts[et_idx] = base_dt
    for i in range(et_idx - 1, -1, -1):
        starts[i] = starts[i + 1] - timedelta(seconds=rows[i]["duration"])
    for i in range(et_idx + 1, len(rows)):
        starts[i] = starts[i - 1] + timedelta(seconds=rows[i - 1]["duration"])

    event_table = []
    for r, start in zip(rows, starts):
        ad_name = AD_NAMES.get(r["ad_code"], f"State {r['ad_code']}")
        event_table.append({
            "phase":            r["phase"],
            "timestamp":        _ts_str(start),
            "hmi_state":        HMI_STATE_NAMES.get(r["hmi_value"], f"Unknown({r['hmi_value']})"),
            "hmi_value":        r["hmi_value"],
            "ad_state":         f"{ad_name} ({r['ad_code']})",
            "odd_triggered":    r["odd_name"],
            "odd_code":         r["odd_code"],
            "duration_seconds": r["duration"],
            "is_extended":      False,
        })
    return event_table


def _build_timeseries(velocity, max_braking_raw, n=15):
    """Synthetic-but-smooth deceleration curve for the Extraction Result charts."""
    if velocity == "standstill":
        vel_ts = [{"t": round(i * 0.2, 2), "v": 0.0} for i in range(n)]
        mid = n // 2
        accel_ts = [
            {"t": round(i * 0.2, 2), "v": round(max_braking_raw if i == mid else max_braking_raw * 0.05, 4)}
            for i in range(n)
        ]
        return accel_ts, vel_ts

    v1, v2 = velocity
    vel_ts, accel_ts = [], []
    for i in range(n):
        t = round(i * 0.2, 2)
        frac = i / (n - 1)
        vel_ts.append({"t": t, "v": round(v1 + (v2 - v1) * frac, 2)})
        peak_dist = abs(frac - 0.5) * 2  # 0 at midpoint, 1 at edges
        accel_ts.append({"t": t, "v": round(max_braking_raw * max(0.05, 1 - peak_dist), 4)})
    return accel_ts, vel_ts


def _build_extraction_data(rec, index):
    event_table = _build_event_table(rec["date"], rec["time"], rec["timeline"])
    et_row = next(r for r in event_table if r["phase"] == "Event Triggered")
    pre_event_rows = [r for r in event_table if r["phase"] == "Pre-Event"]
    pre_event_hmi_val = pre_event_rows[-1]["hmi_value"] if pre_event_rows else None

    event_odd_code = et_row["odd_code"]
    odd_name = et_row["odd_triggered"]
    max_braking_raw = rec["max_braking"]

    if rec["velocity"] == "standstill":
        velocity_display = min_velocity_display = "Vehicle Standstill"
        v1_kmh = v2_kmh = None
        velocity_at_event = "0.0 km/h"
    else:
        v1_kmh, v2_kmh = rec["velocity"]
        velocity_display     = f"{v1_kmh} km/h"
        min_velocity_display = f"{v2_kmh} km/h"
        velocity_at_event    = f"{v2_kmh} km/h"

    if max_braking_raw is None or max_braking_raw > -4.0:
        suggested_classification = "Below -4"
    elif max_braking_raw > -7.0:
        suggested_classification = "-4 to -7"
    else:
        suggested_classification = "-7 to -10"

    accel_ts, vel_ts = _build_timeseries(rec["velocity"], max_braking_raw)

    extraction_data = {
        "max_braking":              f"{max_braking_raw:.4f} m/s²",
        "max_braking_raw":          max_braking_raw,
        "braking_severity":         _classify_braking(max_braking_raw),
        "velocity_at_event":        velocity_at_event,
        "velocity_at_trigger":      velocity_display,
        "velocity_at_trigger_kmh":  v1_kmh,
        "min_velocity_after":       min_velocity_display,
        "min_velocity_after_kmh":   v2_kmh,
        "odd":                      f"{odd_name} (1 occurrences)",
        "odd_trigger":              f"{event_odd_code} - {odd_name}",
        "odd_event_name":           ODD_NAMES.get(event_odd_code, odd_name),
        "event_odd_code":           event_odd_code,
        "pre_event_hmi_val":        pre_event_hmi_val,
        "event_type":               classify_event_type(event_odd_code),
        "suggested_classification": suggested_classification,
        "mrm_triggered":            "Yes" if event_odd_code in MRM_ODD_CODES else "No",
        "scg":                      "No Reason",
        "scg_reason_text":          "N/A",
        "scg_reason_value":         0,
        "total_messages":           12000 + index * 137,
        "ad_active_messages":       11700 + index * 120,
        "accel_data_points":        420 + index * 11,
        "status":                   "Extraction Completed",
        "errors":                   [],
        "accel_timeseries":         accel_ts,
        "velocity_timeseries":      vel_ts,
        "event_table":              event_table,
        "analysis_anchor_note":     None,
        "braking_window_note":      None,
        "post_event_note":          None,
        "odd_code_not_in_state_machine": False,
        "off_state_braking_raw":    None,
        "no_driver_brake_input":    None,
    }
    extraction_data["analysis_summary"] = generate_analysis_summary(extraction_data)
    return extraction_data


def _write_dummy_pdf(path, rec, extraction_data):
    """A minimal real PDF so the Reports page's Download link works live."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(path, pagesize=A4)
    story = [
        Paragraph("MCAP Event Analysis Report (Demo Data)", styles["Title"]),
        Spacer(1, 12),
        Paragraph(f"File: {rec['filename']}", styles["Normal"]),
        Paragraph(f"Technician: {rec['technician']}", styles["Normal"]),
        Paragraph(f"Vehicle/Van: {rec['van']}", styles["Normal"]),
        Paragraph(f"Event Date: {rec['date']} {rec['time']} MYT", styles["Normal"]),
        Spacer(1, 12),
        Paragraph(f"ODD Trigger: {extraction_data['odd_trigger']}", styles["Normal"]),
        Paragraph(f"Max Braking: {extraction_data['max_braking']}", styles["Normal"]),
        Paragraph(f"Braking Severity: {extraction_data['braking_severity']}", styles["Normal"]),
    ]
    doc.build(story)


# ─────────────────────────────────────────────
# Seeding
# ─────────────────────────────────────────────
def clear_demo_data():
    """Remove any previously-seeded demo records (and their PDF files)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT dataset_id FROM datasets WHERE original_filename LIKE ?",
        (f"{DEMO_MARKER}%",)
    ).fetchall()
    conn.close()

    cleared = 0
    for row in rows:
        dataset_id = row["dataset_id"]
        draft = get_draft_by_dataset(dataset_id)
        if draft:
            conn = get_db()
            reports = conn.execute(
                "SELECT pdf_path FROM reports WHERE draft_id=?", (draft["draft_id"],)
            ).fetchall()
            conn.close()
            for r in reports:
                if r["pdf_path"] and os.path.exists(r["pdf_path"]):
                    try:
                        os.remove(r["pdf_path"])
                    except OSError:
                        pass
            delete_draft(draft["draft_id"])
        else:
            # Orphan dataset with no draft yet — delete directly.
            conn = get_db()
            conn.execute("DELETE FROM validation_results WHERE dataset_id=?", (dataset_id,))
            conn.execute("DELETE FROM extraction_results WHERE dataset_id=?", (dataset_id,))
            conn.execute("DELETE FROM audit_logs WHERE dataset_id=?", (dataset_id,))
            conn.execute("DELETE FROM datasets WHERE dataset_id=?", (dataset_id,))
            conn.commit()
            conn.close()
        cleared += 1
    return cleared


def seed_record(index, rec):
    filename    = rec["filename"]
    stored_path = os.path.join(UPLOAD_FOLDER, filename)

    # ── Dataset + validation ────────────────────────────────────────────
    dataset_id = create_dataset(filename, stored_path, rec["technician"], file_hash=f"demo-hash-{index}")
    update_dataset_status(dataset_id, "Extracting", validation_status="Valid")
    save_validation_result(dataset_id, True, None, "Valid")

    # ── Extraction ───────────────────────────────────────────────────────
    extraction_data = _build_extraction_data(rec, index)
    save_extraction_result(dataset_id, extraction_data)
    update_dataset_status(dataset_id, "Ready", extraction_status="Completed")

    # ── Draft + manual inputs ───────────────────────────────────────────
    draft_id = create_report_draft(dataset_id, extraction_data)

    is_draft = rec["status"] == "draft"
    form_data = {
        "event_classification": "" if is_draft else extraction_data["suggested_classification"],
        "vehicle_van":          rec["van"],
        "event_date":           rec["date"],
        "event_timestamp":      f"{rec['date']}T{rec['time']}",
        "technician_remarks":   "",
        "created_by_name":      rec["technician"],
    }
    save_manual_inputs(draft_id, form_data)
    log_action(dataset_id, "FORM_SAVED", f"Draft ID: {draft_id}")

    # ── Summary (Completed / In Progress only) ──────────────────────────
    if rec["summary"] is not None:
        summary_data = dict(rec["summary"])
        summary_data.setdefault("traffic_condition", rec["traffic"])
        summary_data.setdefault("haptic_signal", "")
        summary_data.setdefault("summary_off_state_braking", "")
        summary_data.setdefault("summary_driver_takeover", "")
        summary_data.setdefault("summary_no_abnormalities", "")
        summary_data.setdefault("braking_assessment", "")
        summary_data.setdefault("extra_lines", [])
        summary_data.setdefault("screenshots", [])
        save_summary_data(draft_id, summary_data)
        update_draft_status(draft_id, "In Progress")

    # ── Completed: generate a real (dummy-content) PDF + reports row ───
    if rec["status"] == "completed":
        update_draft_status(draft_id, "Completed")
        date_compact = rec["date"].replace("-", "")
        time_compact = rec["time"].replace(":", "")
        pdf_filename = f"report_draft{draft_id}_{date_compact}_{time_compact}.pdf"
        pdf_path = os.path.join(REPORTS_FOLDER, pdf_filename)
        _write_dummy_pdf(pdf_path, rec, extraction_data)
        generated_at = f"{rec['date']} {rec['time']}"
        save_report(draft_id, dataset_id, pdf_filename, pdf_path, generated_at=generated_at)

    return dataset_id, draft_id


def main():
    print("Seeding demo data...")
    init_db()

    for name in TECHNICIANS:
        add_technician(name)

    cleared = clear_demo_data()
    if cleared:
        print(f"Cleared {cleared} existing demo record(s).")

    for i, rec in enumerate(RECORDS, start=1):
        parts = rec["filename"].replace(".mcap", "").split("_")
        location, odd_label = parts[2], parts[3]
        dataset_id, draft_id = seed_record(i, rec)
        print(f"Record {i}/8 -- ODD{odd_label} {location} "
              f"(dataset #{dataset_id}, draft #{draft_id}, status={rec['status']})")

    print(f"Demo data seeding complete -- {len(RECORDS)} records created.")


if __name__ == "__main__":
    main()
