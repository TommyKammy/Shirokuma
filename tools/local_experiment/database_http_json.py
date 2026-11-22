"""Shared database HTTP JSON framing and serialization."""
import json


class JSONResponse(dict):
    """A mapping whose exact UTF-8 JSON body was already encoded once.

    First-delivery pages carry their encoded body so the HTTP layer does not
    serialize the same ~2 MiB mapping a second time.  The mapping stays a real
    dict for callers; its ``body`` slot is internal.  It is constructed from the
    freshly built page and the caller's object is not the replay cache (the
    cache holds the encoded bytes), so later mutation of the mapping cannot
    change replay data.
    """
    __slots__ = ("body",)

    def __init__(self, value, body):
        if not isinstance(value, dict) or not isinstance(body, (bytes, bytearray)):
            raise TypeError("JSONResponse requires a mapping and encoded bytes")
        super().__init__(value)
        self.body = bytes(body)


def encode(value):
    return json.dumps(value, default=str).encode("utf-8")


def database_body(handler):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON field')
            result[key] = value
        return result
    if handler.headers.get_content_type() != 'application/json' or len(handler.headers.get_all('Content-Length', [])) != 1:
        raise ValueError('JSON body required')
    length = int(handler.headers['Content-Length'])
    if not 0 < length <= 10 * 1024 * 1024 or handler.headers.get('Transfer-Encoding'):
        raise ValueError('Invalid body length')
    value = json.loads(handler.rfile.read(length), object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid JSON number')))
    if not isinstance(value, dict):
        raise ValueError('Body must be an object')
    return value


def respond(handler, value, status=200):
    data = value.body if isinstance(value, JSONResponse) else encode(value)
    handler.send_response(status)
    creation_location = getattr(handler, '_creation_location', None)
    if creation_location:
        handler.send_header('Location', creation_location)
        del handler._creation_location
    if status == 401:
        handler.send_header('WWW-Authenticate', 'Basic realm="Shirokuma", charset="UTF-8"')
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
