"""Shared job definition helpers used by the legacy and Database job paths."""
from datetime import date
import fcntl
import re

import control_store
import warehouse_resident as wr

PARAMETER = re.compile(r'[A-Za-z_][A-Za-z_0-9]*')
SLOT = re.compile(r'\{\{\s*([A-Za-z_][A-Za-z_0-9]*)\s*\}\}')
QUOTED = re.compile(r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"")


def _root():
    return control_store.root()


def _typed(value, kind, nullable=False):
    if value is None and nullable:
        return None
    if kind == 'string' and isinstance(value, str):
        return value
    if kind == 'integer' and type(value) is int and -(2**63) <= value < 2**63:
        return value
    if kind == 'boolean' and type(value) is bool:
        return value
    if kind == 'date' and isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        date.fromisoformat(value)
        return value
    raise ValueError('parameter does not match type ' + str(kind))


def _render(sql, parameters, arguments):
    for quoted in QUOTED.finditer(sql):
        if '{{' in quoted[0] or '}}' in quoted[0]:
            raise ValueError('SQL parameters cannot occur inside strings, identifiers, or comments')
    unquoted = QUOTED.sub('', sql)
    if "'" in unquoted or '"' in unquoted or '/*' in unquoted or '*/' in unquoted:
        raise ValueError('SQL contains an unterminated quote or comment')
    used = set()
    def replace(match):
        name = match[1]
        if name not in parameters:
            raise ValueError('undeclared SQL parameter: ' + name)
        before, after = sql[:match.start()], sql[match.end():]
        if (before and re.search(r'[\w$."\']$', before) or
                after and re.match(r'[\w$."\']', after)):
            raise ValueError('SQL parameters must be separate scalar values')
        if re.search(r'\b(?:FROM|JOIN|TABLE|SCHEMA|CATALOG|USE|AS|INTO|UPDATE|CALL|EXECUTE|EXISTS)\s*$',
                     QUOTED.sub('', before), re.IGNORECASE):
            raise ValueError('SQL parameters cannot supply identifiers or SQL types')
        used.add(name)
        value = arguments[name]
        kind = parameters[name]['type']
        if value is None:
            return 'NULL'
        if kind == 'integer':
            return str(value)
        if kind == 'boolean':
            return 'TRUE' if value else 'FALSE'
        return ('DATE ' if kind == 'date' else '') + wr.literal(value)
    rendered = SLOT.sub(replace, sql)
    if '{{' in rendered or '}}' in rendered:
        # Inspect the original leftovers, since an argument can itself contain braces.
        if '{{' in SLOT.sub('', sql) or '}}' in SLOT.sub('', sql):
            raise ValueError('invalid SQL parameter placeholder')
    return rendered, used


def _lock():
    lock = (_root() / 'jobs-active.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock
