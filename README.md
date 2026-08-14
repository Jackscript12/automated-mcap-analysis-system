# Automated MCAP Analysis System

A Flask-based web application that automates the analysis of MCAP recordings from autonomous-driving (ADAS Level 3) test vehicles. It extracts event timelines, braking metrics, velocity, and ODD (Operational Design Domain) event codes directly from raw MCAP files, and produces structured, technician-reviewed PDF reports — replacing a manual, spreadsheet-and-Foxglove-based workflow.

## Table of Contents

- [Introduction](#introduction)
- [Problem Statement](#problem-statement)
- [System Overview](#system-overview)
- [Key Features](#key-features)
- [Tech Stack](#tech-stack)
- [System Requirements](#system-requirements)
- [Installation](#installation)
- [How to Run](#how-to-run)
- [How to Use](#how-to-use)
- [Project Structure](#project-structure)
- [Supported Event Types](#supported-event-types)
- [Braking Classification](#braking-classification)
- [Known Limitations](#known-limitations)
- [Author](#author)

## Introduction

**MCAP** is an open-source, container-style log format for storing timestamped, multi-channel message data — commonly used in robotics and autonomous-vehicle testing to record everything a vehicle's software stack observed and decided during a test drive (sensor states, planning outputs, HMI states, safety constraints, and more). Each `.mcap` file is essentially a black-box recording of a single test run.

The **Automated MCAP Analysis System** is a purpose-built internal tool for test technicians and engineers who need to turn these raw recordings into reviewable, reportable safety-event analyses. A technician uploads an `.mcap` file, the system automatically decodes the relevant protobuf channels and extracts the event timeline, braking severity, velocity change, and triggering ODD code, and the technician then reviews, annotates, and finalizes the result as a formal PDF report.

## Problem Statement

Before this system existed, analyzing a single MCAP recording was a manual process: a technician would open the file in Foxglove Studio, scrub through the timeline by hand to locate the relevant HMI state transitions, cross-reference ODD codes and braking signals visually, note down timestamps and values, and then manually assemble a report. This approach was:

- **Slow** — each event could take a significant amount of manual scrubbing and cross-referencing to analyze.
- **Error-prone** — timestamps, ODD codes, and braking values were transcribed by hand, with no consistency checks between analyses.
- **Inconsistent** — different technicians could interpret the same recording differently, with no single source of truth for how phases (Pre-Event, Event Triggered, Post-Event) or braking severity should be classified.
- **Not scalable** — as test volume grew, manual review became a bottleneck to getting safety-relevant findings reviewed and reported.

This system replaces that manual workflow with deterministic, repeatable extraction logic, while still keeping a technician firmly in the loop for review, verification, and sign-off before any report is finalized.

## System Overview

The system follows a four-phase workflow for every MCAP file:

1. **Upload** — A technician uploads a `.mcap` file (with their name attached) through the Upload page. The file is stored on disk and a `datasets` record is created. Duplicate files (matched by content hash) are detected and redirected to the existing analysis instead of creating a duplicate entry.
2. **Validate** — The file is opened with the `mcap` reader and checked for the required data channels (ego-motion/acceleration and autonomous-driving state). Files that are unreadable, corrupted, or missing required topics are rejected with a specific error message before extraction ever runs.
3. **Extract** — The core extraction engine (`extraction.py`) walks every relevant protobuf channel in a single pass (or a small number of passes) and builds:
   - The full **Event Timeline** (HMI state transitions from ACTIVE through TOR escalation, HAF/TAF takeover, and Post-Event/OFF).
   - **Maximum braking** deceleration and its severity classification.
   - **Velocity** at the point of the triggering event and its subsequent minimum.
   - The **ODD event code** that triggered the takeover, plus SCG (safety constraint) and MRM (minimum-risk-maneuver) context.
4. **Report** — The technician reviews the auto-extracted data on the Analysis page, fills in the remaining manual fields (event classification, vehicle/van identifier, remarks, screenshots, etc.), saves the analysis summary, marks it complete, and generates a final PDF report — which is then available for download from the Reports page.

## Key Features

- **Automated MCAP parsing** — direct protobuf decoding of ego-motion, autonomous-driving state, HMI state, ODD/state-machine, safety-constraint (SCG), MRM trigger, and driver-brake-torque channels, with no manual byte scanning.
- **Duplicate detection** — files are hashed on upload; re-uploading an already-analyzed file redirects to the existing analysis instead of creating a duplicate dataset.
- **Dynamic Event Timeline** — builds a phase-by-phase timeline (Pre Event → Pre-Event escalation → Event Triggered → Post-Event) directly from the recorded HMI transitions, with a collapsible "core vs. extended" view so reviewers see the meaningful rows by default and can expand to the full raw sequence when needed.
- **Braking window analysis** — combines an AD-Active-filtered window anchored around the TOR escalation ramp with unfiltered fallback windows (during HAF/TAF takeover and shortly after OFF) to reliably capture the true peak deceleration, even when the autonomous-driving system has already disengaged.
- **Velocity analysis** — peak velocity while under active autonomous driving and the subsequent minimum velocity, with automatic "Vehicle Standstill" detection at low speed.
- **ODD event classification** — resolves the triggering Operational Design Domain code to a human-readable name and a high-level event category (Crash Event / Strong Braking / Normal Event).
- **SCG (safety constraint) and MRM reporting** — surfaces the most relevant safety-constraint reason and whether a minimum-risk-maneuver was triggered.
- **Manual review workflow** — technicians complete required fields (event classification, vehicle/van, date, remarks), attach screenshots and free-text summary lines, and the system tracks draft status (`Draft` → `In Progress` → `Completed`).
- **Re-extraction** — an analysis can be re-run against the original MCAP file at any time (e.g. after an extraction-logic fix), with a loading overlay showing extraction progress and a summary of what changed versus the previous result.
- **PDF report generation** — a formatted PDF (via ReportLab) combining the extraction summary, event timeline, technician-completed fields, and attached screenshots.
- **Reports dashboard** — searchable, filterable (by braking-severity tab, free-text search, and date range) list of every generated report, with download and re-analysis links.
- **Audit logging** — key actions (upload, validation, extraction, form saves, report generation, deletion) are logged for traceability.
- **Technician management** — an editable list of technician names used when uploading files and attributing analyses.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend framework | Flask (Python) |
| MCAP parsing | `mcap` (official MCAP reader library) |
| Message decoding | `protobuf` (Google Protocol Buffers) |
| Database | SQLite (via `database.py`, raw SQL) |
| PDF generation | ReportLab |
| Frontend | Jinja2 templates, vanilla JavaScript, custom CSS |
| Numerical/data handling | NumPy, pandas |
| Image handling | Pillow |
| WSGI/HTTP | Werkzeug |

## System Requirements

- Python 3.10 or later (project has been run against newer CPython builds; `protobuf>=4.25.0` is required)
- pip (Python package manager)
- Sufficient local disk space for uploaded `.mcap` files, generated PDF reports, and screenshots — these can be large and are stored on disk, not in the database
- A modern web browser (Chrome, Edge, or Firefox recommended) to access the application UI

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/Jackscript12/automated-mcap-analysis-system.git
cd automated-mcap-analysis-system

# 2. Create and activate a virtual environment
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create the required storage directories
mkdir uploads reports screenshots
```

> The SQLite database file is created automatically on first run — no manual database setup is required.

## How to Run

```bash
python app.py
```

Then open your browser and navigate to:

```
http://localhost:5000
```

The application will initialize its SQLite database on first launch and serve the Home page.

## How to Use

1. **Upload an MCAP file** — go to **Upload MCAP**, select your technician name (or add a new one), drag-and-drop or browse for a `.mcap` file, and click **Upload & Process**. The system validates and extracts the file automatically.
2. **Review the extraction result** — you'll be taken to the Analysis page, where the **Extraction Result** card shows max braking, braking severity, velocity at event, ODD trigger, SCG constraint, and overall status, and the **Event Timeline** shows the full phase-by-phase breakdown.
3. **Re-extract if needed** — if the underlying extraction logic has been updated, use **Re-Extract** to re-run extraction against the original file without re-uploading; a summary of any changed values is shown afterward.
4. **Complete the analysis form** — fill in the required manual fields (event classification, vehicle/van, event date, technician remarks), add any free-text summary lines or screenshots, and save.
5. **Mark the analysis complete** — once all required fields are filled in, the draft can be marked `Completed`.
6. **Generate the PDF report** — from the Generate Report section, confirm and generate the final PDF, which is then listed on the **Reports** page.
7. **Find and download reports** — use the **Reports** page to search, filter by braking-severity classification or date range, and download any previously generated PDF report.

## Project Structure

```
Automated-MCAP-Analysis-System/
├── app.py                  # Flask application: routes, request handling, PDF generation
├── extraction.py           # Core MCAP parsing and event extraction engine
├── database.py             # SQLite schema, queries, and data access functions
├── test_extraction.py      # Extraction logic tests
├── requirements.txt        # Python dependencies
├── static/
│   ├── css/
│   │   └── style.css       # Application stylesheet
│   └── images/
│       └── logo.png        # Application logo
├── templates/
│   ├── index.html          # Home page
│   ├── upload.html         # Upload MCAP page
│   ├── analysis.html       # Event Analysis page (extraction result, timeline, form)
│   ├── reports.html        # Reports dashboard
│   ├── report.html         # PDF report template/layout reference
│   └── audit_logs.html     # Audit log viewer
├── uploads/                 # Uploaded .mcap files (created at setup, not versioned)
├── reports/                 # Generated PDF reports (created at setup, not versioned)
├── screenshots/              # Uploaded analysis screenshots (created at setup, not versioned)
└── mcap_analysis.db         # SQLite database (created automatically, not versioned)
```

## Supported Event Types

The system focuses on **Prio3** takeover events — the highest-priority driver-takeover ODD (Operational Design Domain) codes, which mark a genuine driver-initiated takeover during an HAF/TAF (Hands-off/Take-over-Function) escalation:

| ODD Code | Name | Description |
|---|---|---|
| `354` | `DriverTriesTakeoverByStrongBraking` | Driver takes over by braking strongly |
| `355` | `DriverTriesTakeoverByStrongSteering` | Driver takes over by steering strongly |

These two codes mark the true start of the **Event Triggered** phase within the timeline — any ODD activity recorded before the first Prio3 code within the HAF/TAF window is treated as still belonging to the Pre-Event phase. Events are further categorized at a high level as **Crash Event**, **Strong Braking**, or **Normal Event** depending on the specific ODD code that triggered them.

## Braking Classification

Peak longitudinal deceleration during the event window is classified into a severity label:

| Deceleration Range (m/s²) | Severity |
|---|---|
| Below −8.0 | Emergency Braking |
| −8.0 to −5.0 | Hard Braking |
| −5.0 to −3.0 | Moderate Braking |
| Above −3.0 | Light Braking |

For reporting and filtering purposes, events are also grouped into the following braking-magnitude buckets (used as the tabs on the Reports page):

| Bucket | Range (m/s²) |
|---|---|
| Below −4 | Weaker than −4.0 |
| −4 to −7 | −4.0 to −7.0 |
| −7 to −10 | −7.0 to −10.0 |

## Known Limitations

- **Single-machine, local-disk storage** — uploaded MCAP files, generated PDFs, and screenshots are stored on the local filesystem rather than in cloud/object storage, so disk space and backup strategy are the operator's responsibility.
- **SQLite as the data store** — suitable for a single-team, moderate-volume deployment, but not designed for high-concurrency, multi-instance use.
- **Schema-dependent extraction** — the extraction engine expects specific protobuf channel names and message structures; recordings from a differently-configured stack may fail validation or require extraction-logic updates.
- **No built-in authentication** — the application does not currently implement user login/authorization; access control is expected to be handled at the network/deployment level.
- **Manual review is still required** — the system automates extraction, not judgment; a technician must still review, classify, and sign off on every analysis before a report is generated.
- **Large file sizes** — MCAP recordings can be large (multiple gigabytes per file across a test session), which affects upload time and storage requirements.

## Author

**Ammar Bakhtiar Bin Aminuddin**
Student ID: 2023119167
Universiti Teknologi MARA (UiTM)

Developed during an industrial training placement at **EDAG Holding Sdn. Bhd.**

- **Supervisor:** Dr. Nur Suhailayani Binti Suhaimi
- **Industry Coach:** Muhammad Iskandar Bin Ab Rakib

---

*Last updated: August 2026*
