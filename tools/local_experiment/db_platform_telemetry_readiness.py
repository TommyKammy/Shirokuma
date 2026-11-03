"""Record independent observations of the two fixed local telemetry endpoints."""
from http.client import HTTPConnection, HTTPException
import re

from db_platform_observations import _authority, _now
from warehouse_operations import OperationError

_MAX_BODY = 256
_TIMEOUT = 2


def _endpoint(port, path, statuses):
    connection = HTTPConnection('127.0.0.1', port, timeout=_TIMEOUT)
    try:
        connection.request('GET', path, headers={'Connection': 'close'})
        with connection.getresponse() as response:
            headers = response.headers
            lengths = headers.get_all('Content-Length', [])
            transfer = headers.get_all('Transfer-Encoding', [])
            content = headers.get_all('Content-Encoding', [])
            if (response.status not in statuses or headers.defects or len(lengths) > 1
                    or (content and [value.lower() for value in content] != ['identity'])):
                return None, b''
            if transfer:
                if (lengths or response.version != 11 or not response.chunked
                        or [value.lower() for value in transfer] != ['chunked']):
                    return None, b''
            if lengths:
                if not re.fullmatch(r'[0-9]{1,3}', lengths[0]):
                    return None, b''
                length = int(lengths[0])
                if length > _MAX_BODY:
                    return None, b''
                body = response.read(length)
                if len(body) != length:
                    return None, b''
            elif response.chunked or response.will_close:
                body = response.read(_MAX_BODY + 1)
                if len(body) > _MAX_BODY:
                    return None, b''
            else:
                return None, b''
            return response.status, body
    except (OSError, HTTPException, ValueError):
        return None, b''
    finally:
        connection.close()


def _agenthouse():
    status, body = _endpoint(18123, '/ping', (200,))
    return 'responsive' if status == 200 and body == b'Ok.\n' else 'unavailable'


def _loki():
    status, body = _endpoint(13100, '/ready', (200, 503))
    if status == 503:
        return 'not_ready'
    if status == 200:
        try:
            if body.decode('utf-8').strip() == 'ready':
                return 'ready'
        except UnicodeDecodeError:
            pass
    return 'unavailable'


def observe(principal, role):
    fingerprint = _authority(principal, role)
    result = {}
    for name, check in (('agenthouse', _agenthouse), ('loki', _loki)):
        result[name] = dict(status=check(), observed_at=_now())
        if _authority(principal, role) != fingerprint:
            raise OperationError('PLATFORM_ACCESS_DENIED', 403)
    return result
