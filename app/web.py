import logging
import secrets
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, jellyfin, places, settings

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
    logger.info("Starting chip-jar (Python %s)", sys.version.split()[0])
    db.open_pool()
    yield
    db.close_pool()


app = FastAPI(title="ChipJar", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
# Appended as ?v= to CSS/JS URLs so browsers pick up new assets after a deploy.
templates.env.globals["asset_v"] = max(int(p.stat().st_mtime) for p in Path("app/static").iterdir() if p.is_file())


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


def _fmt_rel(value: Optional[datetime]) -> str:
    """Coarse relative time ("just now", "3h ago", "Tue", "Sep 4")."""
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=settings.UTC)
    now = datetime.now(timezone.utc)
    secs = (now - value).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    local = value.astimezone(settings.LOCAL_TZ)
    if secs < 7 * 86400:
        return local.strftime("%a")
    return local.strftime("%b %-d" if sys.platform != "win32" else "%b %#d")


def _spark_points(series, width: int = 120, height: int = 36, pad: int = 2) -> str:
    """Map a numeric series onto SVG polyline points."""
    vals = [float(v) for v in series] if series else []
    if len(vals) < 2:
        vals = (vals or [0.0]) * 2
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    step = (width - 2 * pad) / (len(vals) - 1)
    pts = []
    for i, v in enumerate(vals):
        x = pad + i * step
        y = height - pad - ((v - lo) / span) * (height - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


_TIER_THRESHOLDS = (200, 100, 50, 25, 15, 10, 5)


def _amt_class(value) -> str:
    """CSS classes colouring an amount by sign and magnitude tier (1-8)."""
    dec = _to_plain(value) if value is not None else None
    if not dec:
        return "amt-cell"
    mag = abs(dec)
    tier = 8 - next((i for i, t in enumerate(_TIER_THRESHOLDS) if mag >= t), 7)
    return f"amt-cell amt-{'pos' if dec > 0 else 'neg'}-{tier}"


def _fmt_amount(value, category: str, signed: bool = True) -> str:
    """Category-aware amount: allowance as -$5.00 / +$5.00, everything else as whole numbers."""
    if value is None:
        return ""
    if category == "allowance":
        dec = _to_plain(value)
        if dec is None:
            return str(value)
        sign = "-" if dec < 0 else ("+" if signed and dec > 0 else "")
        return f"{sign}${_fmt_money(abs(dec))}"
    return _fmt_signed(value) if signed else _fmt_num(value)


templates.env.filters["fmt_ts"] = _fmt_ts
templates.env.filters["fmt_num"] = _fmt_num
templates.env.filters["fmt_signed"] = _fmt_signed
templates.env.filters["fmt_money"] = _fmt_money
templates.env.filters["fmt_rel"] = _fmt_rel
templates.env.filters["spark_points"] = _spark_points
templates.env.filters["amt_class"] = _amt_class
templates.env.filters["fmt_amount"] = _fmt_amount


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


LEDGER_TABS = {
    "incentive": ("incentives", "Chips"),
    "allowance": ("allowance", "Allowance"),
    "medication": ("medications", "Meds"),
    "other": ("other", "Other"),
}


def _ledger_url(tab: str, member_id: int) -> str:
    return f"/report/{LEDGER_TABS.get(tab, LEDGER_TABS['incentive'])[0]}/{member_id}"


def _report_redirect(report: str, member_id: Optional[int], ok: bool) -> RedirectResponse:
    flag = "1" if ok else "0"
    if member_id:
        return RedirectResponse(f"{_ledger_url(report, member_id)}?deleted={flag}", status_code=303)
    return RedirectResponse(f"/?deleted={flag}", status_code=303)


def _is_partial(request: Request) -> bool:
    """HTMX swap request (history-restore misses still need the full page)."""
    return request.headers.get("HX-Request") == "true" and request.headers.get("HX-History-Restore-Request") != "true"


# Recently deleted rows kept in memory so a toast can offer Undo (single-process app).
_UNDO_TTL = 120
_undo_lock = threading.Lock()
_undo_stash: dict[str, tuple[float, dict]] = {}


def _stash_deleted(row: dict) -> str:
    token = secrets.token_urlsafe(16)
    now = time.monotonic()
    with _undo_lock:
        for key in [k for k, (exp, _) in _undo_stash.items() if exp < now]:
            del _undo_stash[key]
        _undo_stash[token] = (now + _UNDO_TTL, row)
    return token


def _pop_deleted(token: str) -> Optional[dict]:
    with _undo_lock:
        item = _undo_stash.pop(token, None)
    if item is None or item[0] < time.monotonic():
        return None
    return item[1]


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
            "version": "0.5.0",
        }
    )


CATEGORY_LABELS = {"incentive": "Chips", "allowance": "Allowance", "medication": "Medication", "other": "Other"}


def _added_toast(category: str, activity_type: str, amount: Optional[Decimal], event_dt: Optional[datetime]) -> dict:
    if category == "incentive" and amount is not None:
        detail = f"{_fmt_signed(amount)} chip{'s' if abs(amount) != 1 else ''}"
    else:
        detail = _fmt_amount(amount, category)
    sub = f"Logged for {_fmt_ts(event_dt, '%b %d, %I:%M %p')}" if event_dt else ""
    return {"kind": "success", "title": f"{CATEGORY_LABELS.get(category, category)}: {activity_type}", "detail": detail, "sub": sub}


def _quick_items(member_id: int, category: str) -> list[dict]:
    """One-tap buttons for a member/category: their most-used items, gains before deductions."""
    if category not in CATEGORY_LABELS:
        return []
    items = [{"activity_type": t, "amount": a, "uses": n} for t, a, n in db.frequent_items(member_id, category)]
    if category == "medication" and not items:
        items = [{"activity_type": name, "amount": Decimal(dose), "uses": 0} for name, _, dose in db.list_medications()]
    if category in ("incentive", "allowance"):
        items.sort(key=lambda i: (i["amount"] or 0) < 0)
    for i in items:
        i["vals"] = {
            "category": category,
            "activity_type": i["activity_type"],
            "amount": "" if i["amount"] is None else str(i["amount"]),
        }
    return items


def _member_card_response(request: Request, member_id: int, toast: dict):
    board = db.dashboard()
    idx, member = next(((i, m) for i, m in enumerate(board) if m["id"] == member_id), (0, None))
    return templates.TemplateResponse(
        request=request,
        name="partials/activity_added.html",
        context={"m": member, "idx": idx, "toast": toast},
    )


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    q = request.query_params
    toast = None
    if q.get("added") == "1" and q.get("act"):
        toast = _added_toast(q.get("cat", ""), q.get("act", ""), _to_plain(q.get("pts")) if q.get("pts") else None, None)
    elif q.get("deleted") == "1":
        toast = {"kind": "success", "title": "Activity deleted", "detail": "", "sub": ""}
    elif q.get("deleted") == "0":
        toast = {"kind": "error", "title": "Couldn't delete that activity", "detail": "", "sub": ""}

    dashboard = db.dashboard()
    ids = [m["id"] for m in dashboard]
    selected_member = int(q["member_id"]) if q.get("member_id", "").isdigit() and int(q["member_id"]) in ids else (ids[0] if ids else None)
    selected_cat = q.get("cat") if q.get("cat") in CATEGORY_LABELS else "incentive"
    incentives = db.list_incentives()
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "dashboard": dashboard,
            "chip_presets": sorted({pts for _, _, pts in incentives}, key=lambda p: (p < 0, abs(p))),
            "medications": db.list_medications(),
            "selected_member": selected_member,
            "selected_cat": selected_cat,
            "quick_items": _quick_items(selected_member, selected_cat) if selected_member else [],
            "toast": toast,
        },
    )


@app.get("/quick-items", response_class=HTMLResponse)
def quick_items(request: Request, household_member_id: int, category: str = "incentive"):
    return templates.TemplateResponse(
        request=request,
        name="partials/quick_items_list.html",
        context={"quick_items": _quick_items(household_member_id, category), "selected_cat": category},
    )


@app.post("/activity")
def add_activity(
    request: Request,
    household_member_id: int = Form(...),
    category: str = Form(...),
    activity_type: str = Form(...),
    amount: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    when: Optional[str] = Form(None),
):
    parsed_amount = _parse_amount(amount, category)
    event_dt = _parse_when(when)
    activity_type = activity_type.strip()
    notes = notes.strip() or None if notes else None

    new_id = db.add_activity(household_member_id, category, activity_type, parsed_amount, notes, event_dt)

    pts_val: Optional[Decimal] = parsed_amount
    if pts_val is None and category == "incentive":
        pts_val = next((Decimal(pts) for name, _, pts in db.list_incentives() if name == activity_type), None)

    if request.headers.get("HX-Request") == "true":
        toast = _added_toast(category, activity_type, pts_val, event_dt)
        toast["ttl"] = 8000
        toast["action"] = {
            "label": "Undo",
            "url": "/activity/undo-add",
            "vals": {"activity_id": new_id},
            "target": "this",
            "swap": "none",
        }
        return _member_card_response(request, household_member_id, toast)

    params = ["added=1", f"member_id={household_member_id}", f"cat={category}", f"act={activity_type}"]
    if pts_val is not None:
        params.append(f"pts={pts_val}")
    return RedirectResponse("/?" + "&".join(params), status_code=303)


def _day_label(d: date, today: date) -> str:
    if d == today:
        return "Today"
    if d == today - timedelta(days=1):
        return "Yesterday"
    label = f"{d:%a, %b} {d.day}"
    return label if d.year == today.year else f"{label}, {d.year}"


def _group_by_day(entries: list[dict]) -> list[dict]:
    today = datetime.now(settings.LOCAL_TZ).date()
    days: list[dict] = []
    for e in entries:
        d = e["local_ts"].date()
        if not days or days[-1]["date"] != d:
            days.append({"date": d, "label": _day_label(d, today), "net": Decimal(0), "entries": []})
        days[-1]["entries"].append(e)
        days[-1]["net"] += e["amount"] or Decimal(0)
    return days


def _med_grid(member_id: int, week: int) -> dict:
    """7-day dose grid ending `week` weeks before today; rows = known meds plus anything else logged."""
    today = datetime.now(settings.LOCAL_TZ).date()
    end = today - timedelta(days=7 * week)
    start = end - timedelta(days=6)
    days = [start + timedelta(days=i) for i in range(7)]
    counts = db.medication_grid(member_id, start)
    names = [name for name, _, _ in db.list_medications()]
    names += sorted({med for med, _ in counts} - set(names))
    rows = [{"name": n, "cells": [counts.get((n, d), 0) for d in days]} for n in names]
    for r in rows:
        r["total"] = sum(r["cells"])
    return {
        "days": days,
        "today": today,
        "rows": rows,
        "week": week,
        "label": f"{start:%b} {start.day} – {end:%b} {end.day}",
    }


def _render_ledger(
    request: Request,
    member_id: int,
    tab: str,
    limit: int = 50,
    period: str = "week",
    toast: Optional[dict] = None,
    highlight: Optional[int] = None,
    week: int = 0,
):
    limit = max(10, min(limit, 1000))
    roster = db.list_household_members()
    members = [{"id": mid, "name": name, "idx": i} for i, (mid, name) in enumerate(roster)]
    member = next((m for m in members if m["id"] == member_id), None)
    if member is None:
        raise HTTPException(status_code=404, detail="Unknown household member")

    ctx: dict = {"prior_balance": None, "prior_count": 0, "med_summary": None, "med_grid": None}
    if tab in ("incentive", "allowance"):
        report = db.incentive_report if tab == "incentive" else db.allowance_report
        rows, _total, prior_balance, prior_count = report(member_id, limit=limit)
        entries = [
            {"id": r[0], "ts": r[1], "local_ts": r[2], "activity_type": r[3], "amount": r[4], "notes": r[5], "running": r[6]}
            for r in rows
        ]
        ctx.update(prior_balance=prior_balance, prior_count=prior_count, count=len(rows) + prior_count)
        has_more = prior_count > 0
    else:
        rows, count, _total = db.activity_list(member_id, tab, limit=limit)
        entries = [
            {"id": r[0], "ts": r[1], "local_ts": r[1].astimezone(settings.LOCAL_TZ), "activity_type": r[2], "amount": r[3], "notes": r[4], "running": None}
            for r in rows
        ]
        ctx["count"] = count
        has_more = count > len(rows)
        if tab == "medication":
            period = period if period in ("week", "month") else "week"
            ctx["med_summary"] = db.medication_summary(member_id, period=period)
            ctx["med_grid"] = _med_grid(member_id, max(0, week))

    ctx.update(
        member=member,
        members=members,
        tab=tab,
        tabs=LEDGER_TABS,
        tab_path=LEDGER_TABS[tab][0],
        limit=limit,
        period=period,
        has_more=has_more,
        days=_group_by_day(entries),
        totals={
            "incentive": db.category_totals_for_members("incentive").get(member_id, Decimal(0)),
            "allowance": db.category_totals_for_members("allowance").get(member_id, Decimal(0)),
        },
        toast=toast,
        highlight=highlight,
    )
    name = "partials/ledger_response.html" if _is_partial(request) else "ledger.html"
    return templates.TemplateResponse(request=request, name=name, context=ctx)


def _query_toast(request: Request) -> Optional[dict]:
    flag = request.query_params.get("deleted")
    if flag == "1":
        return {"kind": "success", "title": "Activity deleted", "detail": "", "sub": ""}
    if flag == "0":
        return {"kind": "error", "title": "Couldn't delete that activity", "detail": "", "sub": ""}
    return None


@app.get("/report/incentives/{member_id}", response_class=HTMLResponse)
def incentive_report(request: Request, member_id: int, limit: int = 50):
    return _render_ledger(request, member_id, "incentive", limit=limit, toast=_query_toast(request))


@app.get("/report/allowance/{member_id}", response_class=HTMLResponse)
def allowance_report(request: Request, member_id: int, limit: int = 50):
    return _render_ledger(request, member_id, "allowance", limit=limit, toast=_query_toast(request))


@app.get("/report/medications/{member_id}", response_class=HTMLResponse)
def medication_report(request: Request, member_id: int, period: str = "week", limit: int = 50, week: int = 0):
    return _render_ledger(request, member_id, "medication", limit=limit, period=period, week=week, toast=_query_toast(request))


@app.get("/report/other/{member_id}", response_class=HTMLResponse)
def other_report(request: Request, member_id: int, limit: int = 50):
    return _render_ledger(request, member_id, "other", limit=limit, toast=_query_toast(request))


@app.post("/activity/undo-add")
def activity_undo_add(request: Request, activity_id: int = Form(...)):
    try:
        row = db.delete_activity(activity_id)
        logger.info("Undo add: deleted activity %s, found=%s", activity_id, row is not None)
    except Exception as e:
        logger.error("Failed to undo add of activity %s: %s", activity_id, e)
        row = None
    if not _is_partial(request):
        return RedirectResponse(f"/?deleted={'1' if row else '0'}", status_code=303)
    if row is None:
        toast = {"kind": "error", "title": "Couldn't undo", "detail": "", "sub": ""}
        return templates.TemplateResponse(request=request, name="partials/activity_added.html", context={"m": None, "toast": toast})
    toast = {"kind": "success", "title": "Removed", "detail": row["activity_type"], "sub": ""}
    return _member_card_response(request, row["household_member_id"], toast)


@app.post("/activity/delete")
def activity_delete(
    request: Request,
    activity_id: int = Form(...),
    member_id: Optional[int] = Form(None),
    report: str = Form("incentive"),
    limit: int = Form(50),
):
    tab = report if report in LEDGER_TABS else "incentive"
    try:
        row = db.delete_activity(activity_id)
        logger.info("Deleted activity %s, found=%s", activity_id, row is not None)
    except Exception as e:
        logger.error("Failed to delete activity %s: %s", activity_id, e)
        row = None

    if not (_is_partial(request) and member_id):
        return _report_redirect(tab, member_id, ok=row is not None)

    if row is None:
        toast = {"kind": "error", "title": "Couldn't delete that activity", "detail": "", "sub": ""}
    else:
        token = _stash_deleted(row)
        toast = {
            "kind": "success",
            "title": "Deleted",
            "detail": row["activity_type"],
            "sub": "",
            "ttl": 8000,
            "action": {
                "label": "Undo",
                "url": "/activity/restore",
                "vals": {"token": token, "member_id": member_id, "report": tab, "limit": limit},
            },
        }
    return _render_ledger(request, member_id, tab, limit=limit, toast=toast)


@app.post("/activity/restore")
def activity_restore(
    request: Request,
    token: str = Form(...),
    member_id: int = Form(...),
    report: str = Form("incentive"),
    limit: int = Form(50),
):
    tab = report if report in LEDGER_TABS else "incentive"
    row = _pop_deleted(token)
    ok = False
    if row is not None:
        try:
            ok = db.restore_activity(row)
            logger.info("Restored activity %s, ok=%s", row["id"], ok)
        except Exception as e:
            logger.error("Failed to restore activity %s: %s", row.get("id"), e)
    if ok:
        toast = {"kind": "success", "title": "Restored", "detail": row["activity_type"], "sub": ""}
    else:
        toast = {"kind": "error", "title": "Couldn't undo", "detail": "", "sub": "The undo window may have expired."}

    if not _is_partial(request):
        return RedirectResponse(_ledger_url(tab, member_id), status_code=303)
    return _render_ledger(request, member_id, tab, limit=limit, toast=toast, highlight=row["id"] if ok else None)


# --- spinners ----------------------------------------------------------------

SPINNER_EMOJI = ["🎬", "🍕", "🎲", "🎮", "🏕️", "🎨", "🍦", "🎯", "📚", "🏖️"]


def _get_spinner_or_404(spinner_id: int) -> dict:
    spinner = db.get_spinner(spinner_id)
    if spinner is None:
        raise HTTPException(status_code=404, detail="Unknown spinner")
    return spinner


def _movie_label(m: jellyfin.Movie) -> str:
    return f"{m.name} ({m.year})" if m.year else m.name


def _wheel_segments(picks: list[dict], idx_by_member: dict[int, int]) -> list[dict]:
    """Equal slices clockwise from 12 o'clock, coloured by the pick's owner."""
    n = len(picks)
    if n == 0:
        return []
    size = 360 / n
    segs = []
    for i, p in enumerate(picks):
        start = i * size
        segs.append(
            {
                **p,
                "idx": i,
                "start": start,
                "end": start + size,
                "mid": start + size / 2,
                "color": f"var(--member-{(idx_by_member.get(p['member_id'], 0) % 6) + 1})",
            }
        )
    return segs


def _spinner_ctx(spinner: dict, winner_idx: Optional[int] = None, result_id: Optional[int] = None) -> dict:
    roster = db.list_household_members()
    idx_by_member = {mid: i for i, (mid, _) in enumerate(roster)}
    picks = db.spinner_picks(spinner["id"])
    pick_by_member = {p["member_id"]: p for p in picks}
    for p in picks:
        p["idx"] = idx_by_member.get(p["member_id"], 0)
    members = [{"id": mid, "name": name, "idx": i, "pick": pick_by_member.get(mid)} for i, (mid, name) in enumerate(roster)]
    segments = _wheel_segments(picks, idx_by_member)
    gradient = ", ".join(f"{s['color']} {s['start']:.3f}deg {s['end']:.3f}deg" for s in segments)
    history = db.spin_history(spinner["id"])
    return {
        "spinner": spinner,
        "members": members,
        "member_idx": idx_by_member,
        "picks": picks,
        "segments": segments,
        "gradient": f"conic-gradient(from 0deg, {gradient})" if gradient else "",
        "can_spin": len(picks) >= 2,
        "history": history,
        "last": history[0] if history else None,
        "jellyfin_enabled": jellyfin.enabled() and spinner["kind"] == "movie",
        "places_enabled": places.enabled() and spinner["kind"] == "restaurant",
        "max_drive": settings.RESTAURANT_MAX_DRIVE_MIN,
        "winner_idx": winner_idx,
        "result_id": result_id,
        "labels": [p["label"] for p in picks],
    }


def _spinner_panel(request: Request, spinner: dict, toast: Optional[dict] = None):
    """Full page on a normal GET, just the panel for HTMX swaps, redirect for non-JS POSTs."""
    if request.method != "GET" and not _is_partial(request):
        return RedirectResponse(f"/spin/{spinner['id']}", status_code=303)
    ctx = _spinner_ctx(spinner)
    ctx["toast"] = toast
    ctx["partial"] = _is_partial(request)
    name = "partials/spinner_panel.html" if _is_partial(request) else "spinner.html"
    return templates.TemplateResponse(request=request, name=name, context=ctx)


@app.get("/spin", response_class=HTMLResponse)
def spin_index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="spin_index.html",
        context={"spinners": db.list_spinners(), "emoji": SPINNER_EMOJI, "toast": _query_toast(request)},
    )


@app.post("/spin")
def spin_create(name: str = Form(...), emoji: str = Form("🎯"), kind: str = Form("generic")):
    name = name.strip()[:60]
    if not name:
        return RedirectResponse("/spin", status_code=303)
    emoji = emoji.strip()[:4] or "🎯"
    kind = kind if kind in db.SPINNER_KINDS else "generic"
    spinner_id = db.create_spinner(name, emoji, kind)
    return RedirectResponse(f"/spin/{spinner_id}", status_code=303)


@app.get("/spin/{spinner_id}", response_class=HTMLResponse)
def spin_show(request: Request, spinner_id: int):
    return _spinner_panel(request, _get_spinner_or_404(spinner_id))


@app.post("/spin/{spinner_id}/delete")
def spin_delete(spinner_id: int):
    ok = db.delete_spinner(spinner_id)
    logger.info("Deleted spinner %s, found=%s", spinner_id, ok)
    return RedirectResponse(f"/spin?deleted={'1' if ok else '0'}", status_code=303)


@app.post("/spin/{spinner_id}/pick")
def spin_pick(
    request: Request,
    spinner_id: int,
    member_id: int = Form(...),
    label: str = Form(""),
    jellyfin_item_id: Optional[str] = Form(None),
    place_id: Optional[str] = Form(None),
):
    spinner = _get_spinner_or_404(spinner_id)
    label = label.strip()[:80]
    item_id = jellyfin_item_id.strip() if jellyfin_item_id and jellyfin.valid_item_id(jellyfin_item_id.strip()) else None
    pid = place_id.strip() if place_id and places.valid_place_id(place_id.strip()) else None
    if item_id and not label:
        movie = jellyfin.get(item_id)
        label = _movie_label(movie) if movie else ""
    if pid and not label:
        place = places.get(pid)
        label = place.name[:80] if place else ""
    if not label:
        db.clear_pick(spinner_id, member_id)
    else:
        db.set_pick(spinner_id, member_id, label, item_id, pid)
    return _spinner_panel(request, spinner)


@app.post("/spin/{spinner_id}/pick/clear")
def spin_pick_clear(request: Request, spinner_id: int, member_id: int = Form(...)):
    spinner = _get_spinner_or_404(spinner_id)
    db.clear_pick(spinner_id, member_id)
    return _spinner_panel(request, spinner)


@app.post("/spin/{spinner_id}/pick/random")
def spin_pick_random(request: Request, spinner_id: int, member_id: int = Form(...), kids: Optional[str] = Form(None)):
    spinner = _get_spinner_or_404(spinner_id)
    toast = None
    if spinner["kind"] == "restaurant":
        taken = {p["place_id"] for p in db.spinner_picks(spinner_id) if p["place_id"]}
        found = [p for p in places.search(avoid=db.recent_place_ids(), shuffle=True) if p.id not in taken] if places.enabled() else []
        if found:
            # Choose among the top few so it's a good spot, not just any spot.
            place = secrets.choice(found[:8])
            db.set_pick(spinner_id, member_id, place.name[:80], None, place.id)
        else:
            toast = {"kind": "error", "title": "No restaurant suggestions right now", "detail": "", "sub": "Check GOOGLE_MAPS_API_KEY / HOME_LAT / HOME_LNG."}
        return _spinner_panel(request, spinner, toast=toast)
    movie = None
    if jellyfin.enabled():
        found = jellyfin.search(kids=kids == "1", exclude=db.recent_movie_ids(), limit=1)
        movie = found[0] if found else None
    if movie:
        db.set_pick(spinner_id, member_id, _movie_label(movie), movie.id)
    else:
        toast = {"kind": "error", "title": "No Jellyfin suggestions right now", "detail": "", "sub": "Check the JELLYFIN_* settings or try again later."}
    return _spinner_panel(request, spinner, toast=toast)


@app.post("/spin/{spinner_id}/reset")
def spin_reset(request: Request, spinner_id: int):
    spinner = _get_spinner_or_404(spinner_id)
    db.clear_picks(spinner_id)
    return _spinner_panel(request, spinner, toast={"kind": "success", "title": "New round", "detail": "", "sub": "Everyone can make a new pick."})


@app.post("/spin/{spinner_id}/spin")
def spin_go(request: Request, spinner_id: int):
    spinner = _get_spinner_or_404(spinner_id)
    picks = db.spinner_picks(spinner_id)
    toast = None
    winner_idx = result_id = None
    if len(picks) < 2:
        toast = {"kind": "error", "title": "Need at least two picks to spin", "detail": "", "sub": ""}
    else:
        winner_idx = secrets.randbelow(len(picks))
        options = [{k: p[k] for k in ("label", "member_id", "member", "jellyfin_item_id", "place_id")} for p in picks]
        result_id = db.record_spin(spinner_id, picks[winner_idx], options)
        logger.info("Spin %s on spinner %s -> %r", result_id, spinner_id, picks[winner_idx]["label"])
    if not _is_partial(request):
        return RedirectResponse(f"/spin/{spinner_id}", status_code=303)
    ctx = _spinner_ctx(spinner, winner_idx=winner_idx, result_id=result_id)
    ctx["toast"] = toast
    ctx["partial"] = True
    return templates.TemplateResponse(request=request, name="partials/spin_outcome.html", context=ctx)


@app.get("/spin/{spinner_id}/movies", response_class=HTMLResponse)
def spin_movies(
    request: Request,
    spinner_id: int,
    member_id: int,
    q: str = "",
    genre: str = "",
    kids: str = "",
    grid: str = "",
):
    spinner = _get_spinner_or_404(spinner_id)
    member = next(({"id": mid, "name": name} for mid, name in db.list_household_members() if mid == member_id), None)
    if member is None:
        raise HTTPException(status_code=404, detail="Unknown household member")
    movies = jellyfin.search(q=q, genre=genre, kids=kids == "1", exclude=db.recent_movie_ids()) if jellyfin.enabled() else []
    ctx = {
        "spinner": spinner,
        "member": member,
        "movies": movies,
        "genres": jellyfin.genres() if jellyfin.enabled() else [],
        "q": q,
        "genre": genre,
        "kids": kids == "1",
        "jellyfin_enabled": jellyfin.enabled(),
        "recent_days": settings.JELLYFIN_RECENT_DAYS,
    }
    name = "partials/movie_grid.html" if grid == "1" else "partials/movie_picker.html"
    return templates.TemplateResponse(request=request, name=name, context=ctx)


@app.get("/spin/{spinner_id}/places", response_class=HTMLResponse)
def spin_places(
    request: Request,
    spinner_id: int,
    member_id: int,
    q: str = "",
    category: str = "casual",
    open_now: str = "",
    shuffle: str = "",
    grid: str = "",
):
    spinner = _get_spinner_or_404(spinner_id)
    member = next(({"id": mid, "name": name} for mid, name in db.list_household_members() if mid == member_id), None)
    if member is None:
        raise HTTPException(status_code=404, detail="Unknown household member")
    category = category if category in places.CATEGORIES else "casual"
    recent = db.recent_place_ids()
    found = places.search(q=q, category=category, open_now=open_now == "1", avoid=recent, shuffle=shuffle == "1") if places.enabled() else []
    ctx = {
        "spinner": spinner,
        "member": member,
        "places": found,
        "recent": recent,
        "categories": {k: v[0] for k, v in places.CATEGORIES.items()},
        "q": q,
        "category": category,
        "open_now": open_now == "1",
        "places_enabled": places.enabled(),
        "max_drive": settings.RESTAURANT_MAX_DRIVE_MIN,
    }
    name = "partials/place_grid.html" if grid == "1" else "partials/place_picker.html"
    return templates.TemplateResponse(request=request, name=name, context=ctx)


@app.get("/places/photo/{place_id}")
def place_photo(place_id: str):
    if not places.valid_place_id(place_id):
        raise HTTPException(status_code=404)
    img = places.photo(place_id)
    if img is None:
        raise HTTPException(status_code=404)
    data, content_type = img
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "public, max-age=604800"})


@app.get("/jellyfin/image/{item_id}")
def jellyfin_image(item_id: str):
    if not jellyfin.valid_item_id(item_id):
        raise HTTPException(status_code=404)
    img = jellyfin.image(item_id)
    if img is None:
        raise HTTPException(status_code=404)
    data, content_type = img
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "public, max-age=86400"})


# --- restaurant ABC ------------------------------------------------------------

ABC_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ROUND_NAMES = ("First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth", "Tenth")


def _round_name(n: int) -> str:
    return ROUND_NAMES[n - 1] if 1 <= n <= len(ROUND_NAMES) else f"Round {n}"


templates.env.filters["round_name"] = _round_name


def _abc_letter(raw: Optional[str]) -> Optional[str]:
    letter = (raw or "").strip().upper()
    return letter if len(letter) == 1 and letter in ABC_LETTERS else None


def _abc_maps_url(v: dict) -> str:
    params = {"api": "1", "query": " ".join(filter(None, [v["name"], v.get("location")]))}
    if v.get("place_id"):
        params.update(query=v["name"], query_place_id=v["place_id"])
    return "https://www.google.com/maps/search/?" + urlencode(params, quote_via=quote)


def _render_abc(request: Request, round_no: Optional[int] = None, letter: Optional[str] = None, toast: Optional[dict] = None):
    if request.method != "GET" and not _is_partial(request):
        return RedirectResponse(f"/abc?round={round_no or ''}&letter={letter or ''}", status_code=303)
    visits = db.abc_visits()
    by_round: dict[int, dict[str, dict]] = {}
    for v in visits:
        v["maps_url"] = _abc_maps_url(v)
        by_round.setdefault(v["round_no"], {})[v["letter"]] = v
    # The active round is the first one with letters left; "up next" is its first open letter.
    active = 1
    while len(by_round.get(active, {})) == len(ABC_LETTERS):
        active += 1
    current = next(c for c in ABC_LETTERS if c not in by_round.get(active, {}))
    last_round = max([active, *by_round])
    if not round_no or not 1 <= round_no <= last_round + 1:
        round_no = active
    done = by_round.get(round_no, {})
    letter = letter or (current if round_no == active else next((c for c in ABC_LETTERS if c not in done), "A"))
    visit = done.get(letter)
    rated = [v["rating"] for v in visits if v["rating"]]
    rounds = [{"no": n, "name": _round_name(n), "done": len(by_round.get(n, {})), "new": False} for n in range(1, last_round + 1)]
    if by_round.get(last_round) or round_no == last_round + 1:
        rounds.append({"no": last_round + 1, "name": _round_name(last_round + 1), "done": 0, "new": True})
    ctx = {
        "rounds": rounds,
        "round_no": round_no,
        "round_name": _round_name(round_no),
        "is_active": round_no == active,
        "current": current,
        "tiles": [{"letter": c, "visit": done.get(c), "current": round_no == active and c == current} for c in ABC_LETTERS],
        "done_count": len(done),
        "letter": letter,
        "visit": visit,
        "others": [v for v in visits if v["letter"] == letter and v["round_no"] != round_no],
        "form": visit or {"visited_on": datetime.now(settings.LOCAL_TZ).date()},
        "stats": {
            "count": len(visits),
            "avg": sum(rated) / len(rated) if rated else None,
            "favorites": [v["name"] for v in visits if v["rating"] == 5],
        },
        "places_enabled": places.enabled(),
        "toast": toast,
        "partial": _is_partial(request),
    }
    name = "partials/abc_panel.html" if _is_partial(request) else "abc.html"
    return templates.TemplateResponse(request=request, name=name, context=ctx)


@app.get("/abc", response_class=HTMLResponse)
def abc_show(request: Request, round: Optional[str] = None, letter: Optional[str] = None):
    round_no = int(round) if round and round.isdigit() else None
    return _render_abc(request, round_no, _abc_letter(letter))


@app.get("/abc/ideas", response_class=HTMLResponse)
def abc_ideas(request: Request, letter: str, q: str = "", grid: str = ""):
    letter = _abc_letter(letter)
    if letter is None:
        raise HTTPException(status_code=404, detail="Unknown letter")
    visited = [(v["place_id"], v["name"]) for v in db.abc_visits()]
    found = places.abc_suggestions(letter, q=q, visited=visited) if places.enabled() else []
    ctx = {
        "letter": letter,
        "places": found,
        "loose": bool(found) and places.first_letter(found[0].name) != letter,
        "q": q.strip()[:80],
        "places_enabled": places.enabled(),
        "max_drive": settings.ABC_MAX_DRIVE_MIN,
    }
    name = "partials/abc_idea_grid.html" if grid == "1" else "partials/abc_ideas.html"
    return templates.TemplateResponse(request=request, name=name, context=ctx)


@app.post("/abc/visit")
def abc_save(
    request: Request,
    letter: str = Form(...),
    name: str = Form(...),
    round_no: int = Form(1),
    visit_id: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    rating: Optional[str] = Form(None),
    visited_on: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    place_id: Optional[str] = Form(None),
):
    letter = _abc_letter(letter)
    name = name.strip()[:80]
    if letter is None or not name or not 1 <= round_no <= 99:
        raise HTTPException(status_code=400, detail="Letter, round and name are required")
    try:
        day = date.fromisoformat(visited_on) if visited_on else None
    except ValueError:
        day = None
    pid = (place_id or "").strip()
    v = {
        "round_no": round_no,
        "letter": letter,
        "name": name,
        "location": (location or "").strip()[:60] or None,
        "rating": int(rating) if rating and rating.isdigit() and 1 <= int(rating) <= 5 else None,
        "visited_on": day,
        "notes": (notes or "").strip()[:500] or None,
        "place_id": pid if places.valid_place_id(pid) else None,
    }
    vid = int(visit_id) if visit_id and visit_id.isdigit() else None
    if db.save_abc_visit(vid, v) is None:
        toast = {"kind": "error", "title": f"{letter} already has a restaurant in the {_round_name(round_no).lower()} round", "detail": "", "sub": "Edit that one, or log this under another round."}
        return _render_abc(request, round_no, letter, toast)
    logger.info("ABC %s round %s -> %r (id=%s)", letter, round_no, name, vid or "new")
    stars = "★" * v["rating"] if v["rating"] else ""
    toast = {"kind": "success", "title": f"{letter} is for {name}", "detail": stars, "sub": "" if vid else "On to the next letter!"}
    # New visits move on to the next open letter; edits stay put.
    return _render_abc(request, round_no, letter if vid else None, toast)


@app.post("/abc/visit/{visit_id}/delete")
def abc_delete(request: Request, visit_id: int):
    row = db.delete_abc_visit(visit_id)
    logger.info("Deleted ABC visit %s, found=%s", visit_id, row is not None)
    if row is None:
        return _render_abc(request, toast={"kind": "error", "title": "Couldn't remove that visit", "detail": "", "sub": ""})
    toast = {"kind": "success", "title": f"Removed {row['name']}", "detail": "", "sub": ""}
    return _render_abc(request, row["round_no"], row["letter"], toast)


def run(host: str = "0.0.0.0", port: int = 8000, reload: bool = False):
    import uvicorn

    uvicorn.run("app.web:app", host=host, port=port, reload=reload)
