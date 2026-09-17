"""Fresh owned table preparation and bounded branch-to-main publication.

Public sequence:
  HostTable(token, worker, control, admin, progress, mode='positive'|'negative')
  prepare() -> sealed; guard_seal() -> sealed; mark_spark_pending() -> None
  verify_written(spark_output) -> verified branch proof
  positive: plan_publish(snapshot_b) -> plan; publish(plan) -> commit proof
  negative: negative_main_append() -> snapshot_d/metadata/rows/data_files;
            plan_publish(snapshot_b), plan_publish(snapshot_d) -> local refusals;
            verify_negative() -> independent Trino rows/files/metadata proof
  cleanup(writers_stopped=True, queries_drained=True) -> owned cleanup proof

No user SQL, snapshot, ref name, endpoint or path is accepted by the workflow.
The exposed snapshot argument must be one of this run's sealed fixed steps.
Publication has no crash-resume/retry: a lost result keeps pending and prevents
any later mutation or DROP. A plan is bound to this in-memory owner and journal.
"""
from copy import deepcopy
import uuid

from tools.local_experiment.spark_branch import table as B
from tools.local_experiment.spark_publish import publish as P
from tools.local_experiment.spark_publish import contract as C
from tools.local_experiment.trino_opa.common import digest

TableError, require, same, sid = B.TableError, B.require, B.same, B.sid
PublishAdmin = P.PublishAdmin
names, fixture_snapshot = B.names, B.fixture_snapshot
REFS, validate_files, rows = B.REFS, B.validate_files, B.rows


class HostTable(B.HostTable):
    def __init__(self, token, worker, control, admin, progress=lambda: None, *, mode='positive'):
        require(type(mode) is str and mode in ('positive', 'negative'), 'PUBLISH_MODE_INVALID')
        super().__init__(token, worker, control, admin, progress)
        require(callable(getattr(admin, 'commit_publish', None)), 'PUBLISH_ADMIN_INVALID')
        self.mode = mode
        self._publish_inputs = {}
        self.journal.update(mode=mode, branch_verified=None, negative_main=None,
                            negative_attempted=False, publish_plans=[], publish_operations=[],
                            publish_verification=None)

    def load(self):
        original = self.journal['identity']
        value = P.project(self.admin.request('GET', self.scope['path']), self.token,
                          original['table_uuid'] if original else None)
        if original:
            require(value['structure_sha256'] == original['structure_sha256'], 'TABLE_STRUCTURE_CHANGED')
        return value

    def _set_ref(self, before, name):
        require(name in REFS and name not in before['refs'] and not self.journal['uncertain']
                and self.journal['pending'] is None and len(self.journal['ref_commits']) < 2, 'REF_MUTATION_FORBIDDEN')
        require(same(self.guard(), before), 'STALE_METADATA_BEFORE_REF')
        body = {'requirements': [{'type': 'assert-table-uuid', 'uuid': before['table_uuid']},
            {'type': 'assert-ref-snapshot-id', 'ref': 'main', 'snapshot-id': before['snapshot_id']},
            {'type': 'assert-ref-snapshot-id', 'ref': name, 'snapshot-id': None}],
            'updates': [{'action': 'set-snapshot-ref', 'ref-name': name, 'type': REFS[name], 'snapshot-id': before['snapshot_id']}]}
        B.validate_ref_body(body, self.token)
        op = {'operation_id': str(uuid.uuid4()), 'ref_name': name, 'request_sha256': digest(body),
              'before_metadata_sha256': before['metadata_sha256'], 'outcome': 'not-sent', 'http_status': None}
        self.journal['ref_commits'].append(op)
        self.progress()
        self.journal.update(pending=op['operation_id'], uncertain=True)
        op['outcome'] = 'pending'
        try:
            self.progress()
        except BaseException:
            self.journal.update(pending=None, uncertain=False)
            op['outcome'] = 'not-sent'
            raise
        try:
            result = self.admin.commit_reference(body)
            op['http_status'] = 200
            committed = P.project(result, self.token, before['table_uuid'])
            expected = {**before['refs'], name: {'type': REFS[name], 'snapshot_id': before['snapshot_id']}}
            require(committed['refs'] == expected and committed['stable_metadata_sha256'] == before['stable_metadata_sha256'],
                    'REF_CHANGED_NON_REFERENCE_METADATA')
            after = self.guard()
            require(same(after, committed), 'REF_READBACK_DIFFER')
            op.update(outcome='verified', after_metadata_sha256=after['metadata_sha256'])
            self.journal.update(pending=None, uncertain=False)
            self.progress()
            return after
        except BaseException as error:
            rejected = getattr(error, 'outcome', None) == 'rejected' and getattr(error, 'http_status', None) in (400, 401, 403, 404, 405, 409)
            op.update(outcome='rejected' if rejected else 'unknown', http_status=getattr(error, 'http_status', None) or op['http_status'])
            self.journal.update(pending=None if rejected else op['operation_id'], uncertain=not rejected)
            try:
                self._save_preserving(error)
            except BaseException:
                self.journal.update(pending=op['operation_id'], uncertain=True)
                op['outcome'] = 'unknown'
                raise
            if not isinstance(error, Exception):
                raise
            raise TableError('REF_COMMIT_REJECTED' if rejected else 'REF_COMMIT_UNKNOWN') from None

    def prepare(self):
        require(not self.journal['prepare_attempted'], 'PREPARE_RETRY_FORBIDDEN')
        self.journal['prepare_attempted'] = True
        self.progress()
        empty = self.create()
        a = self._append('append_a', C.ROWS_A, C.ROWS_A, empty)
        files_a = self.files('files_a', 2)
        require(len(files_a) == next(s['file_count'] for s in a['snapshots'] if s['snapshot_id'] == a['snapshot_id']),
                'DATA_FILE_COUNT_DIFFER')
        tagged = self._set_ref(a, 'baseline_tag')
        require(self.files('files_after_tag', 2) == files_a, 'REF_CHANGED_DATA_FILES')
        b = self._append('append_b', [C.MAIN_ROW], C.ROWS_B, tagged)
        files_b = self.files('files_b', 3)
        C.append_files(files_a, files_b)
        branch = self._set_ref(b, 'audit_branch')
        require(self.files('files_after_branch', 3) == files_b, 'REF_CHANGED_DATA_FILES')
        require(same(self.guard(), branch), 'PREPARE_METADATA_CHANGED')
        seal = {'identity': {key: branch[key] for key in ('table_uuid', 'table_location', 'marker', 'schema_sha256',
                'metadata_location', 'metadata_sha256')}, 'initial_snapshot_id': empty['snapshot_id'],
                'snapshot_a': a['snapshot_id'], 'snapshot_b': b['snapshot_id'], 'refs': branch['refs'],
                'snapshots': branch['snapshots'], 'data_files': {'a': files_a, 'b': files_b}}
        C.validate_sealed(seal, {'table_uuid': branch['table_uuid'], 'table_location': branch['table_location'],
                                'initial_snapshot_id': b['snapshot_id']}, self.token)
        self.journal['sealed'] = seal
        self._inspect_sql_metadata(branch)
        for label, ref, expected in [('tag_before_spark', 'baseline_tag', C.ROWS_A),
                                     ('branch_before_spark', 'audit_branch', C.ROWS_B),
                                     ('snapshot_a_before_spark', a['snapshot_id'], C.ROWS_A),
                                     ('snapshot_b_before_spark', b['snapshot_id'], C.ROWS_B)]:
            self._read_rows(label, ref, expected)
        self.guard_seal()
        self.progress()
        return deepcopy(seal)

    def verify_written(self, spark):
        require(self.journal['spark_outcome'] == 'unknown' and self.journal['pending'] == 'spark_branch_append',
                'SPARK_NOT_PENDING')
        require(type(spark) is dict and spark.get('result') == 'passed' and spark.get('write_outcome') == 'verified'
                and type(spark.get('write_attempts')) is int and spark['write_attempts'] == 1, 'SPARK_WRITE_UNCONFIRMED')
        seal = self.journal['sealed']
        C.validate_before(spark.get('before'), seal)
        C.validate_after(spark.get('table_identity'), spark.get('snapshot_summaries'), spark.get('snapshot_files'), seal)
        current = self.guard()
        a, b = seal['snapshot_a'], seal['snapshot_b']
        require(set(current['refs']) == {'main', *REFS}
                and current['refs']['main'] == {'type': 'branch', 'snapshot_id': b}
                and current['refs']['baseline_tag'] == {'type': 'tag', 'snapshot_id': a}, 'MAIN_OR_TAG_CHANGED')
        c = sid(current['refs']['audit_branch']['snapshot_id'])
        added = [r for r in current['snapshots'] if r['snapshot_id'] == c]
        require(c not in {r['snapshot_id'] for r in seal['snapshots']}
                and len(current['snapshots']) == len(seal['snapshots']) + 1 and len(added) == 1
                and all(r in current['snapshots'] for r in seal['snapshots'])
                and added[0]['parent_snapshot_id'] == b and added[0]['record_count'] == 4
                and added[0]['sequence_number'] > max(r['sequence_number'] for r in seal['snapshots']),
                'BRANCH_DESCENDANT_INVALID')
        expected_identity = {'table_uuid': current['table_uuid'], 'table_location': current['table_location'],
            'marker': current['marker'], 'metadata_location': current['metadata_location'], 'main_snapshot_id': b,
            'tag_snapshot_id': a, 'branch_snapshot_id': c, 'parent_snapshot_id': b}
        summaries = [{k: v for k, v in r.items() if k != 'sha256'} for r in current['snapshots']]
        require(same(spark['table_identity'], expected_identity)
                and same(spark['snapshot_summaries'], summaries), 'SPARK_TABLE_IDENTITY_DIFFER')
        wanted = {'main': C.ROWS_B, 'baseline_tag': C.ROWS_A, 'audit_branch': C.ROWS_C}
        require(same(spark.get('dataframe_rows'), wanted) and same(spark.get('sql_rows'), wanted), 'SPARK_ROWS_DIFFER')
        self._inspect_sql_metadata(current)
        observed = self._all_rows('branch_verified', C.ROWS_B, c)
        inventories = spark['snapshot_files']
        main_files = self.files('main_files_after_spark', 3)
        require(same(main_files, seal['data_files']['b']) and same(inventories['b'], main_files)
                and same(inventories['a'], seal['data_files']['a']), 'MAIN_FILES_CHANGED')
        validate_files(inventories['c'], self.scope['location'], 4)
        C.append_files(main_files, inventories['c'])
        require(len(inventories['c']) == added[0]['file_count'], 'BRANCH_FILE_COUNT_DIFFER')
        require(same(self.guard(), current), 'POST_VERIFY_METADATA_CHANGED')
        verified = {'verified': True, 'identity': expected_identity, 'metadata': current,
                    'rows': observed, 'snapshot_files': deepcopy(inventories),
                    'main_files_unchanged': True, 'parent_verified': True}
        self.journal.update(branch_verified=verified, spark_outcome='verified', pending=None, uncertain=False)
        try:
            self.progress()
        except BaseException:
            self.journal.update(spark_outcome='unknown', pending='spark_branch_append', uncertain=True)
            raise
        return deepcopy(verified)

    def _all_rows(self, prefix, main_rows, c, d=None):
        seal = self.journal['sealed']
        targets = [('main', None, main_rows), ('baseline_tag', 'baseline_tag', C.ROWS_A),
                   ('audit_branch', 'audit_branch', C.ROWS_C), ('snapshot_a', seal['snapshot_a'], C.ROWS_A),
                   ('snapshot_b', seal['snapshot_b'], C.ROWS_B), ('snapshot_c', c, C.ROWS_C)]
        if d is not None:
            targets.append(('snapshot_d', d, C.ROWS_D))
        return {label: self._read_rows(prefix + '_' + label, ref, oracle) for label, ref, oracle in targets}

    def _known_source(self):
        require(self.journal['spark_outcome'] == 'verified' and self.journal['branch_verified'] is not None
                and not self.journal['uncertain'] and self.journal['pending'] is None, 'PUBLISH_SOURCE_UNAVAILABLE')
        return (self.journal['negative_main'] or self.journal['branch_verified'])['metadata']

    def _source_guard(self, expected):
        current = self.guard()
        if not same(current, expected):
            self.journal.update(uncertain=True, pending='publish_source_changed')
            self.progress()
            raise TableError('PUBLISH_SOURCE_CHANGED')
        return current

    def negative_main_append(self):
        require(self.mode == 'negative' and not self.journal['negative_attempted']
                and not self.journal['publish_plans'], 'NEGATIVE_APPEND_FORBIDDEN')
        before = self._source_guard(self._known_source())
        self.journal['negative_attempted'] = True
        self.progress()
        try:
            after = self._append('append_divergent_main', [C.CONCURRENT_ROW], C.ROWS_D, before)
            d = after['snapshot_id']
            require(d not in {r['snapshot_id'] for r in before['snapshots']}
                    and next(r for r in after['snapshots'] if r['snapshot_id'] == d)['parent_snapshot_id']
                        == self.journal['sealed']['snapshot_b'], 'NEGATIVE_PARENT_INVALID')
            files = self.files('negative_main_files', 4)
            C.append_files(self.journal['sealed']['data_files']['b'], files)
            c_files = self.journal['branch_verified']['snapshot_files']['c']
            old_paths = {r['path'] for r in self.journal['sealed']['data_files']['b']}
            require(({r['path'] for r in files} - old_paths).isdisjoint(
                    {r['path'] for r in c_files} - old_paths), 'NEGATIVE_FILES_NOT_INDEPENDENT')
            require(same(self.guard(), after), 'NEGATIVE_METADATA_CHANGED')
            value = {'snapshot_d': d, 'metadata': after, 'rows': deepcopy(C.ROWS_D), 'data_files': files}
            self.journal['negative_main'] = value
            self.progress()
            return deepcopy(value)
        except BaseException as error:
            self.journal.update(uncertain=True, pending='negative_append_unknown')
            self._save_preserving(error)
            raise

    def plan_publish(self, expected_main):
        """Two fixed negative checks or one positive plan; no mutation is sent."""
        before = self._source_guard(self._known_source())
        plans = self.journal['publish_plans']
        seal = self.journal['sealed']
        if self.mode == 'negative':
            require(self.journal['negative_main'] is not None and len(plans) < 2, 'PUBLISH_PLAN_SEQUENCE_INVALID')
            allowed = seal['snapshot_b'] if not plans else self.journal['negative_main']['snapshot_d']
        else:
            require(not plans, 'PUBLISH_PLAN_SEQUENCE_INVALID')
            allowed = seal['snapshot_b']
        require(type(expected_main) is int and expected_main == allowed, 'PUBLISH_EXPECTATION_INVALID')
        c = self.journal['branch_verified']['identity']['branch_snapshot_id']
        reason, chain, body = None, None, None
        if before['snapshot_id'] != expected_main:
            reason = 'STALE_MAIN'
        else:
            chain = P.ancestry(before, expected_main, c)
            if chain is None:
                reason = 'MAIN_NOT_ANCESTOR'
            else:
                body = P.commit_body(before, expected_main=expected_main, branch=c, tag=seal['snapshot_a'])
                P.validate_body(body, self.token)
        operation_id = str(uuid.uuid4())
        value = {'outcome': 'rejected' if reason else 'ready', 'reason': reason, 'operation_id': operation_id,
                 'expected_main': expected_main, 'target_snapshot': c, 'ancestry': chain,
                 'before_metadata_sha256': before['metadata_sha256'],
                 'body_sha256': digest(body) if body is not None else None}
        plans.append(value)
        self.progress()
        if body is not None:
            self._publish_inputs[operation_id] = (deepcopy(value), deepcopy(before), deepcopy(body))
        return deepcopy(value)

    def publish(self, plan):
        """One durable conditional POST, followed by metadata and Trino proof.

        HTTP 409 is a rejection only when the entire pre-commit metadata is still
        observed. All other uncertain outcomes retain pending, even if a later
        read happens to show the target snapshot. There is no automatic adoption.
        """
        require(self.mode == 'positive' and not self.journal['publish_operations']
                and type(plan) is dict and plan.get('operation_id') in self._publish_inputs,
                'PUBLISH_RETRY_OR_PLAN_INVALID')
        saved, before, body = self._publish_inputs[plan['operation_id']]
        require(same(plan, saved) and same(self.journal['publish_plans'], [saved]), 'PUBLISH_PLAN_CHANGED')
        self._source_guard(self._known_source())
        self._source_guard(before)
        operation = {**deepcopy(saved), 'outcome': 'not-sent', 'http_status': None,
                     'after_metadata_sha256': None, 'metadata_only': False}
        self.journal['publish_operations'].append(operation)
        self.progress()
        self.journal.update(pending=operation['operation_id'], uncertain=True)
        operation['outcome'] = 'pending'
        try:
            self.progress()
        except BaseException:
            operation['outcome'] = 'not-sent'
            self.journal.update(pending=None, uncertain=False)
            raise
        try:
            response = self.admin.commit_publish(body)
            operation['http_status'] = 200
            committed = P.project(response, self.token, before['table_uuid'])
            P.verify_transition(before, committed, body)
            current = self.guard()
            P.verify_transition(before, current, body)
            require(same(current, committed), 'PUBLISH_READBACK_DIFFER')
            self._inspect_sql_metadata(current)
            observed = self._all_rows('published', C.ROWS_C, plan['target_snapshot'])
            files = self.files('published_main_files', 4)
            require(same(files, self.journal['branch_verified']['snapshot_files']['c']), 'PUBLISH_FILES_CHANGED')
            require(same(self.guard(), current), 'PUBLISH_POST_VERIFY_CHANGED')
            verification = {'verified': True, 'main_snapshot_id': current['snapshot_id'],
                'branch_snapshot_id': plan['target_snapshot'], 'tag_snapshot_id': self.journal['sealed']['snapshot_a'],
                'metadata': current, 'rows': observed,
                'snapshot_files': deepcopy(self.journal['branch_verified']['snapshot_files']),
                'metadata_only': True, 'new_snapshots': 0, 'post_count': 1}
            self.journal['publish_verification'] = verification
            operation.update(outcome='verified', after_metadata_sha256=current['metadata_sha256'], metadata_only=True)
            self.journal.update(pending=None, uncertain=False)
            self.progress()
            return deepcopy(operation) | {'verification': deepcopy(verification)}
        except BaseException as error:
            rejected = False
            operation['http_status'] = getattr(error, 'http_status', None) or operation['http_status']
            if getattr(error, 'outcome', None) == 'rejected' and getattr(error, 'http_status', None) == 409:
                try:
                    rejected = same(self.guard(), before)
                except BaseException as read_error:
                    if not isinstance(read_error, Exception):
                        # The readback can be the first cancellation. Preserve it
                        # through the unknown receipt instead of hiding it behind 409.
                        error = read_error
            operation.update(outcome='rejected' if rejected else 'unknown',
                             metadata_only=False)
            self.journal.update(pending=None if rejected else operation['operation_id'], uncertain=not rejected)
            try:
                self._save_preserving(error)
            except BaseException:
                operation['outcome'] = 'unknown'
                self.journal.update(pending=operation['operation_id'], uncertain=True)
                raise
            if not isinstance(error, Exception):
                raise error
            if rejected:
                return deepcopy(operation)
            raise TableError('PUBLISH_COMMIT_UNKNOWN') from None

    def verify_negative(self):
        require(self.mode == 'negative' and self.journal['negative_main'] is not None
                and not self.journal['publish_operations'], 'NEGATIVE_VERIFY_SEQUENCE_INVALID')
        current = self._source_guard(self._known_source())
        plans = self.journal['publish_plans']
        require(len(plans) == 2 and [r['reason'] for r in plans] == ['STALE_MAIN', 'MAIN_NOT_ANCESTOR']
                and all(r['outcome'] == 'rejected' and r['body_sha256'] is None
                        and r['before_metadata_sha256'] == current['metadata_sha256'] for r in plans),
                'NEGATIVE_REJECTIONS_UNVERIFIED')
        c = self.journal['branch_verified']['identity']['branch_snapshot_id']
        d = self.journal['negative_main']['snapshot_d']
        self._inspect_sql_metadata(current)
        observed = self._all_rows('negative', C.ROWS_D, c, d)
        files = self.files('negative_unchanged_files', 4)
        require(same(files, self.journal['negative_main']['data_files']), 'NEGATIVE_FILES_CHANGED')
        require(same(self.guard(), current), 'NEGATIVE_POST_VERIFY_CHANGED')
        value = {'verified': True, 'main_snapshot_id': d, 'branch_snapshot_id': c,
                 'tag_snapshot_id': self.journal['sealed']['snapshot_a'],
                 'rejections': [r['reason'] for r in plans], 'post_count': 0, 'rows': observed,
                 'snapshot_files': {**deepcopy(self.journal['branch_verified']['snapshot_files']), 'd': files},
                 'main_files_unchanged': True}
        self.journal['publish_verification'] = value
        self.progress()
        return deepcopy(value)
