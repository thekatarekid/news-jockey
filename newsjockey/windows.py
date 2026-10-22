"""Publication calendar months in the configured portal timezone."""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = 'Asia/Calcutta'

def portal_timezone(db):
    row = db.execute("SELECT value FROM settings WHERE key='portal_timezone'").fetchone()
    return row['value'] if row else DEFAULT_TIMEZONE

def validate_timezone(value):
    try: ZoneInfo(value)
    except (ZoneInfoNotFoundError, TypeError, ValueError): raise ValueError('Enter a valid IANA timezone, such as Asia/Calcutta or America/New_York.')
    return value

def month_window(month, name):
    if not isinstance(month,str) or not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])',month):
        raise ValueError('Select a calendar month in YYYY-MM format.')
    year,number=map(int,month.split('-'));tz=ZoneInfo(name)
    start=datetime(year,number,1,tzinfo=tz)
    end=datetime(year+int(number==12),1 if number==12 else number+1,1,tzinfo=tz)
    return dict(timezone=name,month=month,label=start.strftime('%B %Y'),
                start=start.astimezone(timezone.utc).isoformat(timespec='seconds'),
                end=end.astimezone(timezone.utc).isoformat(timespec='seconds'))

def date_window(db, at=None):
    name=portal_timezone(db);current=(at or datetime.now(timezone.utc)).astimezone(ZoneInfo(name))
    return {**month_window(current.strftime('%Y-%m'),name),'today':current.date().isoformat()}

def group_condition(group, window, alias='a'):
    if group in ('recent','current'): return f'{alias}.published_at>=? AND {alias}.published_at<?', [window['start'], window['end']]
    if group in ('older','previous'): return f'{alias}.published_at<?', [window['start']]
    if group == 'undated': return f'{alias}.published_at IS NULL', []
    if group == 'all': return '1=1', []
    chosen=month_window(group,window['timezone'])
    return f'{alias}.published_at>=? AND {alias}.published_at<?',[chosen['start'],chosen['end']]
