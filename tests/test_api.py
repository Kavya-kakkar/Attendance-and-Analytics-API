import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from pymongo import MongoClient
import os

from app.main import app, db, IST, now_epoch_ms, epoch_to_dt, dt_to_epoch

client = TestClient(app)


@pytest.fixture(autouse=True)
def seed_database():
    """Ensure sample data is loaded before each test or suite."""
    # Clear and re-seed clean predictable data
    db.employees.delete_many({})
    db.attendance_logs.delete_many({})

    # Seed core employees
    employees = [
        {
            "emp_code": "EMP0001",
            "name": "Asha Rao",
            "email": "asha@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
            "created_at": datetime(2026, 1, 5, 4, 30, tzinfo=timezone.utc),
        },
        {
            "emp_code": "EMP0002",
            "name": "Vikram Shah",
            "email": "vikram@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-07-13",
            "created_at": datetime(2026, 7, 13, 4, 0, tzinfo=timezone.utc),
        },
        {
            "emp_code": "EMP0003",
            "name": "Neha Iyer",
            "email": "neha@example.com",
            "department": "Sales",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-02-01",
            "created_at": datetime(2026, 2, 1, 4, 30, tzinfo=timezone.utc),
        },
        {
            "emp_code": "EMP0004",
            "name": "Rohit Das",
            "email": "rohit@example.com",
            "department": "Sales",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-02-01",
            "created_at": datetime(2026, 2, 1, 4, 31, tzinfo=timezone.utc),
        },
        {
            "emp_code": "EMP0005",
            "name": "Meera Nair",
            "email": "meera@example.com",
            "department": "Support",
            "shift_start": "22:00",
            "shift_end": "06:00",
            "joined_on": "2026-03-10",
            "created_at": datetime(2026, 3, 10, 4, 30, tzinfo=timezone.utc),
        },
        {
            "emp_code": "EMP0006",
            "name": "Karan Bedi",
            "email": "karan@example.com",
            "department": "Support",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-03-10",
            "created_at": datetime(2026, 3, 10, 4, 31, tzinfo=timezone.utc),
        },
    ]
    db.employees.insert_many(employees)

    # Seed sample attendance logs
    logs = [
        # Asha Rao: 2026-07-06 full day (normal)
        {
            "emp_code": "EMP0001",
            "date": "2026-07-06",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc),  # 09:28 IST
            "punch_out": datetime(2026, 7, 6, 13, 5, tzinfo=timezone.utc),  # 18:35 IST
            "work_hours": 9.12,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
            "history": [],
        },
        # Asha Rao: 2026-07-07 regularized
        {
            "emp_code": "EMP0001",
            "date": "2026-07-07",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 7, 3, 58, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 7, 13, 0, tzinfo=timezone.utc),
            "work_hours": 9.03,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
            "history": [{
                "at": datetime(2026, 7, 8, 5, 0, tzinfo=timezone.utc),
                "by": "hr.admin",
                "reason": "biometric glitch",
                "changes": {
                    "punch_in": {
                        "from": datetime(2026, 7, 7, 4, 35, tzinfo=timezone.utc),
                        "to": datetime(2026, 7, 7, 3, 58, tzinfo=timezone.utc),
                    },
                    "late_minutes": {"from": 35, "to": 0},
                    "work_hours": {"from": 8.42, "to": 9.03},
                },
            }],
        },
        # Vikram Shah: 2026-07-14 legacy doc
        {
            "emp_code": "EMP0002",
            "date": "2026-07-14",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 14, 4, 2, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 14, 13, 1, tzinfo=timezone.utc),
            "work_hours": 8.98,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
            "history": [],
        },
        # Neha Iyer: 2026-07-06 Late with overtime
        {
            "emp_code": "EMP0003",
            "date": "2026-07-06",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 6, 4, 35, tzinfo=timezone.utc),  # 10:05 IST (35m late)
            "punch_out": datetime(2026, 7, 6, 13, 40, tzinfo=timezone.utc),  # 19:10 IST (40m OT)
            "work_hours": 9.08,
            "late_minutes": 35,
            "overtime_minutes": 40,
            "half_day": False,
            "history": [],
        },
        # Neha Iyer: 2026-07-08 LEAVE
        {
            "emp_code": "EMP0003",
            "date": "2026-07-08",
            "status": "LEAVE",
            "punch_in": None,
            "punch_out": None,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
            "history": [],
        },
        # Rohit Das: 2026-07-07 Half day WFH
        {
            "emp_code": "EMP0004",
            "date": "2026-07-07",
            "status": "WFH",
            "punch_in": datetime(2026, 7, 7, 4, 0, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 7, 7, 30, tzinfo=timezone.utc),
            "work_hours": 3.5,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": True,
            "history": [],
        },
        # Rohit Das: 2026-07-08 ABSENT
        {
            "emp_code": "EMP0004",
            "date": "2026-07-08",
            "status": "ABSENT",
            "punch_in": None,
            "punch_out": None,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
            "history": [],
        },
        # Meera Nair: 2026-07-06 Overnight shift
        {
            "emp_code": "EMP0005",
            "date": "2026-07-06",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 6, 16, 25, tzinfo=timezone.utc),  # 21:55 IST
            "punch_out": datetime(2026, 7, 7, 1, 10, tzinfo=timezone.utc),  # 06:40 IST next day
            "work_hours": 8.75,
            "late_minutes": 0,
            "overtime_minutes": 40,
            "half_day": False,
            "history": [],
        },
        # Karan Bedi: 2026-07-07 Open record (not punched out)
        {
            "emp_code": "EMP0006",
            "date": "2026-07-07",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 7, 4, 5, tzinfo=timezone.utc),  # 09:35 IST
            "punch_out": None,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
            "history": [],
        },
    ]
    db.attendance_logs.insert_many(logs)
    yield


# --------------------------------------------------------------------------- #
# System Tests
# --------------------------------------------------------------------------- #
def test_health_check():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


# --------------------------------------------------------------------------- #
# Employee Endpoints Tests
# --------------------------------------------------------------------------- #
def test_create_employee_success():
    payload = {
        "emp_code": "EMP0010",
        "name": "Ananya Sharma",
        "email": "ananya@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-06-01",
    }
    res = client.post("/employees", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["emp_code"] == "EMP0010"
    assert "_id" not in data
    assert isinstance(data["created_at"], int)


def test_create_employee_duplicate_conflict():
    payload = {
        "emp_code": "EMP0001",  # already exists
        "name": "Duplicate User",
        "email": "dup@example.com",
        "department": "Engineering",
        "joined_on": "2026-01-01",
    }
    res = client.post("/employees", json=payload)
    assert res.status_code == 409


def test_create_employee_invalid_shifts():
    payload = {
        "emp_code": "EMP0099",
        "name": "Invalid Shifts",
        "email": "inv@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "09:30",  # equal shifts forbidden
        "joined_on": "2026-01-01",
    }
    res = client.post("/employees", json=payload)
    assert res.status_code == 422


def test_list_employees():
    res = client.get("/employees?department=Engineering&page=1&page_size=20")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    assert data["items"][0]["emp_code"] == "EMP0001"
    assert data["items"][1]["emp_code"] == "EMP0002"


# --------------------------------------------------------------------------- #
# Attendance Punch-In Tests (R1, R2, Concurrency)
# --------------------------------------------------------------------------- #
def test_punch_in_unknown_employee():
    res = client.post("/attendance/punch-in", json={"emp_code": "EMP9999"})
    assert res.status_code == 404


def test_punch_in_exact_10min_grace_not_late():
    # 2026-07-20 09:40:00 IST -> 04:10:00 UTC
    dt = datetime(2026, 7, 20, 4, 10, 0, tzinfo=timezone.utc)
    ms = int(dt.timestamp() * 1000)
    res = client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ms})
    assert res.status_code == 201
    data = res.json()
    assert data["late_minutes"] == 0
    assert data["date"] == "2026-07-20"


def test_punch_in_strictly_more_than_10min_is_late():
    # 2026-07-21 09:40:01 IST -> 04:10:01 UTC
    dt = datetime(2026, 7, 21, 4, 10, 1, tzinfo=timezone.utc)
    ms = int(dt.timestamp() * 1000)
    res = client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ms})
    assert res.status_code == 201
    data = res.json()
    assert data["late_minutes"] == 10


def test_punch_in_overnight_shift_previous_day():
    # EMP0005 has shift 22:00 - 06:00
    # Punch-in at 05:45 IST on 2026-07-22 (earlier than shift_end 06:00)
    # 05:45 IST is 00:15 UTC on 2026-07-22
    dt = datetime(2026, 7, 22, 0, 15, 0, tzinfo=timezone.utc)
    ms = int(dt.timestamp() * 1000)
    res = client.post("/attendance/punch-in", json={"emp_code": "EMP0005", "punched_at": ms})
    assert res.status_code == 201
    data = res.json()
    assert data["date"] == "2026-07-21"  # belongs to shift starting previous day


def test_punch_in_duplicate_conflict():
    # EMP0001 already has attendance on 2026-07-06
    dt = datetime(2026, 7, 6, 4, 0, 0, tzinfo=timezone.utc)
    ms = int(dt.timestamp() * 1000)
    res = client.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": ms})
    assert res.status_code == 409


# --------------------------------------------------------------------------- #
# Attendance Punch-Out Tests (R3, R4, R5)
# --------------------------------------------------------------------------- #
def test_punch_out_unknown_employee():
    res = client.post("/attendance/punch-out", json={"emp_code": "EMP9999"})
    assert res.status_code == 404


def test_punch_out_no_open_record():
    # EMP0001 is already punched out on 2026-07-06 and 2026-07-07
    dt = datetime(2026, 7, 7, 14, 0, 0, tzinfo=timezone.utc)
    ms = int(dt.timestamp() * 1000)
    res = client.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": ms})
    assert res.status_code == 409


def test_punch_out_success_and_derived_fields():
    # EMP0006 is open on 2026-07-07 punched in at 04:05 UTC (09:35 IST)
    # Punch out at 19:15 IST (13:45 UTC) -> 45m overtime (>= 30 min)
    dt = datetime(2026, 7, 7, 13, 45, 0, tzinfo=timezone.utc)
    ms = int(dt.timestamp() * 1000)
    res = client.post("/attendance/punch-out", json={"emp_code": "EMP0006", "punched_at": ms})
    assert res.status_code == 200
    data = res.json()
    assert data["work_hours"] == 9.67
    assert data["overtime_minutes"] == 45
    assert data["half_day"] is False
    assert data["punch_out"] == ms


def test_punch_out_before_punch_in_fails():
    # EMP0006 punched in at 04:05 UTC on 2026-07-07
    dt = datetime(2026, 7, 7, 3, 0, 0, tzinfo=timezone.utc)
    ms = int(dt.timestamp() * 1000)
    res = client.post("/attendance/punch-out", json={"emp_code": "EMP0006", "punched_at": ms})
    assert res.status_code == 422


# --------------------------------------------------------------------------- #
# List Attendance Tests
# --------------------------------------------------------------------------- #
def test_list_attendance():
    res = client.get("/attendance?date_from=2026-07-06&date_to=2026-07-08")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 6
    # Verify sorting: date DESC, emp_code ASC
    items = data["items"]
    for i in range(len(items) - 1):
        d1, d2 = items[i]["date"], items[i + 1]["date"]
        e1, e2 = items[i]["emp_code"], items[i + 1]["emp_code"]
        assert (d1 > d2) or (d1 == d2 and e1 <= e2)


def test_list_attendance_date_validation():
    res = client.get("/attendance?date_from=2026-07-10&date_to=2026-07-05")
    assert res.status_code == 422


# --------------------------------------------------------------------------- #
# Regularization Tests (PATCH)
# --------------------------------------------------------------------------- #
def test_regularize_attendance_success():
    # EMP0001 on 2026-07-06: currently punch_in is 03:58 UTC (09:28 IST)
    # Correct punch_in to 04:35 UTC (10:05 IST) -> 35m late
    new_in_dt = datetime(2026, 7, 6, 4, 35, 0, tzinfo=timezone.utc)
    ms_in = int(new_in_dt.timestamp() * 1000)
    payload = {
        "punch_in": ms_in,
        "reason": "Correcting gate punch entry error",
        "regularized_by": "manager.amit",
    }
    res = client.patch("/attendance/EMP0001/2026-07-06", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["late_minutes"] == 35
    assert len(data["history"]) == 1
    assert data["history"][0]["by"] == "manager.amit"
    assert "late_minutes" in data["history"][0]["changes"]


def test_regularize_attendance_absent_cannot_have_punch():
    payload = {
        "status": "ABSENT",
        "punch_in": 1783312500000,
        "reason": "Attempting invalid absent with punches",
        "regularized_by": "admin",
    }
    res = client.patch("/attendance/EMP0001/2026-07-06", json=payload)
    assert res.status_code == 422


def test_regularize_attendance_no_changes():
    payload = {
        "status": "PRESENT",  # already PRESENT
        "reason": "No changes actually made",
        "regularized_by": "admin",
    }
    res = client.patch("/attendance/EMP0001/2026-07-06", json=payload)
    assert res.status_code == 422


# --------------------------------------------------------------------------- #
# Analytics Tests
# --------------------------------------------------------------------------- #
def test_analytics_employee_monthly():
    res = client.get("/analytics/employees/EMP0001/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["emp_code"] == "EMP0001"
    assert data["month"] == "2026-07"
    assert data["working_days"] == 23  # July 2026 has 23 weekdays
    assert data["present_days"] == 2.0
    assert data["leave_days"] == 0
    assert data["attendance_pct"] is not None


def test_analytics_department_summary():
    res = client.get("/analytics/departments/summary?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["month"] == "2026-07"
    depts = {item["department"]: item for item in data["items"]}
    assert "Engineering" in depts
    assert "Sales" in depts
    assert "Support" in depts
    assert depts["Engineering"]["headcount"] == 2
    assert depts["Sales"]["present_days"] == 1.5  # Rohit 0.5 + Neha 1.0


def test_analytics_late_leaderboard():
    res = client.get("/analytics/leaderboard/late?month=2026-07&limit=5")
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) >= 1
    assert data["items"][0]["rank"] == 1
    assert data["items"][0]["emp_code"] == "EMP0003"
    assert data["items"][0]["total_late_minutes"] == 35


def test_analytics_department_trend():
    res = client.get("/analytics/departments/Sales/trend?from=2026-07-05&to=2026-07-10")
    assert res.status_code == 200
    data = res.json()
    assert data["department"] == "Sales"
    # Exactly 6 days: July 5 to 10
    assert len(data["items"]) == 6
    assert data["items"][0]["date"] == "2026-07-05"
    assert data["items"][0]["is_working_day"] is False
    assert data["items"][1]["date"] == "2026-07-06"
    assert data["items"][1]["is_working_day"] is True
    assert data["items"][1]["present_count"] == 1.0


def test_admin_explain():
    res = client.get("/admin/explain/attendance_list?date_from=2026-07-01&date_to=2026-07-31")
    assert res.status_code == 200
    data = res.json()
    assert data["endpoint"] == "attendance_list"
    assert data["collection"] == "attendance_logs"
    assert "explain" in data


# --------------------------------------------------------------------------- #
# Concurrency & Deep Edge Case Tests
# --------------------------------------------------------------------------- #
def test_concurrency_punch_in_race():
    """Simulate concurrent duplicate punch-ins: exactly one 201, rest 409."""
    import concurrent.futures

    # Prepare punch for EMP0002 on 2026-07-25
    dt = datetime(2026, 7, 25, 4, 0, 0, tzinfo=timezone.utc)
    ms = int(dt.timestamp() * 1000)

    def do_punch():
        return client.post("/attendance/punch-in", json={"emp_code": "EMP0002", "punched_at": ms})

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(do_punch) for _ in range(5)]
        results = [f.result() for f in futures]

    statuses = [r.status_code for r in results]
    assert statuses.count(201) == 1
    assert statuses.count(409) == 4


def test_concurrency_punch_out_race():
    """Simulate concurrent duplicate punch-outs on same open record: exactly one 200, rest 409."""
    import concurrent.futures

    # Open record for EMP0006 on 2026-07-07
    dt = datetime(2026, 7, 7, 13, 0, 0, tzinfo=timezone.utc)
    ms = int(dt.timestamp() * 1000)

    def do_punch_out():
        return client.post("/attendance/punch-out", json={"emp_code": "EMP0006", "punched_at": ms})

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(do_punch_out) for _ in range(5)]
        results = [f.result() for f in futures]

    statuses = [r.status_code for r in results]
    assert statuses.count(200) == 1
    assert statuses.count(409) == 4


def test_leaderboard_competition_ranking_ties():
    """Verify standard competition ranking: ties share rank, next rank skipped (1, 2, 2, 4)."""
    # Insert two tied employees with 45 late minutes
    db.attendance_logs.insert_many([
        {
            "emp_code": "EMP0001",
            "date": "2026-07-28",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 28, 4, 45, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 28, 13, 0, tzinfo=timezone.utc),
            "work_hours": 8.25,
            "late_minutes": 50,
            "overtime_minutes": 0,
            "half_day": False,
            "history": [],
        },
        {
            "emp_code": "EMP0002",
            "date": "2026-07-28",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 28, 4, 45, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 28, 13, 0, tzinfo=timezone.utc),
            "work_hours": 8.25,
            "late_minutes": 50,
            "overtime_minutes": 0,
            "half_day": False,
            "history": [],
        },
    ])
    # EMP0003 has 35 late minutes (already seeded)
    # Total late minutes: EMP0001=50, EMP0002=50, EMP0003=35
    # Ranks should be: 1, 1, 3! (Not 1, 2, 3)
    res = client.get("/analytics/leaderboard/late?month=2026-07&limit=2")
    assert res.status_code == 200
    items = res.json()["items"]
    # With limit=2, both rank 1 employees must be returned!
    assert len(items) == 2
    assert items[0]["rank"] == 1
    assert items[1]["rank"] == 1
    assert items[0]["total_late_minutes"] == 50
    assert items[1]["total_late_minutes"] == 50

    # With limit=3, all three returned, third has rank 3!
    res3 = client.get("/analytics/leaderboard/late?month=2026-07&limit=3")
    items3 = res3.json()["items"]
    assert len(items3) == 3
    assert items3[2]["rank"] == 3
    assert items3[2]["emp_code"] == "EMP0003"


def test_mid_month_joiner_working_days():
    """R7: For an employee who joined mid-month, working days counted from joined_on."""
    # Vikram Shah joined 2026-07-13 (Monday).
    # July 13 to July 31 2026 has 15 weekdays (Mon-Fri).
    res = client.get("/analytics/employees/EMP0002/monthly?month=2026-07")
    assert res.status_code == 200
    data = res.json()
    assert data["working_days"] == 15


def test_admin_explain_all_endpoints():
    """Verify explain works for all 5 endpoints and returns executionStats."""
    for ep, params in [
        ("attendance_list", "?date_from=2026-07-01&date_to=2026-07-31"),
        ("employee_monthly", "?emp_code=EMP0001&month=2026-07"),
        ("department_summary", "?month=2026-07"),
        ("late_leaderboard", "?month=2026-07&limit=10"),
        ("department_trend", "?department=Engineering&from=2026-07-01&to=2026-07-10"),
    ]:
        res = client.get(f"/admin/explain/{ep}{params}")
        assert res.status_code == 200, f"Failed for {ep}: {res.text}"
        data = res.json()
        has_stats = ("executionStats" in data["explain"]) or ("stages" in data["explain"] and "executionStats" in data["explain"]["stages"][0].get("$cursor", {}))
        assert has_stats, f"Execution stats missing for {ep}"

