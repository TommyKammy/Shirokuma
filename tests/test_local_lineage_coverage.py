"""Read-only coverage preserves unknowns, failed inputs and the initial domain."""
import copy
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/local_experiment'))
sys.path.insert(0, str(ROOT / 'tests'))
from metadata import coverage as C
from test_local_catalog_curate import FakeClient, projection
from test_local_catalog_evidence import fixture as real_fixture, TOKEN as REAL_TOKEN


def save(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'data-plans'
        self.root.mkdir(mode=0o700)
        self.client = FakeClient()
        self.sources = {}

    def add_run(self, token='a' * 32, result='passed', curated=False):
        path = self.root / token
        path.mkdir(mode=0o700)
        save(path / 'summary.json', {'run_token': token, 'result': result})
        save(path / 'private-log.json', {'raw_prompt': 'DO_NOT_COPY_PRIVATE_INPUT', 'password': 'DO_NOT_COPY_SECRET'})
        item = projection()
        item['run_token'] = token
        for entity in item['entities']:
            entity['name'] = entity['name'].replace('a' * 32, token)
        for edge in item['lineage']:
            for key in ('from', 'to'):
                edge[key] = edge[key].replace('a' * 32, token)
        self.sources[token] = item
        if curated:
            C.curate.apply(self.client, item)
        self.client.calls.clear()
        return path

    def collect(self, client=None, reader=None):
        return C.collect(client or self.client, source_root=self.root,
                         evidence_reader=reader or (lambda path: copy.deepcopy(self.sources[path.name])))

    def test_all_runs_missing_is_complete_observation_not_complete_coverage(self):
        self.add_run()
        result = self.collect()
        self.assertEqual(result['observation']['status'], 'complete')
        self.assertEqual(result['coverage'], {'denominator': 2, 'present': 0, 'missing': 2,
            'conflict': 0, 'unknown': 0, 'known': 2, 'rate': 0.0, 'observation_rate': 1.0, 'complete': False})
        self.assertEqual(self.client.writes, [])

    def test_present_and_missing_share_fixed_denominator_no_raw_values(self):
        self.add_run(curated=True)
        self.add_run('b' * 32)
        result = self.collect()
        self.assertEqual(result['coverage']['denominator'], 4)
        self.assertEqual((result['coverage']['present'], result['coverage']['missing'], result['coverage']['rate']), (2, 2, .5))
        self.assertEqual(result['inventory']['eligible'], 2)
        self.assertEqual(self.client.writes, [])
        serialized = json.dumps(result)
        for secret in ('DO_NOT_COPY', 'sqlQuery', 'accessToken', 'databaseSchema', 'password', 's3://'):
            self.assertNotIn(secret, serialized)

    def test_partial_lineage_counts_per_expected_edge(self):
        self.add_run(curated=True)
        pair = next(pair for pair, edge in self.client.edges.items() if 'target mart;' in edge['lineageDetails']['description'])
        del self.client.edges[pair]
        result = self.collect()
        self.assertEqual([edge['status'] for edge in result['runs'][0]['edges']], ['present', 'missing'])

    def test_human_edit_is_conflict_and_is_not_automatically_repaired(self):
        self.add_run(curated=True)
        name = self.sources['a' * 32]['entities'][1]['name']
        self.client.entities[('tables', name)]['description'] = 'human description'
        result = self.collect()
        self.assertEqual(result['coverage']['conflict'], 2)
        self.assertEqual(result['observation']['status'], 'complete')
        self.assertEqual(self.client.writes, [])

    def test_http_failure_is_unknown_not_missing_or_zero_rate(self):
        self.add_run()
        class Broken:
            def request(self, method, path):
                raise C.APIError(method, path, 503)
        result = self.collect(Broken())
        self.assertEqual((result['coverage']['unknown'], result['coverage']['missing']), (2, 0))
        self.assertIsNone(result['coverage']['rate'])
        self.assertEqual(result['coverage']['observation_rate'], 0)
        self.assertEqual(result['observation']['status'], 'incomplete')

    def test_revision_change_makes_previously_present_edges_unknown(self):
        self.add_run(curated=True)
        original, count = self.client.request, 0
        def request(method, path, data=None):
            nonlocal count
            if path.startswith('/teams/'):
                count += 1
                if count == 2:
                    self.client.entities[('teams', C.curate.TEAM)]['version'] = .2
            return original(method, path, data)
        self.client.request = request
        result = self.collect()
        self.assertEqual(result['coverage']['unknown'], 2)
        self.assertFalse(result['observation']['metadata_stable'])
        self.assertEqual(result['runs'][0]['edges'][0]['reason_code'], 'CATALOG_CHANGED')

    def test_target_created_between_reads_is_unknown_instead_of_absent(self):
        self.add_run()
        real = C.observe
        count = 0
        def observe(*args):
            nonlocal count
            count += 1
            if count == 2:
                C.curate.apply(self.client, self.sources['a' * 32])
            return real(*args)
        with patch.object(C, 'observe', observe):
            result = self.collect()
        self.assertEqual(result['coverage']['unknown'], 2)

    def test_malformed_response_does_not_become_conflict(self):
        self.add_run()
        class Malformed:
            def request(self, *_):
                return {'id': 'invalid', 'description': 'DO_NOT_COPY'}
        result = self.collect(Malformed())
        self.assertEqual(result['coverage']['unknown'], 2)
        self.assertEqual(result['coverage']['conflict'], 0)

    def test_malformed_empty_lineage_collection_is_unknown_not_missing(self):
        self.add_run(curated=True)
        original = self.client.request
        for malformed in ({}, False, '', None):
            with self.subTest(value=malformed):
                def request(method, path, data=None):
                    value = original(method, path, data)
                    if path.startswith('/lineage/'):
                        value['upstreamEdges'] = malformed
                    return value
                self.client.request = request
                result = self.collect()
                self.assertEqual(result['coverage']['unknown'], 2)
                self.assertEqual(result['coverage']['missing'], 0)

    def test_failed_invalid_incomplete_and_unrecognized_inputs_remain_visible(self):
        self.add_run('a' * 32, result='failed')
        self.add_run('b' * 32)
        self.add_run('c' * 32)
        (self.root / ('c' * 32) / 'summary.json').unlink()
        (self.root / 'not-a-uuid').mkdir()
        def reader(path):
            raise C.evidence.EvidenceError('DO_NOT_COPY_PRIVATE_FAILURE')
        result = self.collect(reader=reader)
        self.assertEqual({key: result['inventory'][key] for key in ('eligible', 'failed', 'invalid', 'incomplete', 'unrecognized_entries')},
                         {'eligible': 0, 'failed': 1, 'invalid': 1, 'incomplete': 1, 'unrecognized_entries': 1})
        self.assertEqual(result['coverage']['denominator'], 0)
        self.assertIsNone(result['coverage']['rate'])
        self.assertEqual(self.client.calls, [])
        self.assertNotIn('DO_NOT_COPY', json.dumps(result))

    def test_malformed_summary_result_is_invalid_row_not_report_exception(self):
        for result_value in ({'raw': 'private'}, ['passed'], None, 1):
            with self.subTest(result=result_value):
                if not self.sources:
                    path = self.add_run()
                save(path / 'summary.json', {'run_token': path.name, 'result': result_value})
                report = self.collect()
                self.assertEqual(report['inventory']['invalid'], 1)
                self.assertEqual(report['runs'][0]['reason_codes'], ['SOURCE_RESULT_INVALID'])

    def test_source_drift_after_eligibility_keeps_two_unknown_edges(self):
        path = self.add_run(curated=True)
        real = self.client.request
        changed = False
        def request(*args, **kwargs):
            nonlocal changed
            if not changed:
                save(path / 'private-log.json', {'changed': True})
                changed = True
            return real(*args, **kwargs)
        self.client.request = request
        result = self.collect()
        self.assertEqual((result['inventory']['eligible'], result['inventory']['incomplete']), (1, 1))
        self.assertEqual((result['coverage']['denominator'], result['coverage']['unknown']), (2, 2))
        self.assertFalse(result['observation']['source_stable'])

    def test_symlink_source_is_invalid_not_followed_and_never_eligible(self):
        path = self.add_run()
        (path / 'leak').symlink_to('/etc/passwd')
        report = self.collect()
        self.assertEqual(report['inventory']['invalid'], 1)
        self.assertEqual(report['inventory']['eligible'], 0)
        self.assertEqual(self.client.calls, [])

    def test_new_run_during_observation_marks_inventory_incomplete(self):
        self.add_run()
        real = self.client.request
        def request(*args, **kwargs):
            if 'b' * 32 not in self.sources:
                self.add_run('b' * 32)
            return real(*args, **kwargs)
        self.client.request = request
        report = self.collect()
        self.assertFalse(report['inventory']['complete'])
        self.assertEqual(report['observation']['status'], 'incomplete')
        self.assertIn('SOURCE_INVENTORY_CHANGED', report['observation']['reason_codes'])

    def test_get_only_wrapper_rejects_mutation_and_other_run_paths(self):
        self.add_run()
        observer = C.Observation(self.client, self.sources['a' * 32])
        for method, path in [('PUT', '/lineage'), ('POST', '/tables'), ('GET', '/users'),
                             ('GET', C._entity_path('tables', C.curate.SCHEMA + '.dataops_' + 'b' * 32 + '_stg', True))]:
            with self.subTest(method=method, path=path), self.assertRaises(C.CoverageError):
                observer.request(method, path)
        self.assertEqual(self.client.calls, [])

    def test_real_artifact_verifier_and_hashes_no_source_mutation(self):
        path = real_fixture(self.root, recovery=True)
        for item in [path, *path.rglob('*')]:
            item.chmod(0o700 if item.is_dir() else 0o600)
        before = C.source_snapshot(path)
        with patch.object(C.evidence, 'EVIDENCE_ROOT', self.root):
            report = C.collect(self.client, source_root=self.root)
        self.assertEqual((report['inventory']['valid'], report['coverage']['denominator']), (1, 2))
        self.assertEqual(C.source_snapshot(path), before)
        self.assertEqual(report['runs'][0]['run_token'], REAL_TOKEN)

    def test_empty_inventory_never_authenticates(self):
        report = self.collect()
        self.assertEqual(report['observation']['status'], 'complete')
        self.assertEqual(report['coverage']['denominator'], 0)
        self.assertFalse(report['coverage']['complete'])
        self.assertEqual(self.client.calls, [])

    def test_private_json_and_html_output(self):
        self.add_run()
        report = self.collect()
        with patch.object(C, 'OUTPUT_ROOT', Path(self.tmp.name) / 'reports'):
            output = C.write_report(report)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        for name in ('report.json', 'report.html'):
            self.assertEqual(stat.S_IMODE((output / name).stat().st_mode), 0o600)
        self.assertEqual(json.loads((output / 'report.json').read_text()), report)
        self.assertIn('<html', (output / 'report.html').read_text())

    def test_output_root_swap_never_writes_report_to_other_tree(self):
        self.add_run()
        report = self.collect()
        root = Path(self.tmp.name) / 'reports'
        original = Path(self.tmp.name) / 'original-reports'
        other = Path(self.tmp.name) / 'other'
        other.mkdir(mode=0o700)
        real_directory = C.directory
        swapped = False

        def swap_after_validation(path, *, private=True):
            nonlocal swapped
            info = real_directory(path, private=private)
            if path == root and not swapped:
                swapped = True
                root.rename(original)
                root.symlink_to(other, target_is_directory=True)
            return info

        with patch.object(C, 'OUTPUT_ROOT', root), patch.object(C, 'directory', swap_after_validation):
            with self.assertRaises((C.CoverageError, OSError)):
                C.write_report(report)
        self.assertTrue(swapped)
        runs = list(original.iterdir())
        self.assertEqual(len(runs), 1, 'pinned original did not receive the report')
        self.assertEqual(json.loads((runs[0] / 'report.json').read_text()), report)
        self.assertEqual(list(other.iterdir()), [], 'report escaped to a substituted tree')

    def test_report_can_create_missing_private_checkout_parents_without_symlinks(self):
        self.add_run()
        report = self.collect()
        project = Path(self.tmp.name) / 'project'
        project.mkdir()
        root = project / 'tmp/local-experiment/lineage-coverage'
        with patch.object(C, 'ROOT', project), patch.object(C, 'OUTPUT_ROOT', root):
            output = C.write_report(report)
        self.assertEqual(json.loads((output / 'report.json').read_text()), report)
        for path in (project / 'tmp', project / 'tmp/local-experiment', root, output):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)

    def test_report_rejects_symlink_in_checkout_parent_without_foreign_write(self):
        self.add_run()
        report = self.collect()
        project = Path(self.tmp.name) / 'project'
        project.mkdir()
        foreign = Path(self.tmp.name) / 'foreign'
        foreign.mkdir()
        (project / 'tmp').symlink_to(foreign, target_is_directory=True)
        with patch.object(C, 'ROOT', project), patch.object(C, 'OUTPUT_ROOT', project / 'tmp/local-experiment/lineage-coverage'):
            with self.assertRaises((C.CoverageError, OSError)):
                C.write_report(report)
        self.assertEqual(list(foreign.iterdir()), [])


class Response(io.BytesIO):
    status = 200


class ReadOnlyClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.state.chmod(0o700)
        self.env = self.state / '.env'
        self.env.write_text('OM_STATE=' + str(self.state) + '\nOM_DB_PASSWORD=db\nOM_POSTGRES_PASSWORD=pg\n'
                            'OM_ADMIN_PASSWORD=OnlyAFakePassword123!\nOM_FERNET_KEY=fernet\n')
        self.env.chmod(0o600)
        self.calls = []
        owner = self
        class Opener:
            def open(self, request, timeout):
                owner.calls.append((request, timeout))
                return Response(json.dumps({'accessToken': 'header.payload.signature'}
                                           if request.method == 'POST' else {'version': .1}).encode())
        self.client = C.ReadOnlyClient(opener=Opener(), state=self.state)

    def test_single_login_only_existing_password_and_no_touch(self):
        before = self.env.stat()
        self.client.request('GET', next(iter(C.ANCHORS)))
        self.client.request('GET', next(iter(C.ANCHORS)))
        self.assertEqual([call[0].method for call in self.calls], ['POST', 'GET', 'GET'])
        self.assertEqual(self.calls[0][0].full_url, C.URL + '/users/login')
        self.assertTrue(all(0 < timeout <= 10 for _, timeout in self.calls))
        self.assertEqual(self.env.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual([p.name for p in self.state.iterdir()], ['.env'])

    def test_failed_login_is_not_retried_or_defaulted_and_http_error_is_closed(self):
        stream = io.BytesIO(b'PRIVATE_ERROR')
        error = urllib.error.HTTPError(C.URL + '/users/login', 401, 'failed', {}, stream)
        with patch.object(self.client.opener, 'open', side_effect=error) as opened:
            for _ in range(2):
                with self.assertRaisesRegex(C.CoverageError, 'LOGIN_UNAVAILABLE'):
                    self.client.request('GET', next(iter(C.ANCHORS)))
        self.assertEqual(opened.call_count, 1)
        self.assertTrue(stream.closed)
        self.assertFalse((self.state / 'admin-initialized').exists())

    def test_invalid_credentials_privacy_or_duplicates_prevent_network(self):
        for alteration in ('mode', 'duplicate', 'symlink'):
            with self.subTest(alteration=alteration):
                original = self.env.read_bytes()
                if alteration == 'mode':
                    self.env.chmod(0o644)
                elif alteration == 'duplicate':
                    self.env.write_bytes(original + b'OM_ADMIN_PASSWORD=duplicate\n')
                else:
                    self.env.rename(self.state / 'hidden')
                    self.env.symlink_to(self.state / 'hidden')
                client = C.ReadOnlyClient(opener=self.client.opener, state=self.state)
                with self.assertRaises(C.CoverageError):
                    client.request('GET', next(iter(C.ANCHORS)))
                if self.env.is_symlink():
                    self.env.unlink()
                    (self.state / 'hidden').rename(self.env)
                self.env.write_bytes(original)
                self.env.chmod(0o600)
        self.assertEqual(self.calls, [])

    def test_http_boundary_rejects_before_login(self):
        for method, path in [('POST', '/users/login'), ('PUT', '/lineage'), ('GET', '//attacker'),
                             ('GET', '/users/changePassword'), ('GET', '/tables?limit=100')]:
            with self.subTest(method=method, path=path), self.assertRaises(C.CoverageError):
                self.client.request(method, path)
        self.assertEqual(self.calls, [])

    def test_malformed_and_oversized_json_are_rejected(self):
        self.client.login_attempted, self.client.token = True, 'token'
        for body in (b'{"x":1,"x":2}', b'{"x":NaN}', b'x' * (C.MAX_RESPONSE + 1)):
            with self.subTest(size=len(body)), patch.object(self.client.opener, 'open', return_value=Response(body)):
                with self.assertRaises(C.CoverageError):
                    self.client.request('GET', next(iter(C.ANCHORS)))

    def test_no_proxy_redirect_and_deadline_are_enforced(self):
        client = C.ReadOnlyClient(state=self.state)
        self.assertTrue(any(isinstance(item, C.NoRedirect) for item in client.opener.handlers))
        self.assertFalse(any(getattr(item, 'proxies', None) for item in client.opener.handlers))
        self.client.login_attempted, self.client.token = True, 'token'
        self.client.deadline = 0
        with self.assertRaisesRegex(C.CoverageError, 'API_DEADLINE'):
            self.client.request('GET', next(iter(C.ANCHORS)))
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
