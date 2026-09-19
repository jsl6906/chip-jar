import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, settings

_handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
if settings.LOG_FILE:
    _handlers.append(logging.FileHandler(settings.LOG_FILE, mode="a"))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=_handlers,
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Starting personal-tracking (Python %s)", sys.version.split()[0])
    db.open_pool()
    yield
    db.close_pool()


app = FastAPI(title="Personal Tracking", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = datetime.now(timezone.utc)
    client_ip = request.client.host if request.client else "unknown"
    logger.info("Request: %s %s from %s", request.method, request.url.path, client_ip)
    try:
        response = await call_next(request)
    except Exception as e:
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        logger.error("Request failed: %s %s after %.3fs - %s", request.method, request.url.path, elapsed, e)
        raise
    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    logger.info("Response: %s for %s %s (%.3fs)", response.status_code, request.method, request.url.path, elapsed)
    return response


# --- template filters --------------------------------------------------------

def _fmt_ts(value: Optional[datetime], fmt: Optional[str] = None) -> str:
    """Format a UTC datetime in the configured local timezone."""
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=settings.UTC)
    return value.astimezone(settings.LOCAL_TZ).strftime(fmt or "%Y-%m-%d %I:%M:%S %p")


def _to_plain(value) -> Optional[Decimal]:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _fmt_num(value) -> str:
    """Render a numeric without trailing zeros (10.00 -> 10, 2.50 -> 2.5)."""
    if value is None:
        return ""
    dec = _to_plain(value)
    if dec is None:
        return str(value)
    return str(int(dec)) if dec == dec.to_integral_value() else str(dec.normalize())


def _fmt_signed(value) -> str:
    """Whole-number display with explicit + sign (chips)."""
    if value is None:
        return ""
    dec = _to_plain(value)
    if dec is None:
        return str(value)
    shown = _fmt_num(dec)
    return f"+{shown}" if dec > 0 else shown


def _fmt_money(value, signed: bool = False) -> str:
    if value is None:
        return ""
    try:
        dec = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return str(value)
    prefix = "+" if signed and dec > 0 else ""
    return f"{prefix}{dec:.2f}"


templates.env.filters["fmt_ts"] = _fmt_ts
templates.env.filters["fmt_num"] = _fmt_num
templates.env.filters["fmt_signed"] = _fmt_signed
templates.env.filters["fmt_money"] = _fmt_money


# --- helpers -----------------------------------------------------------------

def _parse_amount(raw: Optional[str], category: str) -> Optional[Decimal]:
    """Parse form amount; allowance keeps cents, other categories round to whole numbers."""
    if raw is None or raw.strip() == "":
        return None
    try:
        dec = Decimal(raw.strip())
    except (InvalidOperation, ValueError):
        logger.warning("Unparseable amount %r for category=%s", raw, category)
        return None
    if category == "allowance":
        return dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return dec.to_integral_value(rounding=ROUND_HALF_UP)


def _parse_when(raw: Optional[str]) -> Optional[datetime]:
    """Interpret an HTML datetime-local value as local time; return aware UTC."""
    if not raw:
        return None
    try:
        local = datetime.strptime(raw, "%Y-%m-%dT%H:%M").replace(tzinfo=settings.LOCAL_TZ)
    except ValueError:
        return None
    return local.astimezone(timezone.utc)


def _report_redirect(report: str, member_id: Optional[int], ok: bool) -> RedirectResponse:
    flag = "1" if ok else "0"
    if member_id:
        path = "/report/allowance" if report == "allowance" else "/report/incentives"
        return RedirectResponse(f"{path}/{member_id}?deleted={flag}", status_code=303)
    return RedirectResponse(f"/?deleted={flag}", status_code=303)


# --- routes ------------------------------------------------------------------
# Routes are plain `def` so FastAPI runs the blocking DB calls in its threadpool.

@app.get("/health")
def health_check():
    try:
        member_count = db.ping()
    except Exception as e:
        logger.error("Health check failed: %s", e)
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "timestamp": datetime.now(timezone.utc).isoformat(), "error": str(e)},
        )
    return JSONResponse(
        content={
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "database": "connected",
            "members_count": member_count,
            "version": "0.2.0",
        }
    )


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "members": db.list_household_members(),
            "incentives": db.list_incentives(),
            "medications": db.list_medications(),
            "added": request.query_params.get("added") == "1",
            "incentive_totals": db.category_totals_for_members("incentive"),
            "allowance_totals": db.category_totals_for_members("allowance"),
        },
    )


@app.post("/activity")
def add_activity(
    household_member_id: int = Form(...),
    category: str = Form(...),
    activity_type: str = Form(...),
    amount: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    when: Optional[str] = Form(None),
):
    parsed_amount = _parse_amount(amount, category)
    event_dt = _parse_when(when)

    db.add_activity(household_member_id, category, activity_type, parsed_amount, notes, event_dt)

    pts_val: Optional[Decimal] = parsed_amount
    if pts_val is None and category == "incentive":
        pts_val = next((Decimal(pts) for name, _, pts in db.list_incentives() if name == activity_type), None)

    params = ["added=1", f"member_id={household_member_id}", f"cat={category}", f"act={activity_type}"]
    if pts_val is not None:
        params.append(f"pts={pts_val}")
    if event_dt:
        params.append("ts=" + _fmt_ts(event_dt, "%Y-%m-%d %I:%M %p").replace(" ", "+"))
    return RedirectResponse("/?" + "&".join(params), status_code=303)


@app.get("/report/incentives/{member_id}", response_class=HTMLResponse)
def incentive_report(request: Request, member_id: int):
    rows, total, prior_balance, prior_count = db.incentive_report(member_id)
    return templates.TemplateResponse(
        request=request,
        name="incentive_report.html",
        context={
            "rows": rows,
            "total": total,
            "prior_balance": prior_balance,
            "prior_count": prior_count,
            "member_id": member_id,
            "members": db.list_household_members(),
            "incentive_desc": {name: desc for name, desc, _ in db.list_incentives()},
        },
    )


@app.get("/report/allowance/{member_id}", response_class=HTMLResponse)
def allowance_report(request: Request, member_id: int):
    rows, total, prior_balance, prior_count = db.allowance_report(member_id)
    return templates.TemplateResponse(
        request=request,
        name="allowance_report.html",
        context={
            "rows": rows,
            "total": total,
            "prior_balance": prior_balance,
            "prior_count": prior_count,
            "member_id": member_id,
            "members": db.list_household_members(),
        },
    )


@app.post("/activity/delete")
def activity_delete(
    activity_id: int = Form(...),
    member_id: Optional[int] = Form(None),
    report: str = Form("incentive"),
):
    try:
        deleted = db.delete_activity(activity_id)
        logger.info("Deleted activity %s, rows_deleted=%s", activity_id, deleted)
    except Exception as e:
        logger.error("Failed to delete activity %s: %s", activity_id, e)
        return _report_redirect(report, member_id, ok=False)
    return _report_redirect(report, member_id, ok=True)


@app.get("/report/medications/{member_id}", response_class=HTMLResponse)
def medication_report(request: Request, member_id: int, period: str = "week"):
    return templates.TemplateResponse(
        request=request,
        name="medication_report.html",
        context={
            "rows": db.medication_summary(member_id, period=period),
            "member_id": member_id,
            "period": period,
            "members": db.list_household_members(),
        },
    )


@app.get("/report/other/{member_id}", response_class=HTMLResponse)
def other_report(request: Request, member_id: int, limit: int = 100):
    rows, count, total = db.other_activity_report(member_id, limit=limit)
    return templates.TemplateResponse(
        request=request,
        name="other_report.html",
        context={
            "rows": rows,
            "count": count,
            "total": total,
            "member_id": member_id,
            "limit": limit,
            "members": db.list_household_members(),
        },
    )


def run(host: str = "0.0.0.0", port: int = 8000, reload: bool = False):
    import uvicorn

    uvicorn.run("app.web:app", host=host, port=port, reload=reload)
