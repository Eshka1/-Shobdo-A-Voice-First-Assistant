import re

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _check_fields(data, allowed, required):
    """Return an error string if the body isn't a dict, has unknown fields, or lacks required ones."""
    if not isinstance(data, dict):
        return 'Request body must be a JSON object'
    unknown = set(data) - set(allowed)
    if unknown:
        return 'Unexpected field(s): ' + ', '.join(sorted(unknown))
    missing = [f for f in required if f not in data]
    if missing:
        return 'Missing field(s): ' + ', '.join(missing)
    return None


def validate_register(data):
    """Returns (clean_data, error). Exactly one of them is None."""
    err = _check_fields(data, allowed=['name', 'email', 'password'],
                        required=['name', 'email', 'password'])
    if err:
        return None, err

    name, email, password = data['name'], data['email'], data['password']
    if not all(isinstance(v, str) for v in (name, email, password)):
        return None, 'name, email and password must be strings'

    name, email = name.strip(), email.strip().lower()
    if not 1 <= len(name) <= 80:
        return None, 'Name must be 1-80 characters'
    if len(email) > 254 or not EMAIL_RE.match(email):
        return None, 'Invalid email address'
    if len(password) < 8:
        return None, 'Password must be at least 8 characters'
    if len(password.encode('utf-8')) > 72:
        return None, 'Password must be at most 72 bytes'
    return {'name': name, 'email': email, 'password': password}, None


def validate_login(data):
    err = _check_fields(data, allowed=['email', 'password'],
                        required=['email', 'password'])
    if err:
        return None, err
    email, password = data['email'], data['password']
    if not isinstance(email, str) or not isinstance(password, str):
        return None, 'email and password must be strings'
    return {'email': email.strip().lower(), 'password': password}, None
