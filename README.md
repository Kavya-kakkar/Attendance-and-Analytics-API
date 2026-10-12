# Employee Attendance & Analytics API

A robust, production-ready backend service built with **FastAPI** and **MongoDB** for employee attendance management, automated rule-based calculations, audit logging, and analytical reporting. Developed for the HROne Software Engineer Trainee assignment.

---

## 1. Project Overview

This API manages daily employee attendance tracking and analytics for an HR system:
- **Employee Management**: Unique business employee code (`emp_code`), shift configurations (standard and overnight), and department assignments.
- **Punch Operations**: Automated punch-in and punch-out with race-safe concurrency controls, overnight shift boundary resolution, grace-period late tracking, work duration calculation, and overtime cutoffs.
- **Audit Regularization**: Manual attendance corrections with an immutable audit history capturing before-and-after change diffs and actor details.
- **Advanced Analytics**: MongoDB aggregation pipelines executing cross-collection summaries, competition-ranked leaderboards with window functions, and daily attendance trends with gap filling.
- **Admin Query Execution Plans**: An explain endpoint verifying index usage (`IXSCAN`) and absence of collection scans (`COLLSCAN`).

---

## 2. Technology Stack

- **Language**: Python 3.11+ (verified on 3.13 / 3.14)
- **Web Framework**: FastAPI & Uvicorn
- **Database**: MongoDB 6.0+ (Atlas or local instance)
- **Driver**: PyMongo 4.7+
- **Data Validation**: Pydantic v2
- **Testing**: Pytest & HTTPX TestClient

---

## 3. Implemented Features & Endpoints

| Area | Method | Endpoint | Description |
|---|---|---|---|
| **System** | `GET` | `/health` | Readiness probe returning `200` only when MongoDB responds to a ping, else `503`. |
| **Employees** | `POST` | `/employees` | Registers an employee; enforces unique `emp_code` and shift validations (`409` on duplicate). |
| | `GET` | `/employees` | Lists employees sorted by `emp_code` ascending with pagination and department filters. |
| **Attendance** | `POST` | `/attendance/punch-in` | Records punch-in; evaluates IST dates, overnight shifts (R1), and grace periods (R2). |
| | `POST` | `/attendance/punch-out` | Closes open punch records; computes work hours (R4), overtime (R3), and half-day (R5). |
| | `GET` | `/attendance` | Lists attendance sorted by `date` DESC, `emp_code` ASC with MongoDB-level pagination. |
| | `PATCH` | `/attendance/{emp_code}/{date}` | Regularizes attendance, recomputes derived metrics, and appends to immutable `history`. |
| **Analytics** | `GET` | `/analytics/employees/{emp_code}/monthly` | Monthly employee summary via aggregation (R5, R6, R7, R8). |
| | `GET` | `/analytics/departments/summary` | Monthly department cross-collection summary including zero-log headcount (R9). |
| | `GET` | `/analytics/leaderboard/late` | Late leaderboard using standard competition ranking with ties (1, 2, 2, 4). |
| | `GET` | `/analytics/departments/{department}/trend` | Daily trend with `$densify` gap-filling and 7-day moving averages. |
| **Admin** | `GET` | `/admin/explain/{endpoint}` | Returns raw MongoDB execution plan with `executionStats` for performance verification. |

---

## 4. Business Rules Implemented (R1 – R10)

- **R1 (Time Zone & Date)**: IST (UTC+05:30) is the business timezone. For overnight shifts (`shift_end <= shift_start`), punch-ins prior to `shift_end` IST belong to the previous day's shift. Instants are truncated to whole seconds (`ms // 1000 * 1000`).
- **R2 (Late Minutes)**: 10-minute grace period. Punch-in is late only if strictly > 10:00 after shift start; when late, `late_minutes = floor((punch_in - shift_start) / 60)` from shift start.
- **R3 (Overtime)**: Whole minutes (floor) past shift end, counted only when &ge; 30 minutes; otherwise 0.
- **R4 (Work Hours)**: `(punch_out - punch_in) in seconds / 3600`, rounded to 2 decimals half-up (`ROUND_HALF_UP`).
- **R5 (Half Day)**: `half_day = True` if rounded `work_hours < 4.50`. Half days count as 0.5 of a present day.
- **R6 (Presence)**: `PRESENT`, `WFH`, and `ON_DUTY` count as present; `ABSENT` and `LEAVE` do not.
- **R7 (Working Days)**: Mon–Fri calendar days. For mid-month joiners, counted from `max(first_day_of_month, joined_on)`. `present_days` only counts Mon–Fri records (weekend records still add to late and overtime totals).
- **R8 (Rounding)**: 2 decimals half-up for counts/hours/percentages; 4 decimals half-up for rates.
- **R9 (Headcount)**: Employees with `joined_on <= end_of_period`, including employees with zero logs.
- **R10 (Pagination)**: `page` starts at 1, max `page_size` 100 (default 20), `total` matches filtered count.

---

## 5. Local Setup & Execution Guide

### Prerequisites
- Python 3.11+
- MongoDB 6.0+ running locally (default: `localhost:27017`) or a MongoDB Atlas cluster URI.

### Step 1: Clone and Set Up Virtual Environment

**Windows PowerShell:**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Step 2: Environment Variables

Set environment variables if connecting to an external MongoDB or custom database name:

```powershell
$env:MONGO_URI = "mongodb://localhost:27017"
$env:MONGO_DB  = "attendance_db"
```
*(By default, fallback values `mongodb://localhost:27017` and `attendance_db` are used automatically if variables are not provided.)*

### Step 3: Seed Sample Data

Load the 9 sample documents from `sample_data/`:
```bash
python sample_seed.py
```

### Step 4: Run the FastAPI Application

Start the server using Uvicorn:
```bash
uvicorn app.main:app --port 8000 --reload
```
- Interactive API Documentation (Swagger UI): `http://localhost:8000/docs`
- Alternative API Documentation (ReDoc): `http://localhost:8000/redoc`
- Health Probe: `http://localhost:8000/health`

---

## 6. Running Automated Tests

Run the full automated test suite covering all business rules, concurrency race conditions, ties, and execution plans:

```bash
python -m pytest tests/test_api.py -v
```

All 29 tests execute against the configured test database and verify:
- Employee CRUD & validations
- Grace period & overnight punch-in boundary math
- Single-winner concurrency protection for simultaneous punch-in and punch-out
- Regularization audit trails and validation constraints
- MongoDB aggregation pipelines with `$densify` and `$setWindowFields`
- Query execution plans via `/admin/explain/{endpoint}`

---

## 7. Submission Checklist & Status

- [x] Application starts cleanly from repository root via `uvicorn app.main:app --port 8000`.
- [x] All database indexes created idempotently at startup.
- [x] Environment variables `MONGO_URI` and `MONGO_DB` supported with fallback defaults.
- [x] `REVIEW.md` completed with all 12 defects, symptoms, and fixes.
- [x] `DECISIONS.md` completed answering the 5 architectural questions.
- [x] Comprehensive test suite in `tests/test_api.py` passing 100%.
- [x] No secrets, `.env`, Dockerfile, or temporary virtual environment files committed.
