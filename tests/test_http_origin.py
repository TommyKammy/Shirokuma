"""Pure contracts for the shared, exact browser Origin policy."""
from email.message import Message
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/local_experiment'))
import http_origin
import request_auth


def headers(*pairs):
    message = Message()
    for key, value in pairs:
        message[key] = value
    return message


class HttpOriginTests(unittest.TestCase):
    def test_http_matches_exact_host_without_shared_mode(self):
        with patch.object(request_auth, 'shared_mode', side_effect=AssertionError('HTTP mode lookup')):
            for host in ('localhost', 'localhost:8080', '[::1]:8080', 'Example.test:80'):
                self.assertTrue(http_origin.request_origin_allowed(
                    headers(('Host', host), ('Origin', 'http://' + host))))
            for origin in ('http://localhost', 'http://LOCALHOST:8080',
                           'http://localhost:8080/', ' http://localhost:8080',
                           'http://localhost:8080.evil'):
                self.assertFalse(http_origin.request_origin_allowed(
                    headers(('Host', 'localhost:8080'), ('Origin', origin))))

    def test_https_uses_current_shared_mode_and_authentication_error_is_false(self):
        message = headers(('Host', 'localhost:8080'), ('Origin', 'https://localhost:8080'))
        for mode in (True, False, True):
            with patch.object(request_auth, 'shared_mode', return_value=mode) as shared:
                self.assertIs(http_origin.request_origin_allowed(message), mode)
                shared.assert_called_once_with()
        with patch.object(request_auth, 'shared_mode',
                          side_effect=request_auth.AuthenticationError('invalid configuration')):
            self.assertFalse(http_origin.request_origin_allowed(message))

    def test_missing_empty_and_mismatched_authorities_are_false(self):
        with patch.object(request_auth, 'shared_mode', side_effect=AssertionError('unexpected lookup')):
            for pairs in ((), (('Host', 'localhost'),), (('Origin', 'http://localhost'),),
                          (('Host', ''), ('Origin', 'http://localhost')),
                          (('Host', 'localhost'), ('Origin', '')),
                          (('Host', 'localhost'), ('Origin', 'https://elsewhere'))):
                self.assertFalse(http_origin.request_origin_allowed(headers(*pairs)))

    def test_duplicate_headers_keep_first_value_semantics(self):
        for origins, hosts, expected in (
                (('http://localhost', 'http://elsewhere'), ('localhost',), True),
                (('http://elsewhere', 'http://localhost'), ('localhost',), False),
                (('', 'http://localhost'), ('localhost',), False),
                (('http://localhost',), ('localhost', 'elsewhere'), True),
                (('http://localhost',), ('elsewhere', 'localhost'), False)):
            with self.subTest(origins=origins, hosts=hosts):
                self.assertIs(http_origin.request_origin_allowed(headers(
                    *[('Origin', value) for value in origins],
                    *[('Host', value) for value in hosts])), expected)


if __name__ == '__main__':
    unittest.main()
