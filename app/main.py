"""
Employee Attendance & Analytics API

FastAPI application fulfilling the HROne Engineering Assignment contract.
Adheres strictly to business rules R1 - R10, openapi.yaml, and DATA_MODEL.md.
"""
import calendar
import json
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any, Dict, List, Optional

from bson import json_util
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, Field, model_validator
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

load_dotenv()

# --------------------------------------------------------------------------- #
# Configuration & Database Client
# --------------------------------------------------------------------------- #
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

client = MongoClient(MONGO_URI, tz_aware=True)
db = client[MONGO_DB]

IST = timezone(timedelta(hours=5, minutes=30))


def create_indexes():
    """Create all required indexes idempotently at startup."""
    # employees collection
    db.employees.create_index([("emp_code", 1)], unique=True, name="idx_emp_code_unique")
    db.employees.create_index([("department", 1), ("emp_code", 1)], name="idx_dept_emp_code")
    db.employees.create_index([("joined_on", 1)], name="idx_joined_on")

    # attendance_logs collection
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True, name="idx_emp_date_unique")
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)], name="idx_date_desc_emp_asc")
    db.attendance_logs.create_index([("emp_code", 1), ("punch_in", -1)], name="idx_emp_punch_in_desc")
    db.attendance_logs.create_index([("date", 1), ("late_minutes", 1)], name="idx_date_late_minutes")
    db.attendance_logs.create_index([("status", 1)], name="idx_status")


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_indexes()
    yield
    client.close()


app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan,
)

# Ensure indexes are created idempotently on startup
try:
    create_indexes()
except Exception:
    pass



# --------------------------------------------------------------------------- #
# Domain Helpers (Rules R1 - R8)
# --------------------------------------------------------------------------- #
def round_half_up_2(val: Optional[float]) -> Optional[float]:
    """Round a number to 2 decimal places using arithmetic half-up rounding (R8)."""
    if val is None:
        return None
    return float(Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def round_half_up_4(val: Optional[float]) -> Optional[float]:
    """Round a rate to 4 decimal places using arithmetic half-up rounding (R8)."""
    if val is None:
        return None
    return float(Decimal(str(val)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def epoch_to_dt(ms: int) -> datetime:
    """
    Convert epoch milliseconds to a UTC datetime.
    Truncates to whole seconds before conversion per R1.
    """
    truncated_ms = (ms // 1000) * 1000
    return datetime.fromtimestamp(truncated_ms / 1000, tz=timezone.utc)


def dt_to_epoch(dt: Optional[datetime]) -> Optional[int]:
    """Convert a BSON/Python datetime to epoch milliseconds integer."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def now_epoch_ms() -> int:
    """Current UTC server time as integer epoch milliseconds truncated to whole seconds."""
    ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return (ms // 1000) * 1000


def is_overnight_shift(shift_start: str, shift_end: str) -> bool:
    """True when shift_end <= shift_start (R1)."""
    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))
    return (eh, em) <= (sh, sm)


def compute_attendance_date(punch_in_dt: datetime, shift_start: str, shift_end: str) -> str:
    """
    Determine attendance date string 'YYYY-MM-DD' per R1.
    A record's date is the IST calendar date of its punch_in.
    Overnight shifts: a punch-in earlier than shift_end belongs to the shift that started the previous day.
    """
    dt_ist = punch_in_dt.astimezone(IST)
    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))
    if (eh, em) <= (sh, sm):
        # Overnight shift
        if (dt_ist.hour, dt_ist.minute, dt_ist.second) < (eh, em, 0):
            return (dt_ist.date() - timedelta(days=1)).isoformat()
    return dt_ist.date().isoformat()


def compute_late_minutes(punch_in_dt: datetime, shift_start: str, date_str: str) -> int:
    """
    Compute late minutes per R2.
    Grace period is 10 minutes. Late only if punch-in is strictly > 10:00 after shift_start.
    When late, late_minutes = whole minutes (floor) elapsed since shift_start.
    """
    sh, sm = map(int, shift_start.split(":"))
    shift_start_dt = datetime.fromisoformat(f"{date_str}T{sh:02d}:{sm:02d}:00").replace(tzinfo=IST)
    punch_in_ist = punch_in_dt.astimezone(IST)
    diff_sec = (punch_in_ist - shift_start_dt).total_seconds()
    if diff_sec > 600:
        return int(diff_sec // 60)
    return 0


def compute_overtime(punch_out_dt: datetime, shift_start: str, shift_end: str, date_str: str) -> int:
    """
    Compute overtime minutes per R3.
    Whole minutes (floor) between shift_end and punch_out, counted only when >= 30; otherwise 0.
    Overnight shift end falls on the next calendar day.
    """
    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))
    shift_end_date = date.fromisoformat(date_str)
    if (eh, em) <= (sh, sm):
        shift_end_date += timedelta(days=1)
    shift_end_dt = datetime.fromisoformat(f"{shift_end_date.isoformat()}T{eh:02d}:{em:02d}:00").replace(tzinfo=IST)
    punch_out_ist = punch_out_dt.astimezone(IST)
    diff_sec = (punch_out_ist - shift_end_dt).total_seconds()
    mins = int(diff_sec // 60)
    return mins if mins >= 30 else 0


def compute_work_hours(punch_in_dt: datetime, punch_out_dt: datetime) -> float:
    """(punch_out - punch_in) in seconds / 3600, rounded to 2 decimals half-up (R4)."""
    seconds = Decimal(str((punch_out_dt - punch_in_dt).total_seconds()))
    hours = seconds / Decimal("3600")
    rounded = hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(rounded)


def count_working_days(month_str: str, joined_on_str: str) -> int:
    """Count Monday-Friday working days in the month from max(month_start, joined_on) (R7)."""
    year, month = map(int, month_str.split("-"))
    num_days = calendar.monthrange(year, month)[1]
    month_start = date(year, month, 1)
    month_end = date(year, month, num_days)
    joined_date = date.fromisoformat(joined_on_str)
    start_date = max(month_start, joined_date)
    if start_date > month_end:
        return 0
    working_days = 0
    curr = start_date
    while curr <= month_end:
        if curr.weekday() < 5:  # Mon=0, Fri=4
            working_days += 1
        curr += timedelta(days=1)
    return working_days


def serialize_employee_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Format employee document for HTTP response."""
    res = dict(doc)
    res.pop("_id", None)
    res["created_at"] = dt_to_epoch(res.get("created_at"))
    return res


def serialize_attendance_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Format attendance document for HTTP response (no internal _id, epoch ms for instants)."""
    res = dict(doc)
    res.pop("_id", None)
    res.pop("id", None)
    res["punch_in"] = dt_to_epoch(res.get("punch_in"))
    res["punch_out"] = dt_to_epoch(res.get("punch_out"))
    if res.get("late_minutes") is None:
        res["late_minutes"] = 0
    if res.get("overtime_minutes") is None:
        res["overtime_minutes"] = 0
    if res.get("half_day") is None:
        res["half_day"] = False
    if res.get("work_hours") is not None:
        res["work_hours"] = round_half_up_2(res["work_hours"])

    history_list = []
    for h in res.get("history") or []:
        h_entry = dict(h)
        h_entry["at"] = dt_to_epoch(h_entry.get("at"))
        raw_changes = h_entry.get("changes") or {}
        cleaned_changes = {}
        for k, v in raw_changes.items():
            ch = dict(v)
            if k in ("punch_in", "punch_out"):
                ch["from"] = dt_to_epoch(ch.get("from")) if ch.get("from") is not None else None
                ch["to"] = dt_to_epoch(ch.get("to")) if ch.get("to") is not None else None
            elif k == "work_hours":
                ch["from"] = round_half_up_2(ch.get("from")) if ch.get("from") is not None else None
                ch["to"] = round_half_up_2(ch.get("to")) if ch.get("to") is not None else None
            cleaned_changes[k] = ch
        h_entry["changes"] = cleaned_changes
        history_list.append(h_entry)
    res["history"] = history_list
    return res


def clean_mongo_obj(obj: Any) -> Any:
    """Deep serialize MongoDB BSON objects to standard JSON primitives."""
    return json.loads(json_util.dumps(obj))


# --------------------------------------------------------------------------- #
# Pydantic Schemas matching openapi.yaml
# --------------------------------------------------------------------------- #
class Status(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LEAVE = "LEAVE"
    WFH = "WFH"
    ON_DUTY = "ON_DUTY"


class PresenceStatus(str, Enum):
    PRESENT = "PRESENT"
    WFH = "WFH"
    ON_DUTY = "ON_DUTY"


class EmployeeCreate(BaseModel):
    emp_code: str = Field(..., pattern=r"^EMP\d{4,6}$")
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = Field("09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field("18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str = Field(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

    @model_validator(mode="after")
    def validate_shift_difference(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PunchInRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(None, ge=100000000000, le=4102444800000)
    status: PresenceStatus = PresenceStatus.PRESENT


class PunchOutRequest(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(None, ge=100000000000, le=4102444800000)


class RegularizeRequest(BaseModel):
    status: Optional[Status] = None
    punch_in: Optional[int] = Field(None, ge=100000000000, le=4102444800000)
    punch_out: Optional[int] = Field(None, ge=100000000000, le=4102444800000)
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)


# --------------------------------------------------------------------------- #
# Aggregation Pipeline Builders
# --------------------------------------------------------------------------- #
def build_employee_monthly_pipeline(emp_code: str, month_str: str) -> List[Dict[str, Any]]:
    year, month = map(int, month_str.split("-"))
    num_days = calendar.monthrange(year, month)[1]
    start_date = f"{month_str}-01"
    end_date = f"{month_str}-{num_days:02d}"

    return [
        {"$match": {
            "emp_code": emp_code,
            "date": {"$gte": start_date, "$lte": end_date}
        }},
        {"$project": {
            "date": 1,
            "status": 1,
            "late_minutes": {"$ifNull": ["$late_minutes", 0]},
            "overtime_minutes": {"$ifNull": ["$overtime_minutes", 0]},
            "half_day": {"$ifNull": ["$half_day", False]},
            "day_of_week": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}
        }},
        {"$project": {
            "is_weekday": {"$in": ["$day_of_week", [2, 3, 4, 5, 6]]},
            "is_present_status": {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
            "status": 1,
            "half_day": 1,
            "late_minutes": 1,
            "overtime_minutes": 1,
        }},
        {"$group": {
            "_id": None,
            "present_days": {
                "$sum": {
                    "$cond": [
                        {"$and": ["$is_weekday", "$is_present_status"]},
                        {"$cond": ["$half_day", 0.5, 1.0]},
                        0.0
                    ]
                }
            },
            "leave_days": {
                "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
            },
            "late_count": {
                "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
            },
            "total_late_minutes": {"$sum": "$late_minutes"},
            "total_overtime_minutes": {"$sum": "$overtime_minutes"}
        }}
    ]


def build_department_summary_pipeline(month_str: str, department: Optional[str] = None) -> List[Dict[str, Any]]:
    year, month = map(int, month_str.split("-"))
    num_days = calendar.monthrange(year, month)[1]
    start_date = f"{month_str}-01"
    end_date = f"{month_str}-{num_days:02d}"

    match_stage: Dict[str, Any] = {"joined_on": {"$lte": end_date}}
    if department:
        match_stage["department"] = department

    pipeline: List[Dict[str, Any]] = [
        {"$match": match_stage},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"emp": "$emp_code"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$emp_code", "$$emp"]},
                    {"$gte": ["$date", start_date]},
                    {"$lte": ["$date", end_date]}
                ]}}}
            ],
            "as": "logs"
        }},
        {"$group": {
            "_id": "$department",
            "headcount": {"$sum": 1},
            "all_logs": {"$push": "$logs"}
        }},
        {"$project": {
            "department": "$_id",
            "headcount": 1,
            "flattened": {
                "$reduce": {
                    "input": "$all_logs",
                    "initialValue": [],
                    "in": {"$concatArrays": ["$$value", "$$this"]}
                }
            }
        }},
        {"$project": {
            "_id": 0,
            "department": 1,
            "headcount": 1,
            "present_days": {
                "$sum": {
                    "$map": {
                        "input": {
                            "$filter": {
                                "input": "$flattened",
                                "as": "log",
                                "cond": {
                                    "$and": [
                                        {"$in": ["$$log.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                        {"$in": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$$log.date"}}}, [2, 3, 4, 5, 6]]}
                                    ]
                                }
                            }
                        },
                        "as": "item",
                        "in": {"$cond": [{"$eq": ["$$item.half_day", True]}, 0.5, 1.0]}
                    }
                }
            },
            "work_hours_list": {
                "$map": {
                    "input": {
                        "$filter": {
                            "input": "$flattened",
                            "as": "log",
                            "cond": {
                                "$and": [
                                    {"$in": ["$$log.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$ne": ["$$log.work_hours", None]}
                                ]
                            }
                        }
                    },
                    "as": "item",
                    "in": "$$item.work_hours"
                }
            },
            "late_count": {
                "$size": {
                    "$filter": {
                        "input": "$flattened",
                        "as": "log",
                        "cond": {"$gt": [{"$ifNull": ["$$log.late_minutes", 0]}, 0]}
                    }
                }
            },
            "total_late_minutes": {
                "$sum": {
                    "$map": {
                        "input": "$flattened",
                        "as": "log",
                        "in": {"$ifNull": ["$$log.late_minutes", 0]}
                    }
                }
            },
            "leave_count": {
                "$size": {
                    "$filter": {
                        "input": "$flattened",
                        "as": "log",
                        "cond": {"$eq": ["$$log.status", "LEAVE"]}
                    }
                }
            },
            "on_duty_count": {
                "$size": {
                    "$filter": {
                        "input": "$flattened",
                        "as": "log",
                        "cond": {"$eq": ["$$log.status", "ON_DUTY"]}
                    }
                }
            }
        }},
        {"$project": {
            "department": 1,
            "headcount": 1,
            "present_days": 1,
            "avg_work_hours": {"$cond": [{"$gt": [{"$size": "$work_hours_list"}, 0]}, {"$avg": "$work_hours_list"}, None]},
            "late_count": 1,
            "total_late_minutes": 1,
            "leave_count": 1,
            "on_duty_count": 1
        }},
        {"$sort": {"department": 1}}
    ]
    return pipeline


def build_late_leaderboard_pipeline(month_str: str, limit: int = 10, department: Optional[str] = None) -> List[Dict[str, Any]]:
    year, month = map(int, month_str.split("-"))
    num_days = calendar.monthrange(year, month)[1]
    start_date = f"{month_str}-01"
    end_date = f"{month_str}-{num_days:02d}"

    pipeline: List[Dict[str, Any]] = [
        {"$match": {
            "date": {"$gte": start_date, "$lte": end_date},
            "late_minutes": {"$gt": 0}
        }},
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {"$sum": 1}
        }},
        {"$lookup": {
            "from": "employees",
            "localField": "_id",
            "foreignField": "emp_code",
            "as": "emp"
        }},
        {"$unwind": "$emp"}
    ]

    if department:
        pipeline.append({"$match": {"emp.department": department}})

    pipeline.extend([
        {"$project": {
            "_id": 0,
            "emp_code": "$_id",
            "name": "$emp.name",
            "department": "$emp.department",
            "total_late_minutes": 1,
            "late_count": 1
        }},
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {
                "rank": {"$rank": {}}
            }
        }},
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}}
    ])
    return pipeline


def build_department_trend_pipeline(department: str, from_str: str, to_str: str) -> List[Dict[str, Any]]:
    from_dt = datetime.fromisoformat(from_str).replace(tzinfo=timezone.utc)
    to_dt = datetime.fromisoformat(to_str).replace(tzinfo=timezone.utc)
    to_dt_exclusive = (datetime.fromisoformat(to_str) + timedelta(days=1)).replace(tzinfo=timezone.utc)

    pipeline: List[Dict[str, Any]] = [
        {"$lookup": {
            "from": "employees",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "as": "emp"
        }},
        {"$unwind": "$emp"},
        {"$match": {
            "emp.department": department,
            "date": {"$gte": from_str, "$lte": to_str}
        }},
        {"$group": {
            "_id": "$date",
            "present_count": {
                "$sum": {
                    "$cond": [
                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1.0]},
                        0.0
                    ]
                }
            },
            "late_count": {
                "$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}
            }
        }},
        {"$project": {
            "_id": 0,
            "date_str": "$_id",
            "date_obj": {"$dateFromString": {"dateString": "$_id"}},
            "present_count": 1,
            "late_count": 1
        }},
        {"$unionWith": {
            "pipeline": [
                {"$documents": [
                    {"date_obj": from_dt, "present_count": 0, "late_count": 0, "is_dummy": True},
                    {"date_obj": to_dt, "present_count": 0, "late_count": 0, "is_dummy": True}
                ]}
            ]
        }},
        {"$group": {
            "_id": "$date_obj",
            "present_count": {"$sum": {"$cond": [{"$eq": ["$is_dummy", True]}, 0, "$present_count"]}},
            "late_count": {"$sum": {"$cond": [{"$eq": ["$is_dummy", True]}, 0, "$late_count"]}}
        }},
        {"$project": {
            "date_obj": "$_id",
            "present_count": 1,
            "late_count": 1
        }},
        {"$densify": {
            "field": "date_obj",
            "range": {
                "step": 1,
                "unit": "day",
                "bounds": [from_dt, to_dt_exclusive]
            }
        }},
        {"$set": {
            "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date_obj"}},
            "present_count": {"$ifNull": ["$present_count", 0.0]},
            "late_count": {"$ifNull": ["$late_count", 0]}
        }},
        {"$set": {
            "is_working_day": {
                "$in": [{"$dayOfWeek": "$date_obj"}, [2, 3, 4, 5, 6]]
            }
        }},
        {"$lookup": {
            "from": "employees",
            "let": {"d": "$date"},
            "pipeline": [
                {"$match": {
                    "$expr": {
                        "$and": [
                            {"$eq": ["$department", department]},
                            {"$lte": ["$joined_on", "$$d"]}
                        ]
                    }
                }}
            ],
            "as": "matched_emps"
        }},
        {"$set": {
            "headcount": {"$size": "$matched_emps"}
        }},
        {"$set": {
            "attendance_rate": {
                "$cond": [
                    {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                    {"$divide": ["$present_count", "$headcount"]},
                    None
                ]
            }
        }},
        {"$setWindowFields": {
            "sortBy": {"date": 1},
            "output": {
                "moving_avg_7d": {
                    "$avg": "$attendance_rate",
                    "window": {
                        "documents": [-6, 0]
                    }
                }
            }
        }},
        {"$project": {
            "_id": 0,
            "date": 1,
            "is_working_day": 1,
            "headcount": 1,
            "present_count": 1,
            "late_count": 1,
            "attendance_rate": 1,
            "moving_avg_7d": 1
        }},
        {"$sort": {"date": 1}}
    ]
    return pipeline


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/health", tags=["system"])
def health():
    """Readiness probe. Returns 200 only if MongoDB answers a ping, otherwise 503."""
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unreachable: {str(e)}")


@app.post("/employees", status_code=201, tags=["employees"])
def create_employee(body: EmployeeCreate):
    """Create an employee. Enforces uniqueness on emp_code via database index."""
    doc = body.model_dump()
    created_now = datetime.now(timezone.utc)
    # Truncate to whole seconds per R1
    truncated_sec = (int(created_now.timestamp()))
    doc["created_at"] = datetime.fromtimestamp(truncated_sec, tz=timezone.utc)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="emp_code already exists")

    return serialize_employee_doc(doc)


@app.get("/employees", tags=["employees"])
def list_employees(
    department: Optional[str] = Query(None, description="Exact, case-sensitive match"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List employees, sorted by emp_code ascending."""
    q: Dict[str, Any] = {}
    if department:
        q["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    cursor = db.employees.find(q).sort("emp_code", 1).skip(skip).limit(page_size)
    items = [serialize_employee_doc(doc) for doc in cursor]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/attendance/punch-in", status_code=201, tags=["attendance"])
def punch_in(body: PunchInRequest):
    """
    Record an employee punch-in.
    Enforces R1 (IST date, overnight detection) and R2 (late calculation).
    Race-safe against concurrent duplicate requests via (emp_code, date) unique index.
    """
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    punch_ms = body.punched_at if body.punched_at is not None else now_epoch_ms()
    punch_dt = epoch_to_dt(punch_ms)

    att_date = compute_attendance_date(punch_dt, emp["shift_start"], emp["shift_end"])
    late_mins = compute_late_minutes(punch_dt, emp["shift_start"], att_date)

    doc = {
        "emp_code": body.emp_code,
        "date": att_date,
        "status": body.status.value,
        "punch_in": punch_dt,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_mins,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="already punched in for this date")

    return serialize_attendance_doc(doc)


@app.post("/attendance/punch-out", status_code=200, tags=["attendance"])
def punch_out(body: PunchOutRequest):
    """
    Record an employee punch-out.
    Closes the most recent record whose punch_in <= punched_at.
    Computes work_hours (R4), overtime_minutes (R3), and half_day (R5).
    """
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    punch_ms = body.punched_at if body.punched_at is not None else now_epoch_ms()
    punch_dt = epoch_to_dt(punch_ms)

    # Find most recent attendance record with a punch-in for this employee
    latest = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$ne": None}},
        sort=[("punch_in", -1)],
    )

    if not latest:
        raise HTTPException(status_code=404, detail="No punch-in found")

    punch_in_dt = latest["punch_in"]
    if punch_in_dt.tzinfo is None:
        punch_in_dt = punch_in_dt.replace(tzinfo=timezone.utc)

    # Validation: punched_at <= punch_in or > punch_in + 24 hours -> 422
    if punch_dt <= punch_in_dt:
        raise HTTPException(status_code=422, detail="punch_out must be strictly after punch_in")

    if (punch_dt - punch_in_dt).total_seconds() > 86400:
        raise HTTPException(status_code=422, detail="punch_out cannot be more than 24 hours after punch_in")

    if latest.get("punch_out") is not None:
        raise HTTPException(status_code=409, detail="Record is already punched out")

    work_hours = compute_work_hours(punch_in_dt, punch_dt)
    overtime_mins = compute_overtime(punch_dt, emp["shift_start"], emp["shift_end"], latest["date"])
    half_day = work_hours < 4.50

    # Atomic update ensuring single winner under concurrent requests
    updated = db.attendance_logs.find_one_and_update(
        {"emp_code": body.emp_code, "date": latest["date"], "punch_out": None},
        {"$set": {
            "punch_out": punch_dt,
            "work_hours": work_hours,
            "overtime_minutes": overtime_mins,
            "half_day": half_day,
        }},
        return_document=True,
    )

    if not updated:
        raise HTTPException(status_code=409, detail="Record is already punched out")

    return serialize_attendance_doc(updated)


@app.get("/attendance", tags=["attendance"])
def list_attendance(
    emp_code: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    date_to: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    status: Optional[Status] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List attendance records, sorted by date descending, then emp_code ascending."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from cannot be after date_to")

    q: Dict[str, Any] = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        date_q: Dict[str, Any] = {}
        if date_from:
            date_q["$gte"] = date_from
        if date_to:
            date_q["$lte"] = date_to
        q["date"] = date_q
    if status:
        q["status"] = status.value

    skip = (page - 1) * page_size
    total = db.attendance_logs.count_documents(q)
    cursor = db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(page_size)
    items = [serialize_attendance_doc(doc) for doc in cursor]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.patch("/attendance/{emp_code}/{date}", tags=["attendance"])
def regularize_attendance(
    emp_code: str = Path(...),
    date: str = Path(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    body: RegularizeRequest = ...,
):
    """
    Regularize (manually correct) one day's attendance record.
    Recomputes derived fields and appends an immutable audit entry to history.
    """
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    old_status = record.get("status")
    old_punch_in = record.get("punch_in")
    old_punch_out = record.get("punch_out")
    old_work_hours = record.get("work_hours")
    old_late_minutes = record.get("late_minutes", 0)
    old_overtime_minutes = record.get("overtime_minutes", 0)
    old_half_day = record.get("half_day", False)

    # Determine requested target status and punch times
    new_status = body.status.value if body.status is not None else old_status

    if new_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(status_code=422, detail="ABSENT/LEAVE records cannot have punch times")
        new_punch_in_dt = None
        new_punch_out_dt = None
        new_work_hours = None
        new_late_minutes = 0
        new_overtime_minutes = 0
        new_half_day = False
    else:
        # Presence status requires a punch_in
        if body.punch_in is not None:
            new_punch_in_dt = epoch_to_dt(body.punch_in)
        else:
            new_punch_in_dt = old_punch_in
            if new_punch_in_dt is None:
                raise HTTPException(status_code=422, detail="Presence status requires a punch_in")

        # Validate punch_in stays on the record's attendance date per R1
        inferred_date = compute_attendance_date(new_punch_in_dt, emp["shift_start"], emp["shift_end"])
        if inferred_date != date:
            raise HTTPException(status_code=422, detail="punch_in does not fall on the record attendance date per R1")

        new_late_minutes = compute_late_minutes(new_punch_in_dt, emp["shift_start"], date)

        if body.punch_out is not None:
            new_punch_out_dt = epoch_to_dt(body.punch_out)
        else:
            new_punch_out_dt = old_punch_out

        if new_punch_out_dt is not None:
            if new_punch_in_dt.tzinfo is None:
                new_punch_in_dt = new_punch_in_dt.replace(tzinfo=timezone.utc)
            if new_punch_out_dt.tzinfo is None:
                new_punch_out_dt = new_punch_out_dt.replace(tzinfo=timezone.utc)

            if new_punch_out_dt <= new_punch_in_dt:
                raise HTTPException(status_code=422, detail="punch_out must be strictly after punch_in")
            if (new_punch_out_dt - new_punch_in_dt).total_seconds() > 86400:
                raise HTTPException(status_code=422, detail="punch_out cannot be more than 24 hours after punch_in")

            new_work_hours = compute_work_hours(new_punch_in_dt, new_punch_out_dt)
            new_overtime_minutes = compute_overtime(new_punch_out_dt, emp["shift_start"], emp["shift_end"], date)
            new_half_day = new_work_hours < 4.50
        else:
            new_work_hours = None
            new_overtime_minutes = 0
            new_half_day = False

    # Collect actual changes
    changes: Dict[str, Any] = {}
    if new_status != old_status:
        changes["status"] = {"from": old_status, "to": new_status}
    if new_punch_in_dt != old_punch_in:
        changes["punch_in"] = {"from": old_punch_in, "to": new_punch_in_dt}
    if new_punch_out_dt != old_punch_out:
        changes["punch_out"] = {"from": old_punch_out, "to": new_punch_out_dt}
    if new_work_hours != old_work_hours:
        changes["work_hours"] = {"from": old_work_hours, "to": new_work_hours}
    if new_late_minutes != old_late_minutes:
        changes["late_minutes"] = {"from": old_late_minutes, "to": new_late_minutes}
    if new_overtime_minutes != old_overtime_minutes:
        changes["overtime_minutes"] = {"from": old_overtime_minutes, "to": new_overtime_minutes}
    if new_half_day != old_half_day:
        changes["half_day"] = {"from": old_half_day, "to": new_half_day}

    if not changes:
        raise HTTPException(status_code=422, detail="Regularization request changes nothing")

    audit_entry = {
        "at": datetime.now(timezone.utc),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    # Optimistic locking: ensure existing history array length matches to prevent lost updates
    current_history_len = len(record.get("history") or [])
    updated = db.attendance_logs.find_one_and_update(
        {
            "emp_code": emp_code,
            "date": date,
            f"history.{current_history_len}": {"$exists": False},
        },
        {
            "$set": {
                "status": new_status,
                "punch_in": new_punch_in_dt,
                "punch_out": new_punch_out_dt,
                "work_hours": new_work_hours,
                "late_minutes": new_late_minutes,
                "overtime_minutes": new_overtime_minutes,
                "half_day": new_half_day,
            },
            "$push": {"history": audit_entry},
        },
        return_document=True,
    )

    if not updated:
        raise HTTPException(status_code=409, detail="Concurrent regularization conflict")

    return serialize_attendance_doc(updated)


@app.get("/analytics/employees/{emp_code}/monthly", tags=["analytics"])
def get_employee_monthly_analytics(
    emp_code: str = Path(...),
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    """Monthly attendance summary for one employee backed by a MongoDB aggregation."""
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    working_days = count_working_days(month, emp["joined_on"])
    pipeline = build_employee_monthly_pipeline(emp_code, month)
    agg_res = list(db.attendance_logs.aggregate(pipeline))

    if agg_res:
        row = agg_res[0]
        present_days = round_half_up_2(row.get("present_days", 0.0)) or 0.0
        leave_days = int(row.get("leave_days", 0))
        late_count = int(row.get("late_count", 0))
        total_late_minutes = int(row.get("total_late_minutes", 0))
        total_overtime_minutes = int(row.get("total_overtime_minutes", 0))
    else:
        present_days = 0.0
        leave_days = 0
        late_count = 0
        total_late_minutes = 0
        total_overtime_minutes = 0

    if working_days > 0:
        attendance_pct = round_half_up_2((present_days / working_days) * 100)
    else:
        attendance_pct = None

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary", tags=["analytics"])
def get_department_summary_analytics(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = Query(None),
):
    """Department summary for a month via a cross-collection MongoDB aggregation."""
    pipeline = build_department_summary_pipeline(month, department)
    results = list(db.employees.aggregate(pipeline))

    items = []
    for row in results:
        items.append({
            "department": row["department"],
            "headcount": int(row["headcount"]),
            "present_days": round_half_up_2(row.get("present_days", 0.0)) or 0.0,
            "avg_work_hours": round_half_up_2(row.get("avg_work_hours")),
            "late_count": int(row.get("late_count", 0)),
            "total_late_minutes": int(row.get("total_late_minutes", 0)),
            "leave_count": int(row.get("leave_count", 0)),
            "on_duty_count": int(row.get("on_duty_count", 0)),
        })

    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late", tags=["analytics"])
def get_late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = Query(None),
):
    """Late-comers leaderboard for a month using MongoDB $setWindowFields standard competition ranking."""
    pipeline = build_late_leaderboard_pipeline(month, limit, department)
    results = list(db.attendance_logs.aggregate(pipeline))

    items = []
    for row in results:
        items.append({
            "rank": int(row["rank"]),
            "emp_code": row["emp_code"],
            "name": row["name"],
            "department": row["department"],
            "total_late_minutes": int(row["total_late_minutes"]),
            "late_count": int(row["late_count"]),
        })

    return {"month": month, "items": items}


@app.get("/analytics/departments/{department}/trend", tags=["analytics"])
def get_department_trend(
    department: str = Path(...),
    from_date: str = Query(..., alias="from", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    to_date: str = Query(..., alias="to", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
):
    """Daily attendance trend for a department with $densify gap filling and 7-day moving average."""
    # 404 for unknown department (no employees)
    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(status_code=404, detail="Department not found")

    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)

    if d_to < d_from:
        raise HTTPException(status_code=422, detail="to date cannot be earlier than from date")

    if (d_to - d_from).days > 92:
        raise HTTPException(status_code=422, detail="Date range cannot exceed 92 days")

    pipeline = build_department_trend_pipeline(department, from_date, to_date)
    results = list(db.attendance_logs.aggregate(pipeline))

    items = []
    for row in results:
        items.append({
            "date": row["date"],
            "is_working_day": bool(row["is_working_day"]),
            "headcount": int(row["headcount"]),
            "present_count": round_half_up_2(row.get("present_count", 0.0)) or 0.0,
            "late_count": int(row.get("late_count", 0)),
            "attendance_rate": round_half_up_4(row.get("attendance_rate")),
            "moving_avg_7d": round_half_up_4(row.get("moving_avg_7d")),
        })

    return {"department": department, "items": items}


@app.get("/admin/explain/{endpoint}", tags=["admin"])
def explain_endpoint(
    endpoint: str = Path(..., pattern=r"^(attendance_list|employee_monthly|department_summary|late_leaderboard|department_trend)$"),
    emp_code: Optional[str] = Query(None),
    month: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = Query(None),
    limit: Optional[int] = Query(10, ge=1, le=50),
    date_from: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    date_to: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    status: Optional[Status] = Query(None),
    from_date: Optional[str] = Query(None, alias="from", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    to_date: Optional[str] = Query(None, alias="to", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    page: Optional[int] = Query(1, ge=1),
    page_size: Optional[int] = Query(20, ge=1, le=100),
):
    """
    MongoDB explain output for an endpoint's main query or aggregation pipeline.
    Runs with executionStats verbosity.
    """
    if endpoint == "attendance_list":
        if date_from and date_to and date_from > date_to:
            raise HTTPException(status_code=422, detail="date_from cannot be after date_to")
        q: Dict[str, Any] = {}
        if emp_code:
            q["emp_code"] = emp_code
        if date_from or date_to:
            date_q: Dict[str, Any] = {}
            if date_from:
                date_q["$gte"] = date_from
            if date_to:
                date_q["$lte"] = date_to
            q["date"] = date_q
        if status:
            q["status"] = status.value

        p = page if page is not None else 1
        ps = page_size if page_size is not None else 20
        skip = (p - 1) * ps

        cmd = {
            "find": "attendance_logs",
            "filter": q,
            "sort": {"date": -1, "emp_code": 1},
            "skip": skip,
            "limit": ps,
        }
        raw_explain = db.command("explain", cmd, verbosity="executionStats")
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": clean_mongo_obj(raw_explain),
        }

    elif endpoint == "employee_monthly":
        if not emp_code:
            raise HTTPException(status_code=422, detail="emp_code is required for employee_monthly explain")
        if not month:
            raise HTTPException(status_code=422, detail="month is required for employee_monthly explain")
        pipeline = build_employee_monthly_pipeline(emp_code, month)
        cmd = {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}
        raw_explain = db.command("explain", cmd, verbosity="executionStats")
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": clean_mongo_obj(raw_explain),
        }

    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(status_code=422, detail="month is required for department_summary explain")
        pipeline = build_department_summary_pipeline(month, department)
        cmd = {"aggregate": "employees", "pipeline": pipeline, "cursor": {}}
        raw_explain = db.command("explain", cmd, verbosity="executionStats")
        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": clean_mongo_obj(raw_explain),
        }

    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(status_code=422, detail="month is required for late_leaderboard explain")
        lim = limit if limit is not None else 10
        pipeline = build_late_leaderboard_pipeline(month, lim, department)
        cmd = {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}
        raw_explain = db.command("explain", cmd, verbosity="executionStats")
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": clean_mongo_obj(raw_explain),
        }

    elif endpoint == "department_trend":
        if not department:
            raise HTTPException(status_code=422, detail="department is required for department_trend explain")
        if not from_date or not to_date:
            raise HTTPException(status_code=422, detail="from and to dates are required for department_trend explain")
        pipeline = build_department_trend_pipeline(department, from_date, to_date)
        cmd = {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}
        raw_explain = db.command("explain", cmd, verbosity="executionStats")
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": clean_mongo_obj(raw_explain),
        }
