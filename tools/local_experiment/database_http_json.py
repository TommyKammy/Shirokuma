"""Shared database HTTP JSON framing and serialization."""
import json


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
    data = json.dumps(value, default=str).encode("utf-8")
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
