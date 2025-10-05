from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Optional
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from . import db

app = FastAPI(title="Personal Tracking")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


try:
    _EASTERN = ZoneInfo("America/New_York") if ZoneInfo else None
except Exception:  # Zone not found
    _EASTERN = None


def _fmt_ts(value: datetime):
    if not value:
        return ""
    # Assume stored timestamps are UTC; DuckDB returns naive timestamps treated as UTC.
    if _EASTERN is None:
        # Fallback: treat as UTC and just show with 'UTC'
        return value.strftime("%Y-%m-%d %H:%M:%S UTC")
    if value.tzinfo is None and ZoneInfo:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    local = value.astimezone(_EASTERN)
    return local.strftime("%Y-%m-%d %I:%M:%S %p")


def _fmt_signed(value):
    if value is None:
        return ""
    return f"+{value}" if isinstance(value, (int, float)) and value > 0 else str(value)

templates.env.filters["fmt_ts"] = _fmt_ts
templates.env.filters["fmt_signed"] = _fmt_signed

db.init_db()

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    members = db.list_household_members()
    incentives = db.list_incentives()
    medications = db.list_medications()
    added = request.query_params.get("added") == "1"
    incentive_totals = {mid: total for (mid, total) in db.incentive_totals_for_members()}
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "members": members,
            "incentives": incentives,
            "medications": medications,
            "added": added,
            "incentive_totals": incentive_totals,
        },
    )

@app.post("/activity")
async def add_activity(
    household_member_id: int = Form(...),
    category: str = Form(...),
    activity_type: str = Form(...),
    amount: Optional[str] = Form(None),  # accept raw string so blank doesn't cause int parse error
    notes: Optional[str] = Form(None),
    when: Optional[str] = Form(None),  # ISO local datetime from datetime-local input
):
    parsed_amount: Optional[int]
    if amount is None or amount == "":
        parsed_amount = None
    else:
        try:
            parsed_amount = int(amount)
        except ValueError:
            parsed_amount = None

    # Validate incentive negative does not exceed available total.
    if category == "incentive":
        # Determine effective amount (may be None -> default lookup later)
        effective_amount = parsed_amount
        if effective_amount is None:
            # Try to derive default from lookup before validation
            for name, desc, pts in db.list_incentives():
                if name == activity_type:
                    effective_amount = pts
                    break
        if effective_amount is not None and effective_amount < 0:
            current_total = db.incentive_total(household_member_id)
            if current_total + effective_amount < 0:
                # Redirect back with error indicator
                return RedirectResponse("/?added=0&error=insufficient", status_code=303)

    # Parse custom timestamp if provided; treat input as local time in Eastern if timezone not supplied.
    event_dt: Optional[datetime] = None
    if when:
        try:
            # HTML datetime-local supplies 'YYYY-MM-DDTHH:MM'
            event_dt = datetime.strptime(when, "%Y-%m-%dT%H:%M")
            # Assume user's local is Eastern if we have zone info; convert to UTC naive for storage consistency (db stores naive UTC)
            if _EASTERN is not None and ZoneInfo:
                local_dt = event_dt.replace(tzinfo=_EASTERN)
                event_dt = local_dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        except ValueError:
            event_dt = None

    db.add_activity(household_member_id, category, activity_type, parsed_amount, notes, event_dt)

    # Build success message params
    pts_val: Optional[int] = None
    if parsed_amount is not None:
        pts_val = parsed_amount
    elif category == "incentive":
        for name, desc, pts in db.list_incentives():
            if name == activity_type:
                pts_val = pts
                break
    # Format timestamp for display (local Eastern if possible)
    ts_disp = None
    if event_dt:
        if _EASTERN is not None and ZoneInfo:
            ts_disp = event_dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(_EASTERN).strftime("%Y-%m-%d %I:%M %p")
        else:
            ts_disp = event_dt.strftime("%Y-%m-%d %H:%M")
    params = ["added=1", f"cat={category}", f"act={activity_type}"]
    if pts_val is not None:
        params.append(f"pts={pts_val}")
    if ts_disp:
        # Replace spaces with + for URL friendliness
        params.append("ts=" + ts_disp.replace(" ", "+"))
    return RedirectResponse("/" + "?" + "&".join(params), status_code=303)

@app.get("/report/incentives/{member_id}", response_class=HTMLResponse)
async def incentive_report(request: Request, member_id: int):
    rows, total, prior_balance, prior_count = db.incentive_report(member_id)
    members = db.list_household_members()
    incentive_desc = {name: desc for (name, desc, pts) in db.list_incentives()}
    return templates.TemplateResponse(
        "incentive_report.html",
        {
            "request": request,
            "rows": rows,
            "total": total,
            "prior_balance": prior_balance,
            "prior_count": prior_count,
            "member_id": member_id,
            "members": members,
            "incentive_desc": incentive_desc,
        },
    )

@app.get("/report/medications/{member_id}", response_class=HTMLResponse)
async def medication_report(request: Request, member_id: int, period: str = "week"):
    rows = db.medication_summary(member_id, period=period)
    members = db.list_household_members()
    return templates.TemplateResponse(
        "medication_report.html",
        {
            "request": request,
            "rows": rows,
            "member_id": member_id,
            "period": period,
            "members": members,
        },
    )

@app.get("/report/other/{member_id}", response_class=HTMLResponse)
async def other_report(request: Request, member_id: int, limit: int = 100):
    rows, count, total = db.other_activity_report(member_id, limit=limit)
    members = db.list_household_members()
    # Combined description map for incentives & medications (may be unused for 'other')
    return templates.TemplateResponse(
        "other_report.html",
        {
            "request": request,
            "rows": rows,
            "count": count,
            "total": total,
            "member_id": member_id,
            "limit": limit,
            "members": members,
        },
    )


def run(host: str = "0.0.0.0", port: int = 8000, reload: bool = False):
    import uvicorn
    uvicorn.run("app.web:app", host=host, port=port, reload=reload)
