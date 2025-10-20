from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Optional
from datetime import datetime
import logging
import sys
import os
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from . import db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('app.log', mode='a')
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Personal Tracking")

# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = datetime.utcnow()
    client_ip = request.client.host if request.client else 'unknown'
    logger.info(f"Request: {request.method} {request.url.path} from {client_ip}")
    
    try:
        response = await call_next(request)
        process_time = (datetime.utcnow() - start_time).total_seconds()
        logger.info(f"Response: {response.status_code} for {request.method} {request.url.path} ({process_time:.3f}s)")
        return response
    except Exception as e:
        process_time = (datetime.utcnow() - start_time).total_seconds()
        logger.error(f"Request failed: {request.method} {request.url.path} after {process_time:.3f}s - {str(e)}")
        raise

# Log startup
logger.info(f"FastAPI app starting - Python {sys.version}")
logger.info(f"DATA_DIR: {os.environ.get('DATA_DIR', 'default')}")
logger.info(f"RELOAD: {os.environ.get('RELOAD', '0')}")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

@app.get("/health")
async def health_check():
    """Health check endpoint for Docker and monitoring"""
    try:
        # Test database connectivity
        con = db.connect()
        result = con.execute("SELECT COUNT(*) FROM household_member").fetchone()
        member_count = result[0] if result else 0
        
        health_info = {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "database": "connected",
            "members_count": member_count,
            "version": "1.0.0"
        }
        logger.info(f"Health check passed: {health_info}")
        return JSONResponse(content=health_info)
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "timestamp": datetime.utcnow().isoformat(),
                "error": str(e)
            }
        )


try:
    _EASTERN = ZoneInfo("America/New_York") if ZoneInfo else None
except Exception:  # Zone not found
    _EASTERN = None


def _fmt_ts(value: datetime, fmt: Optional[str] = None):
    """Format a datetime value into Eastern Time (America/New_York).

    - If `fmt` is provided, use it as the strftime format string. Otherwise use
      the default "%Y-%m-%d %I:%M:%S %p".
    - If zoneinfo/ZoneInfo is unavailable or the Eastern zone is not found,
      fall back to showing the naive/UTC time (with a UTC marker when appropriate).
    """
    if not value:
        return ""
    if fmt is None:
        fmt = "%Y-%m-%d %I:%M:%S %p"

    # Assume stored timestamps are UTC; DuckDB typically returns naive timestamps
    # which we treat as UTC for conversion purposes.
    try:
        if _EASTERN is None:
            # No Eastern zone available; show as UTC (append marker if not present)
            try:
                # If value is naive, treat as UTC for display
                if value.tzinfo is None and ZoneInfo:
                    value = value.replace(tzinfo=ZoneInfo("UTC"))
            except Exception:
                pass
            # Append UTC marker when not explicitly included in format
            formatted = value.strftime(fmt)
            if "%Z" not in fmt and "UTC" not in formatted:
                return f"{formatted} UTC"
            return formatted

        # Ensure value is timezone-aware in UTC so astimezone works predictably
        if value.tzinfo is None and ZoneInfo:
            value = value.replace(tzinfo=ZoneInfo("UTC"))
        local = value.astimezone(_EASTERN)
        return local.strftime(fmt)
    except Exception:
        # On any unexpected error, fall back to naive formatting
        try:
            return value.strftime(fmt)
        except Exception:
            return str(value)


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
    logger.info(f"Incentive report requested for member {member_id}")
    try:
        rows, total, prior_balance, prior_count = db.incentive_report(member_id)
        members = db.list_household_members()
        
        logger.debug(f"Incentive report: {len(rows)} rows, total={total}, prior={prior_balance}")
        
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
    except Exception as e:
        logger.error(f"Error generating incentive report for member {member_id}: {str(e)}")
        raise


@app.post("/activity/delete")
async def activity_delete(request: Request, activity_id: int = Form(...), member_id: Optional[int] = Form(None)):
    """Delete activity by id and redirect back to the appropriate report page.

    Expects form fields: activity_id, member_id (optional, for redirect back to member report)
    """
    try:
        deleted = db.delete_activity(activity_id)
        logger.info(f"Deleted activity {activity_id}, rows_deleted={deleted}")
    except Exception as e:
        logger.error(f"Failed to delete activity {activity_id}: {str(e)}")
        # Continue to redirect back with error query param
        if member_id:
            return RedirectResponse(f"/report/incentives/{member_id}?deleted=0", status_code=303)
        return RedirectResponse(f"/?deleted=0", status_code=303)

    if member_id:
        return RedirectResponse(f"/report/incentives/{member_id}?deleted=1", status_code=303)
    return RedirectResponse("/?deleted=1", status_code=303)

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
