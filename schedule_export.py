import csv
import io
import re
from datetime import datetime, timedelta
from audit import EAT_LABEL, EAT_TZ
from database import format_session_schedule, session_conducted_label
TIMEFRAME_OPTIONS = {'all': 'All sessions', 'upcoming': 'Upcoming only', 'past': 'Past only'}
STATUS_OPTIONS = {'': 'Any status', 'scheduled': 'Scheduled', 'completed': 'Completed', 'cancelled': 'Cancelled'}
SESSION_TYPE_OPTIONS = {'': 'Any type', 'online': 'Online', 'practical': 'Practical', 'both': 'Online & practical'}

def filters_are_default(filters):
    return (filters.get('timeframe') or 'all') == 'all' and (not filters.get('area_id')) and (not filters.get('status')) and (not filters.get('session_type')) and (not filters.get('date_from')) and (not filters.get('date_to'))

def describe_schedule_filters(filters, area_name=None):
    parts = [TIMEFRAME_OPTIONS.get(filters.get('timeframe') or 'all', 'All sessions')]
    if area_name:
        parts.append(f'Area: {area_name}')
    elif filters.get('area_id'):
        parts.append(f"Area ID: {filters['area_id']}")
    if filters.get('status'):
        parts.append(STATUS_OPTIONS.get(filters['status'], filters['status']))
    if filters.get('session_type'):
        parts.append(SESSION_TYPE_OPTIONS.get(filters['session_type'], filters['session_type']))
    if filters.get('date_from'):
        parts.append(f"From {filters['date_from']} (EAT)")
    if filters.get('date_to'):
        parts.append(f"To {filters['date_to']} (EAT)")
    return parts

def build_ics_calendar_name(filters, area_name=None):
    timeframe = filters.get('timeframe') or 'all'
    if timeframe == 'upcoming':
        name = 'LCAF Upcoming Sessions'
    elif timeframe == 'past':
        name = 'LCAF Past Sessions'
    else:
        name = 'LCAF Programme Schedule'
    if area_name:
        name = f'{name} — {area_name}'
    return name

def parse_schedule_filters(source):
    src = source
    area_raw = (src.get('area_id') or '').strip()
    area_id = int(area_raw) if area_raw.isdigit() else None
    timeframe = (src.get('timeframe') or 'all').strip()
    if timeframe not in TIMEFRAME_OPTIONS:
        timeframe = 'all'
    status = (src.get('status') or '').strip()
    if status not in STATUS_OPTIONS:
        status = ''
    session_type = (src.get('session_type') or '').strip()
    if session_type not in SESSION_TYPE_OPTIONS:
        session_type = ''
    return {'date_from': (src.get('date_from') or '').strip() or None, 'date_to': (src.get('date_to') or '').strip() or None, 'area_id': area_id, 'status': status or None, 'session_type': session_type or None, 'timeframe': timeframe, 'include_cancelled': status == 'cancelled'}

def _ics_escape(text):
    if not text:
        return ''
    return str(text).replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')

def _ics_datetime(date_str, time_str=None):
    if not date_str:
        return None
    time_str = (time_str or '09:00')[:5]
    try:
        dt = datetime.strptime(f'{date_str[:10]} {time_str}', '%Y-%m-%d %H:%M')
        dt = dt.replace(tzinfo=EAT_TZ)
        return dt.strftime('%Y%m%dT%H%M%S')
    except ValueError:
        return None

def schedule_rows_to_csv(sessions):
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow([f'Scheduled date ({EAT_LABEL})', 'Scheduled time (EAT)', f'Conducted ({EAT_LABEL})', 'Thematic area', 'Module number', 'Module name', 'Session title', 'Session type', 'Status', 'Facilitator', 'Notes'])
    for s in sessions:
        conducted = session_conducted_label(s) or ''
        writer.writerow([s['scheduled_date'], s['scheduled_time'] or '', conducted, s['area_name'], s['module_number'], s['module_name'], s['title'] or '', s['session_type'], s['status'], s['facilitator_name'] or '', s['notes'] or ''])
    return output.getvalue()

def schedule_rows_to_ics(sessions, calendar_name='LCAF Programme Schedule'):
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Light Come Africa Foundation//LCAF Registry//EN', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH', f'X-WR-CALNAME:{_ics_escape(calendar_name)}', 'X-WR-TIMEZONE:Africa/Nairobi']
    for s in sessions:
        start = _ics_datetime(s['scheduled_date'], s['scheduled_time'])
        if not start:
            continue
        try:
            dt_start = datetime.strptime(start, '%Y%m%dT%H%M%S').replace(tzinfo=EAT_TZ)
            dt_end = dt_start + timedelta(hours=1)
            end = dt_end.strftime('%Y%m%dT%H%M%S')
        except ValueError:
            continue
        title = s['title'] or f"M{s['module_number']} — {s['module_name']}"
        summary = f"{s['area_name']}: {title}"
        desc_parts = [f"Area: {s['area_name']}", f"Module: M{s['module_number']} — {s['module_name']}", f"Type: {s['session_type']}", f"Status: {s['status']}"]
        if s['facilitator_name']:
            desc_parts.append(f"Facilitator: {s['facilitator_name']}")
        if s['notes']:
            desc_parts.append(f"Notes: {s['notes']}")
        conducted = session_conducted_label(s)
        if conducted:
            desc_parts.append(f'Conducted: {conducted}')
        location = 'Online' if s['session_type'] == 'online' else 'LCAF training session'
        lines.extend(['BEGIN:VEVENT', f"UID:lcaf-session-{s['id']}@lightcomeafrica.org", f"DTSTAMP:{datetime.now(EAT_TZ).strftime('%Y%m%dT%H%M%S')}", f'DTSTART;TZID=Africa/Nairobi:{start}', f'DTEND;TZID=Africa/Nairobi:{end}', f'SUMMARY:{_ics_escape(summary)}', f'DESCRIPTION:{_ics_escape(chr(10).join(desc_parts))}', f'LOCATION:{_ics_escape(location)}', f"STATUS:{('CONFIRMED' if s['status'] == 'scheduled' else 'CANCELLED' if s['status'] == 'cancelled' else 'COMPLETED')}", 'END:VEVENT'])
    lines.append('END:VCALENDAR')
    return '\r\n'.join(lines) + '\r\n'

def build_schedule_filename(filters, fmt, export_date):
    parts = ['lcaf-schedule']
    if filters.get('timeframe') and filters['timeframe'] != 'all':
        parts.append(filters['timeframe'])
    if filters.get('date_from'):
        parts.append(f"from-{filters['date_from']}")
    if filters.get('date_to'):
        parts.append(f"to-{filters['date_to']}")
    if filters.get('status'):
        parts.append(filters['status'])
    if filters.get('session_type'):
        parts.append(filters['session_type'])
    if filters.get('area_id'):
        parts.append(f"area-{filters['area_id']}")
    parts.append(export_date)
    safe = re.sub('[^\\w\\-]', '-', '-'.join(parts))
    ext = 'csv' if fmt == 'csv' else 'ics'
    return f'{safe}.{ext}'
