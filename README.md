# Automated MCAP Analysis System

A Flask-based web application that automates the analysis of MCAP recordings from autonomous-driving test vehicles operating at **Advanced Driver Assistance System (ADAS) Level 3** — a level of driving automation where the vehicle's software can drive on its own under certain conditions, but the human driver must still be ready to take back control when asked. It extracts event timelines, braking metrics, vehicle speed, and ODD (Operational Design Domain — a set of conditions under which the autonomous system is designed to operate) event codes directly from raw MCAP files, and produces structured, technician-reviewed PDF reports — replacing a manual, spreadsheet-and-visualization-tool-based workflow.

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

**MCAP** is an open-source, container-style log file format used to store time-series sensor and signal data from autonomous vehicles — essentially a timestamped recording of everything a vehicle's software observed and decided during a test drive (sensor readings, planning outputs, dashboard/interface states, safety limits, and more). Each `.mcap` file is like a black-box flight recorder for a single test run.

The **Automated MCAP Analysis System** is a purpose-built internal tool for test technicians and engineers who need to turn these raw recordings into reviewable, reportable safety-event analyses. A technician uploads an `.mcap` file, and the system automatically decodes the relevant data channels — encoded using **Protobuf** (short for Protocol Buffers, a compact data format used to package and encode the signal messages stored inside MCAP files) — and extracts the event timeline, braking severity, change in vehicle speed, and the ODD code that triggered the event. The technician then reviews, annotates, and finalizes the result as a formal PDF report.

## Problem Statement

Before this system existed, analyzing a single MCAP recording was a manual process: a technician would open the file in Foxglove Studio (a third-party tool for visually inspecting robotics and vehicle sensor log files), scrub through the recording by hand to locate the relevant **HMI** (Human-Machine Interface — the system that communicates the vehicle's status to the driver, for example through dashboard alerts) state changes, cross-reference ODD codes and braking signals visually, note down timestamps and values, and then manually assemble a report. This approach was:

- **Slow** — each event could take a significant amount of manual scrubbing and cross-referencing to analyze.
- **Error-prone** — timestamps, ODD codes, and braking values were transcribed by hand, with no consistency checks between analyses.
- **Inconsistent** — different technicians could interpret the same recording differently, with no single source of truth for how the stages of an event (before the takeover, the moment it happens, and afterward) or braking severity should be classified.
- **Not scalable** — as test volume grew, manual review became a bottleneck to getting safety-relevant findings reviewed and reported.

This system replaces that manual workflow with deterministic, repeatable extraction logic, while still keeping a technician firmly in the loop for review, verification, and sign-off before any report is finalized.

## System Overview

The system follows a four-phase workflow for every MCAP file:

1. **Upload** — A technician uploads a `.mcap` file (with their name attached) through the Upload page. The file is stored on disk and a database record is created. Duplicate files (matched by content hash, a unique fingerprint calculated from the file's contents) are detected and redirected to the existing analysis instead of creating a duplicate entry.
2. **Validate** — The file is opened with the official `mcap` reader library and checked for the required data channels — including the vehicle's own motion/acceleration data (often called "ego-motion," meaning the motion of the vehicle itself, as measured by its own sensors) and its **AD State** (Autonomous Driving State — indicates whether the vehicle's autonomous system is active or inactive at any given moment). Files that are unreadable, corrupted, or missing required data are rejected with a specific error message before extraction ever runs.
3. **Extract** — The core extraction engine (`extraction.py`) walks every relevant Protobuf-encoded data channel in a single pass (or a small number of passes) and builds:
   - The full **Event Timeline** — HMI state changes from normal driving, through **TOR** (Take-Over Request — a signal requesting the driver to resume manual control) escalation, through the final **HAF_TAF_TOR** escalation phase (the last stage before the driver fully takes back control), and on to the Post-Event/OFF stage once the handover is complete.
   - **Maximum braking** deceleration and its severity classification.
   - **Vehicle speed** at the point of the triggering event and its subsequent lowest point.
   - The **ODD event code** that triggered the takeover, plus context from the **SCG** (Safety Constraint Generator — a module that enforces safety limits on vehicle motion) and whether an **MRM** (Minimal Risk Maneuver — an emergency procedure that safely brings the vehicle to a stop) was triggered.
4. **Report** — The technician reviews the auto-extracted data on the Analysis page, fills in the remaining manual fields (event classification, vehicle/van identifier, remarks, screenshots, etc.), saves the analysis summary, marks it complete, and generates a final PDF report — which is then available for download from the Reports page.

## Key Features

- **Automated MCAP parsing** — direct Protobuf decoding of vehicle motion, AD State, HMI state, ODD/state-machine, SCG (safety constraint), MRM trigger, and driver-brake-pedal-pressure data channels, with no manual byte scanning.
- **Duplicate detection** — files are fingerprinted (hashed) on upload; re-uploading an already-analyzed file redirects to the existing analysis instead of creating a duplicate dataset.
- **Dynamic Event Timeline** — builds a phase-by-phase timeline (Pre-Event → TOR escalation → the moment the driver takes over → Post-Event) directly from the recorded HMI state changes, with a collapsible "core vs. extended" view so reviewers see the meaningful rows by default and can expand to see the full raw sequence — including early startup or "ramp-up" states such as PREACTIVE and ACTIVATION_PHASE (the stages the system passes through before autonomous driving becomes fully active) — when needed.
- **Braking window analysis** — combines a window filtered to only when the AD State was active, anchored around the TOR escalation build-up, with unfiltered fallback windows (during the final HAF_TAF_TOR takeover and shortly after the system turns off) to reliably capture the true peak deceleration, even when the autonomous-driving system has already disengaged.
- **Vehicle speed analysis** — peak speed while under active autonomous driving and the subsequent lowest speed, with automatic "Vehicle Standstill" detection at low speed.
- **ODD event classification** — resolves the triggering Operational Design Domain code to a human-readable name and a high-level event category (Crash Event / Strong Braking / Normal Event).
- **SCG and MRM reporting** — surfaces the most relevant safety-constraint reason and whether a Minimal Risk Maneuver was triggered.
- **Manual review workflow** — technicians complete required fields (event classification, vehicle/van, date, remarks), attach screenshots and free-text summary lines, and the system tracks draft status (`Draft` → `In Progress` → `Completed`).
- **Re-extraction** — an analysis can be re-run against the original MCAP file at any time (e.g. after an extraction-logic fix), with a loading overlay showing extraction progress and a summary of what changed versus the previous result.
- **PDF report generation** — a formatted PDF (via ReportLab, a Python library for generating PDF documents) combining the extraction summary, event timeline, technician-completed fields, and attached screenshots.
- **Reports dashboard** — searchable, filterable (by braking-severity tab, free-text search, and date range) list of every generated report, with download and re-analysis links.
- **Audit logging** — key actions (upload, validation, extraction, form saves, report generation, deletion) are logged for traceability.
- **Technician management** — an editable list of technician names used when uploading files and attributing analyses.

## Tech Stack

| Layer | Technology | What it does |
|---|---|---|
| Backend framework | Flask (Python) | Handles web requests and serves the application's pages |
| MCAP parsing | `mcap` library | Reads MCAP log files — the format used to store time-series sensor and signal data from autonomous vehicles |
| Message decoding | Protobuf (Protocol Buffers) | A compact data format used to encode the individual signal messages stored inside MCAP files |
| Data compression | zstandard, lz4 | Fast compression algorithms some MCAP files use internally to keep recordings smaller |
| Database | SQLite | A lightweight, file-based embedded database — no separate database server needs to be installed or run |
| PDF generation | ReportLab | A Python library used to generate the final PDF reports |
| Frontend | Jinja2 templates, vanilla JavaScript, custom CSS | Builds the web pages and interactive elements the user sees in the browser |
| Numerical/data handling | NumPy, pandas | Python libraries for working with numerical values and tabular data |
| Image handling | Pillow | A Python library for processing image files, such as attached screenshots |
| Web server toolkit | Werkzeug | The underlying toolkit Flask uses to handle web requests and responses (WSGI/HTTP) |

## System Requirements

- Python 3.10 or later (project has been run against newer CPython builds; `protobuf>=4.25.0` is required)
- pip (Python's package manager, used to install the project's dependencies)
- Sufficient local disk space for uploaded `.mcap` files, generated PDF reports, and screenshots — these can be large and are stored on disk, not in the database
- A modern web browser (Chrome, Edge, or Firefox recommended) to access the application UI (User Interface — the pages and controls you interact with in the browser)

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/Jackscript12/automated-mcap-analysis-system.git
cd automated-mcap-analysis-system

# 2. Create and activate a virtual environment
# (an isolated Python environment so this project's dependencies
#  don't conflict with anything else installed on your system)
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
2. **Review the extraction result** — you'll be taken to the Analysis page, where the **Extraction Result** card shows maximum braking, braking severity, vehicle speed at the event, the ODD code that triggered it, the SCG (safety constraint) reason, and overall status, and the **Event Timeline** shows the full phase-by-phase breakdown.
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

The system focuses on **Prio3** (Priority 3) takeover events — driver-initiated takeover actions flagged for review, marking a genuine, deliberate takeover by the driver during a HAF_TAF_TOR (the final escalation phase before full driver takeover):

| ODD Code | Name | Description |
|---|---|---|
| `354` | `DriverTriesTakeoverByStrongBraking` | Driver takes over by braking strongly |
| `355` | `DriverTriesTakeoverByStrongSteering` | Driver takes over by steering strongly |

These two codes mark the true start of the **Event Triggered** phase within the timeline — the point where the driver has actually begun taking back control. Any ODD activity recorded before the first Prio3 code within the HAF_TAF_TOR window is treated as still belonging to the Pre-Event phase, before the takeover began. Events are further categorized at a high level as **Crash Event**, **Strong Braking**, or **Normal Event** depending on the specific ODD code that triggered them.

## Braking Classification

Peak longitudinal (forward/backward) deceleration during the event window is measured in **m/s²** (meters per second squared — a unit describing how quickly the vehicle's speed is decreasing) and classified into a severity label:

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
- **SQLite as the data store** — suitable for a single-team, moderate-volume deployment, but not designed for high-concurrency (many people reading and writing data at the exact same time), multi-instance use.
- **Schema-dependent extraction** — the extraction engine expects specific Protobuf data-channel names and message structures; recordings from a differently-configured vehicle software stack may fail validation or require extraction-logic updates.
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
