"""Shared browser Origin policy for local HTTP adapters."""
import request_auth


def request_origin_allowed(headers):
    """Match the browser authority; TLS proxies must preserve Host."""
    origin, host = headers.get('Origin'), headers.get('Host')
    if not origin or not host:
        return False
    if origin == 'http://' + host:
        return True
    if origin == 'https://' + host:
        try:
            return request_auth.shared_mode()
        except request_auth.AuthenticationError:
            return False
    return False

