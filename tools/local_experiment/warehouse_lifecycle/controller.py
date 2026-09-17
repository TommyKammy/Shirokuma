"""Synchronous fixed XS chill/wake state machine; Kubernetes is host-owned.

The root runner holds the experiment lock and is the only producer. Unknown
requests, stale observations and unconsumed results never authorize scale-down.
"""
import copy
import time
import uuid

from tools.local_experiment.trino_opa.common import ExperimentError, require
from tools.local_experiment.gateway_warehouse.client import stamp

QUIET_SECONDS = 2
FRESH_SECONDS = 2


def identity(value, *, replicas=None):
    require(type(value) is dict and {'deployment_uid', 'pod_uid', 'replicas'} <= set(value)
            and type(value['replicas']) is int and value['replicas'] in {0, 1}, 'RUNTIME_IDENTITY_INVALID')
    result = {key: value[key] for key in ('deployment_uid', 'pod_uid', 'replicas')}
    require(replicas is None or result['replicas'] == replicas, 'RUNTIME_REPLICAS_MISMATCH')
    for key in ('deployment_uid', 'pod_uid'):
        if key == 'pod_uid' and result['replicas'] == 0:
            require(result[key] is None, 'RUNTIME_IDENTITY_INVALID')
            continue
        try:
            parsed = uuid.UUID(result[key])
        except (ValueError, TypeError, AttributeError):
            raise ExperimentError('RUNTIME_IDENTITY_INVALID') from None
        require(str(parsed) == result[key], 'RUNTIME_IDENTITY_INVALID')
    return result


def idle_decision(snapshot, *, now, epoch):
    """Unknown is distinct from busy; monotonic freshness is process-local."""
    if snapshot.get('result') != 'observed' or snapshot.get('ledger', {}).get('epoch') != epoch:
        return 'unknown'
    age = now - snapshot['observed_monotonic']
    if not 0 <= age <= FRESH_SECONDS:
        return 'unknown'
    ledger = snapshot['ledger']
    if ledger['uncaptured_requests']:
        return 'unknown'
    if ledger['pending_handles'] or not ledger['all_results_consumed'] or snapshot['nonterminal_count']:
        return 'busy'
    if snapshot['quiet_elapsed_seconds'] < QUIET_SECONDS:
        return 'busy'
    return 'idle'


class Controller:
    def __init__(self, client, *, guard, scale, reconnect, progress=None, decision=None):
        require(all(callable(value) for value in (guard, scale, reconnect))
                and (progress is None or callable(progress)) and (decision is None or callable(decision)),
                'CONTROLLER_CALLBACK_INVALID')
        self.client = client
        self.guard, self.scale, self.reconnect = guard, scale, reconnect
        self.progress, self.decision = progress, decision
        self.expected = None
        self.stage = None
        self.phase_started = None
        self.decision_attempted = False
        self.last_snapshot = None
        self.journal = {'schema_version': 1, 'run_token': client.run_token, 'result': 'pending',
                        'pending': None, 'identity': None, 'observations': [], 'decisions': [],
                        'busy_defer_verified': False, 'chill_verified': False, 'wake_verified': False,
                        'scale_operations': [], 'no_query_resume_claim': True,
                        'identity_scope': 'synthetic-http-headers-not-authentication',
                        'quiet_seconds': QUIET_SECONDS, 'freshness_seconds': FRESH_SECONDS}
        self.client.progress = lambda _: self.notify()

    def receipt(self):
        return {**copy.deepcopy(self.journal), 'client': self.client.receipt()}

    def notify(self):
        if self.progress is not None:
            self.progress(self.receipt())

    def checked_guard(self):
        value = self.guard()
        require(type(value) is dict and {'xs', 's'} <= set(value), 'RUNTIME_SNAPSHOT_INVALID')
        current = {size: identity(value[size]) for size in ('xs', 's')}
        if self.expected is None:
            require(all(row['replicas'] == 1 for row in current.values()), 'RUNTIME_INITIAL_REPLICAS_INVALID')
            self.expected = copy.deepcopy(current)
            self.journal['identity'] = {'initial': copy.deepcopy(current)}
        else:
            require(current == self.expected, 'RUNTIME_IDENTITY_CHANGED')
        return current

    def observe(self):
        started = time.monotonic()
        try:
            before = self.checked_guard()
            queries = self.client.queries('xs')
            # Freshness begins at the HTTP observation, not after a potentially
            # slow Kubernetes identity check.
            observed = queries['observed_monotonic']
            after = self.checked_guard()
            require(before == after, 'RUNTIME_IDENTITY_CHANGED')
            snapshot = {'result': 'observed', 'observed_at': stamp(), 'observed_monotonic': observed,
                        'duration_ms': max(0, int((time.monotonic() - started) * 1000)), 'identity': after['xs'],
                        'queries': queries, 'nonterminal_count': queries['nonterminal_count'],
                        'ledger': self.client.ledger(),
                        'quiet_elapsed_seconds': max(0, observed - self.client.last_activity),
                        'backend_active': self.client.active['xs']}
        except Exception as error:
            snapshot = {'result': 'unknown', 'observed_at': stamp(), 'observed_monotonic': time.monotonic(),
                        'failure_code': str(error) if isinstance(error, ExperimentError) else 'OBSERVATION_FAILED',
                        'nonterminal_count': None, 'ledger': self.client.ledger(), 'backend_active': None}
        self.last_snapshot = snapshot
        self.journal['observations'].append(copy.deepcopy(snapshot))
        self.notify()
        return snapshot

    def quiet(self):
        # Two complete observations, separated by the whole quiet interval. The
        # second is taken after the wait, not inferred from the first zero.
        wait = max(0, self.client.last_activity + QUIET_SECONDS - time.monotonic())
        if wait:
            time.sleep(wait)
        first = self.observe()
        require(idle_decision(first, now=time.monotonic(), epoch=self.client.epoch) == 'idle', 'WAREHOUSE_NOT_IDLE')
        epoch = self.client.epoch
        time.sleep(QUIET_SECONDS)
        second = self.observe()
        require(self.client.epoch == epoch and second.get('identity') == first.get('identity')
                and idle_decision(second, now=time.monotonic(), epoch=self.client.epoch) == 'idle', 'WAREHOUSE_NOT_IDLE')
        self.client.require_seen_terminal(second['queries'], ('xs_before', 'xs_hold'))
        return second

    def phase(self, name):
        self.stage, self.phase_started, self.decision_attempted = name, time.monotonic(), False
        self.journal['pending'] = {'operation': name}
        self.notify()

    def record_decision(self, status, reason, *, before, after, old_uid, new_uid):
        ledger = (self.last_snapshot['ledger'] if status == 'blocked' and self.stage == 'busy-defer'
                  else self.client.ledger())
        projection = {'warehouse': 'XS', 'reason': reason,
                      'active_queries': self.last_snapshot.get('nonterminal_count')
                          if self.last_snapshot and self.stage != 'wake' else None,
                      'pending_posts': ledger['uncaptured_requests'], 'open_handles': ledger['pending_handles'],
                      'replicas_before': before, 'replicas_after': after,
                      'old_pod_uid': old_uid, 'new_pod_uid': new_uid,
                      'backend_active': self.client.active['xs'] if self.client.journal['pending'] is None else None}
        duration = max(0, int((time.monotonic() - self.phase_started) * 1000))
        self.decision_attempted = True
        record = {'action': self.stage, 'status': status, 'projection': projection, 'duration_ms': duration, 'delivery': 'pending'}
        self.journal['decisions'].append(record)
        self.notify()
        if self.decision is not None:
            self.decision(self.stage, status, copy.deepcopy(projection), duration)
        record['delivery'] = 'verified' if self.decision is not None else 'not-enabled'
        self.journal['pending'] = None
        self.notify()

    def scale_xs(self, replicas):
        self.checked_guard()
        previous = self.expected['xs']
        attempt = {'size': 'xs', 'from_replicas': previous['replicas'], 'to_replicas': replicas,
                   'result': 'pending', 'before': copy.deepcopy(previous)}
        self.journal['scale_operations'].append(attempt)
        self.journal['pending'] = {'operation': 'scale', 'size': 'xs', 'replicas': replicas}
        self.notify()
        if replicas == 0:
            require(not self.client.active['xs'] and self.last_snapshot is not None
                    and self.last_snapshot['backend_active'] is False
                    and idle_decision(self.last_snapshot, now=time.monotonic(), epoch=self.client.epoch) == 'idle',
                    'SCALE_WITHOUT_FRESH_DRAIN')
        result = identity(self.scale('xs', replicas), replicas=replicas)
        require(result['deployment_uid'] == previous['deployment_uid'], 'SCALE_DEPLOYMENT_CHANGED')
        if replicas == 1:
            require(result['pod_uid'] != self.journal['identity']['initial']['xs']['pod_uid'], 'WAKE_POD_NOT_REPLACED')
        self.expected['xs'] = result
        self.checked_guard()
        attempt.update(result='verified', after=copy.deepcopy(result))
        self.journal['pending'] = None
        self.notify()
        return result

    def _failed(self, error):
        self.journal['result'] = 'failed' if isinstance(error, Exception) else 'interrupted'
        self.journal['failure_code'] = str(error) if isinstance(error, ExperimentError) else 'LIFECYCLE_FAILED'
        if self.stage and not self.decision_attempted:
            try:
                initial = (self.journal['identity'] or {}).get('initial', {}).get('xs', {})
                self.record_decision('failed', 'verification-failed' if isinstance(error, Exception) else 'interrupted',
                    before=0 if self.stage == 'wake' else 1, after=None, old_uid=initial.get('pod_uid'), new_uid=None)
            except BaseException:
                pass  # Preserve the original cancellation or transport failure.
        try:
            self.notify()
        except BaseException:
            pass

    def prepare(self):
        """Run the unchanged registration and held-query proof, then wait for CLI."""
        require(self.journal['result'] == 'pending', 'CONTROLLER_ALREADY_RUN')
        self.journal['result'] = 'running'
        try:
            initial = self.checked_guard()
            self.client.register_backends(guard=self.checked_guard)
            ready = self.client.ready()
            self.old_process = ready['backends']['xs']['coordinator_id']
            self.s_process = ready['backends']['s']['coordinator_id']
            self.client.select('xs_before')
            self.client.select('s_before')

            self.phase('busy-defer')
            held = self.client.start('xs_hold')
            require(held['query_id'] is not None and held['response_complete'] is False
                    and held['followup_gets'] == 0, 'UNSUBMITTED_HANDLE_NOT_OBSERVED')
            busy = self.observe()
            require(busy['result'] == 'observed' and busy['nonterminal_count'] == 0
                    and all(row['query_id'] != held['query_id'] for row in busy['queries']['rows'])
                    and idle_decision(busy, now=time.monotonic(), epoch=self.client.epoch) == 'busy',
                    'HELD_POST_BUSY_NOT_PROVEN')
            require(not self.journal['scale_operations'] and not self.client.journal['controls'], 'BUSY_DEFER_MUTATED')
            self.journal['busy_defer_verified'] = True
            self.notify()
            # Consume the held query before potentially slow audit delivery.
            # The blocked decision retains the preceding observation's ledger.
            self.client.finish('xs_hold')
            self.record_decision('blocked', 'host-handle-open', before=1, after=1,
                                 old_uid=initial['xs']['pod_uid'], new_uid=initial['xs']['pod_uid'])

            self.journal.update(result='prepared', pending=None)
            self.notify()
            return self.receipt()
        except BaseException as error:
            self._failed(error)
            raise

    def chill(self):
        require(self.journal['result'] == 'prepared', 'CONTROLLER_NOT_PREPARED')
        self.journal['result'] = 'chilling'
        try:
            initial = self.journal['identity']['initial']
            self.phase('chill')
            self.quiet()
            self.client.set_active(False, guard=self.checked_guard)
            # Inactive must be observed before taking a new full drain snapshot.
            self.quiet()
            down = self.scale_xs(0)
            self.client.select('s_chilled')
            self.client.select('xs_unavailable')
            s_history = self.client.queries('s')
            self.client.require_seen_terminal(s_history, ('s_before', 's_chilled'))
            self.checked_guard()
            self.client.registry()
            self.journal['chill_verified'] = True
            self.record_decision('success', 'idle-drained', before=1, after=0,
                                 old_uid=initial['xs']['pod_uid'], new_uid=None)

            self.journal.update(result='chilled', pending=None)
            self.notify()
            return self.receipt()
        except BaseException as error:
            self._failed(error)
            raise

    def wake(self):
        require(self.journal['result'] == 'chilled', 'CONTROLLER_NOT_CHILLED')
        self.journal['result'] = 'waking'
        try:
            initial = self.journal['identity']['initial']
            self.phase('wake')
            up = self.scale_xs(1)
            self.reconnect('xs')
            self.checked_guard()
            ready = self.client.ready(('xs',), require_healthy=False)
            require(ready['backends']['xs']['coordinator_id'] != self.old_process, 'WAKE_PROCESS_NOT_REPLACED')
            empty = self.client.queries('xs')
            require(empty['rows'] == [], 'WAKE_HISTORY_NOT_EMPTY')
            self.client.set_active(True, guard=self.checked_guard)
            final_ready = self.client.ready()
            require(final_ready['backends']['s']['coordinator_id'] == self.s_process, 'S_PROCESS_CHANGED')
            self.client.select('xs_after')
            self.client.select('s_after')
            xs_history, s_history = self.client.queries('xs'), self.client.queries('s')
            self.client.require_seen_terminal(xs_history, ('xs_after',))
            self.client.require_seen_terminal(s_history, ('s_before', 's_chilled', 's_after'))
            require({row['case'] for row in xs_history['rows']} == {'xs_after'}, 'WAKE_HISTORY_IDENTITY_MISMATCH')
            self.checked_guard()
            require(self.client.ledger()['all_results_consumed'] and not self.client.ledger()['uncaptured_requests'], 'QUERY_CLEANUP_UNVERIFIED')
            self.journal['identity']['final'] = copy.deepcopy(self.expected)
            self.journal['wake_verified'] = True
            self.record_decision('success', 'wake-ready', before=0, after=1,
                                 old_uid=initial['xs']['pod_uid'], new_uid=up['pod_uid'])
            self.client.journal['result'] = 'verified'
            self.journal.update(result='verified', pending=None)
            self.notify()
            return self.receipt()
        except BaseException as error:
            self._failed(error)
            raise

    def run(self):
        self.prepare()
        self.chill()
        return self.wake()
