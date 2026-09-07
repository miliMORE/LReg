import re
from datetime import date
EMAIL_PATTERN = re.compile('^[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}$')
KENYA_NATIONAL_ID_PATTERN = re.compile('^\\d{7,8}$')
KENYA_PHONE_PATTERN = re.compile('^(?:\\+?254|0)?[17]\\d{8}$')

def normalize_email(raw):
    if not raw:
        return None
    return raw.strip().lower()
ADMIN_USERNAME_PATTERN = re.compile('^[a-zA-Z0-9][a-zA-Z0-9._-]{2,31}$')

def validate_admin_username(raw):
    username = (raw or '').strip()
    if len(username) < 3:
        return (None, 'Username is required (at least 3 characters).')
    if not ADMIN_USERNAME_PATTERN.match(username):
        return (None, 'Username: 3–32 characters, letters, numbers, dots, hyphens, or underscores.')
    return (username, None)

def validate_full_name(raw):
    name = (raw or '').strip()
    if len(name) < 3:
        return (None, 'Full name is required (at least 3 characters).')
    if not re.search('[A-Za-z]', name):
        return (None, 'Full name must contain letters.')
    parts = [p for p in name.split() if p]
    if len(parts) < 2:
        return (None, 'Enter full name (first and last name at minimum).')
    return (name, None)

def validate_kenya_national_id(raw):
    cleaned = re.sub('[\\s\\-]', '', (raw or '').strip())
    if not cleaned:
        return (None, 'National ID is required.')
    if not KENYA_NATIONAL_ID_PATTERN.match(cleaned):
        return (None, 'Kenya National ID must be 7 or 8 digits (numbers only).')
    return (cleaned, None)

def validate_email(raw, required=False):
    email = normalize_email(raw)
    if not email:
        if required:
            return (None, 'Email address is required.')
        return (None, None)
    if not EMAIL_PATTERN.match(email):
        return (None, 'Enter a valid email address (e.g. name@example.com).')
    return (email, None)

def validate_kenya_phone(raw, required=False):
    cleaned = re.sub('[\\s\\-]', '', (raw or '').strip())
    if not cleaned:
        if required:
            return (None, 'Phone number is required.')
        return (None, None)
    if not KENYA_PHONE_PATTERN.match(cleaned):
        return (None, 'Enter a valid Kenya phone number (e.g. 0712345678 or +254712345678).')
    if cleaned.startswith('+254'):
        normalized = cleaned
    elif cleaned.startswith('254'):
        normalized = '+' + cleaned
    elif cleaned.startswith('0'):
        normalized = '+254' + cleaned[1:]
    else:
        normalized = '+254' + cleaned
    return (normalized, None)

def validate_placement_org(raw):
    org = (raw or '').strip()
    if len(org) < 2:
        return (None, 'Placement organisation is required.')
    return (org, None)

def validate_join_date(raw):
    value = (raw or '').strip()
    if not value:
        return (None, 'Join date is required.')
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return (None, 'Join date must be a valid date (YYYY-MM-DD).')
    if parsed > date.today():
        return (None, 'Join date cannot be in the future.')
    return (value, None)

def validate_job_title(raw):
    title = (raw or '').strip()
    if not title:
        return (None, None)
    if len(title) < 2:
        return (None, 'Job title is too short.')
    return (title, None)
SLUG_PATTERN = re.compile('^[a-z0-9]+(?:-[a-z0-9]+)*$')

def suggest_slug_from_name(name):
    cleaned = re.sub('[^a-zA-Z0-9\\s\\-]', '', (name or '').strip().lower())
    cleaned = re.sub('[\\s_]+', '-', cleaned).strip('-')
    return cleaned or None

def validate_thematic_area_name(raw):
    name = (raw or '').strip()
    if len(name) < 2:
        return (None, 'Thematic area name is required (at least 2 characters).')
    if not re.search('[A-Za-z]', name):
        return (None, 'Thematic area name must contain letters.')
    return (name, None)

def validate_slug(raw, required=True):
    slug = (raw or '').strip().lower().replace(' ', '-')
    if not slug:
        if required:
            return (None, 'Slug is required (URL-safe identifier, e.g. digital-literacy).')
        return (None, None)
    if not SLUG_PATTERN.match(slug):
        return (None, 'Slug must be lowercase letters, numbers, and hyphens only (e.g. digital-literacy).')
    return (slug, None)

def validate_positive_int(raw, field_label, required=True, minimum=1):
    value = (raw or '').strip()
    if not value:
        if required:
            return (None, f'{field_label} is required.')
        return (None, None)
    try:
        number = int(value)
    except ValueError:
        return (None, f'{field_label} must be a whole number.')
    if number < minimum:
        return (None, f'{field_label} must be at least {minimum}.')
    return (number, None)

def validate_session_date(raw):
    value = (raw or '').strip()
    if not value:
        return (None, 'Session date is required.')
    try:
        date.fromisoformat(value)
    except ValueError:
        return (None, 'Session date must be a valid date (YYYY-MM-DD).')
    return (value, None)

def validate_session_time(raw, required=True):
    value = (raw or '').strip()
    if not value:
        if required:
            return (None, 'Session time is required (HH:MM, 24-hour format).')
        return (None, None)
    match = re.match('^([01]\\d|2[0-3]):([0-5]\\d)$', value)
    if not match:
        return (None, 'Session time must be in 24-hour HH:MM format (e.g. 09:00 or 14:30).')
    return (value, None)

def validate_module_name(raw):
    name = (raw or '').strip()
    if len(name) < 2:
        return (None, 'Module name is required (at least 2 characters).')
    return (name, None)
