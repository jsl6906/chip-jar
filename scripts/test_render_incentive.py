from jinja2 import Environment, FileSystemLoader
from datetime import timezone


def _fmt_signed(value):
    if value is None:
        return ""
    return f"+{value}" if isinstance(value, (int, float)) and value > 0 else str(value)


def _fmt_ts(value):
    if not value:
        return ""
    # naive UTC to simple formatted string for testing
    if value.tzinfo is None:
        return value.strftime("%Y-%m-%d %H:%M:%S UTC")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
from datetime import datetime, timedelta

# Load templates from app/templates
env = Environment(loader=FileSystemLoader('app/templates'))
# Register filters used by the application templates
env.filters['fmt_signed'] = _fmt_signed
env.filters['fmt_ts'] = _fmt_ts
tmpl = env.get_template('incentive_report.html')

# Create sample rows: two entries on same day, one on previous day. Rows now include id as first element.
now = datetime.utcnow()
rows = []
# two entries today
rows.append((1, now, 'Do homework', 10, 'good', 10))
rows.append((2, now - timedelta(hours=1), 'Clean room', -5, 'messy', 5))
# one entry yesterday
rows.append((3, now - timedelta(days=1), 'Help sibling', 15, 'kind', 20))

# sample context
ctx = {
    'request': None,
    'rows': rows,
    'total': 35,
    'prior_balance': 0,
    'prior_count': 0,
    'member_id': 1,
    'members': [(1, 'Test')],
    'incentive_desc': {'Do homework': 'desc'}
}

out = tmpl.render(**ctx)
print(out)
