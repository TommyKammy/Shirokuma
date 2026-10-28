import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools/local_experiment'
sys.path.insert(0, str(TOOLS))
import control_store as store
import data_management as dm
import warehouse_admission as admission


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        environment = patch.dict(os.environ, SHIROKUMA_STATE_DIR=str(self.directory), PYTHONPATH=str(TOOLS))
        environment.start()
        self.addCleanup(environment.stop)
        with store.connect() as db:
            dm.record_operation(db, dm.Operation('op', 'admin', 'apply', (), None, 'AWAITING_APPLY', (), None, (), ()))

    def admit(self, subject='q', **extra):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return admission.admit(db, actor='owner', kind='query', subject_id=subject, **extra)

    def test_admission_and_intent_rollback_together(self):
        with self.assertRaises(ValueError):
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                admission.admit(db, actor='owner', kind='query', subject_id='q')
                raise ValueError('intent failed')
        with store.connect() as db:
            self.assertEqual(admission.blockers(db), ())
        with store.connect() as db:
            with self.assertRaises(admission.AdmissionConflict):
                admission.admit(db, actor='owner', kind='query', subject_id='q')

    def test_root_cross_cap_preserves_existing_job_lifecycle_policy(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            scan = admission.admit(db, actor='owner', kind='stage_scan', subject_id='scan')
            for state in ('ACTIVE', 'UNKNOWN'):
                if state == 'UNKNOWN':
                    admission.finish(db, scan, state=state)
                for kind in ('job', 'stage_scan'):
                    with self.assertRaises(admission.AdmissionConflict):
                        admission.admit(db, warehouse_id='other', actor='other', kind=kind, subject_id=kind + state)
            admission.finish(db, scan, state='FAILED', evidence_ref='positively-closed')
            job = admission.admit(db, actor='owner', kind='job', subject_id='job')
            for state in ('ACTIVE', 'UNKNOWN'):
                if state == 'UNKNOWN':
                    admission.finish(db, job, state=state)
                with self.assertRaises(admission.AdmissionConflict):
                    admission.admit(db, warehouse_id='other', actor='other', kind='stage_scan', subject_id=state)
            # Job-vs-Job policy belongs to the Job admission caller, including
            # informed manual handling of its own older UNKNOWN runs.
            admission.admit(db, actor='owner', kind='job', subject_id='next-job')

    def test_legacy_run_states_block_scans_without_new_job_policy(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT INTO runs (id,job_name,actor,role,definition,arguments,status,created_at) VALUES ('legacy','job','owner','admin','{}','{}','queued','now')")
            for state in ('queued', 'running', 'unknown'):
                db.execute('UPDATE runs SET status=? WHERE id=?', (state, 'legacy'))
                with self.assertRaises(admission.AdmissionConflict):
                    admission.admit(db, actor='owner', kind='stage_scan', subject_id=state)
                job = admission.admit(db, actor='owner', kind='job', subject_id=state)
                admission.finish(db, job, state='FINISHED')

    def test_drain_rejects_new_but_allows_original_child_work(self):
        parent = self.admit('job', generation_id='g1')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            admission.begin_drain(db, operation_id='op')
        with self.assertRaises(admission.AdmissionDenied):
            self.admit('new')
        with admission.accepted(parent):
            child = self.admit('child', generation_id='g1')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(admission.AdmissionConflict):
                admission.finish(db, parent, state='FINISHED')
            admission.finish(db, child, state='FINISHED')
            admission.finish(db, parent, state='FINISHED')
        with self.assertRaises(admission.AdmissionConflict):
            with admission.accepted(parent):
                pass

    def test_unknown_is_durable_and_only_cleanup_evidence_releases_it(self):
        handle = self.admit(generation_id='g1')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            admission.finish(db, handle, state='UNKNOWN')
        run = subprocess.run([sys.executable, '-c',
            'import control_store as s,warehouse_admission as a\nwith s.connect() as d: print(a.blockers(d)[0]["state"])'],
            capture_output=True, text=True)
        self.assertEqual((run.returncode, run.stdout.strip()), (0, 'UNKNOWN'), run.stderr)
        with self.assertRaises(admission.AdmissionConflict):
            with admission.accepted(handle):
                pass
        with admission.accepted(handle, allow_unknown=True):
            with self.assertRaises(admission.AdmissionConflict):
                self.admit('child')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(admission.AdmissionConflict):
                admission.finish(db, handle, state='CANCELED')
            admission.finish(db, handle, state='CANCELED', evidence_ref='backend-delete-ack')
            self.assertEqual(admission.blockers(db), ())

    def test_generation_owner_and_lookup_contract(self):
        parent = self.admit('job', generation_id='g1')
        with store.connect() as db:
            self.assertEqual(admission.get_for_subject(db, kind='query', subject_id='job'), parent)
        with self.assertRaises(admission.AdmissionConflict):
            self.admit('child', parent=parent, generation_id='g2')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaises(admission.AdmissionConflict):
                admission.admit(db, actor='other', kind='query', subject_id='child', parent=parent)

    def test_legacy_root_captures_generation_once_and_child_inherits_it(self):
        with store.connect() as db:
            for generation in ('g1', 'g2'):
                dm.record_generation(db, dm.WarehouseConfigGeneration(generation, 'LOCAL', generation, (), (), (), 'APPLIED'))
            db.execute("INSERT INTO warehouse_controls VALUES ('LOCAL','OPEN',NULL,1,'g1')")
        parent = self.admit('legacy-without-generation')
        self.assertEqual(parent.generation_id, 'g1')
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT generation_id FROM warehouse_admissions WHERE id=?', (parent.id,)).fetchone()[0], 'g1')
            db.execute("UPDATE warehouse_controls SET applied_generation_id='g2'")
        with admission.accepted(parent):
            child = self.admit('child')
            self.assertEqual(child.generation_id, 'g1')
            with self.assertRaises(admission.AdmissionConflict):
                self.admit('wrong-child', generation_id='g2')
        self.assertEqual(self.admit('new-legacy-root').generation_id, 'g2')
        with self.assertRaises(admission.AdmissionConflict):
            self.admit('stale-generation', generation_id='g1')
        with store.connect() as db:
            self.assertEqual(admission.get_for_subject(db, kind='query', subject_id='legacy-without-generation'), parent)
            self.assertEqual(db.execute('SELECT generation_id FROM warehouse_admissions WHERE id=?', (parent.id,)).fetchone()[0], 'g1')

    def test_legacy_root_without_known_generation_keeps_none(self):
        handle = self.admit('unknown-generation')
        self.assertIsNone(handle.generation_id)
        with store.connect() as db:
            self.assertIsNone(admission.control(db)['applied_generation_id'])
            self.assertIsNone(db.execute('SELECT generation_id FROM warehouse_admissions WHERE id=?', (handle.id,)).fetchone()[0])
            self.assertEqual(db.execute('SELECT count(*) FROM databases').fetchone()[0], 0)

    def test_separate_process_cannot_admit_after_drain_commits(self):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            admission.begin_drain(db, operation_id='op')
        run = subprocess.run([sys.executable, '-c',
            'import control_store as s,warehouse_admission as a\ntry:\n with s.connect() as d:\n  d.execute("BEGIN IMMEDIATE")\n  a.admit(d,actor="x",kind="query",subject_id="q")\nexcept a.AdmissionDenied: print("blocked")'],
            capture_output=True, text=True)
        self.assertEqual((run.returncode, run.stdout.strip()), (0, 'blocked'), run.stderr)

    def test_gate_deadline_and_nested_shared_continuation(self):
        script = ('import warehouse_admission as a,time\n'
                  'try:\n with a.lifecycle_gate(exclusive=True,deadline=time.monotonic()+.1): print("entered")\n'
                  'except a.GateTimeout: print("timeout")')
        with admission.lifecycle_gate():
            with admission.lifecycle_gate():
                run = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
            self.assertEqual((run.returncode, run.stdout.strip()), (0, 'timeout'), run.stderr)
            with self.assertRaises(admission.AdmissionConflict):
                with admission.lifecycle_gate(exclusive=True):
                    pass
        with admission.lifecycle_gate(exclusive=True, deadline=time.monotonic() + .5):
            pass


if __name__ == '__main__':
    unittest.main()
