"""Pure fake resource samples; no runtime status, HTTP, ps or service actions."""
from copy import deepcopy
import importlib
import io
import json
import os
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.local_experiment.finops import local_processes as P

START = 'Sun Sep 20 08:30:00 2026'


def identity(pid):
    return dict(pid=pid,ppid=10,pgid=pid,uid=os.getuid(),start_time=START,command_sha256='a'*64)


def snapshot(pids, kernels=0):
    return dict(state='observed',reason=None,identities={pid:identity(pid) for pid in pids},kernel_count=kernels)


class Fake:
    def __init__(self):
        self.before={'mlflow':snapshot([101,102]),'notebook-lab':snapshot([201,202,203],2),
                     'model-serving':snapshot([301])}
        self.after=deepcopy(self.before); self.events=[]; self.reads=0; self.round=0
        self.error=None; self.change=None
        self.stamps=iter(['2026-09-20T09:00:00Z','2026-09-20T09:00:01.020Z'])
        self.ticks=iter([100_000_000_000,101_020_000_000])
    def now(self): return next(self.stamps)
    def monotonic_ns(self): return next(self.ticks)
    def sleep(self,duration): self.events.append(('sleep',duration))
    def snapshot(self,name):
        self.events.append(('snapshot',name)); value=(self.before if self.round<3 else self.after)[name]
        self.round+=1
        if isinstance(value,BaseException): raise value
        return deepcopy(value)
    def sample(self,pids):
        self.events.append(('sample',set(pids))); self.reads+=1
        if self.error is not None: raise self.error
        values={pid:dict(start_time=START,cpu_ms=1000+(30 if self.reads==2 else 0),
                        rss_bytes=pid*1024) for pid in pids}
        if self.change is not None: self.change(values,self.reads)
        return values


class ProcessTests(unittest.TestCase):
    def test_two_shared_samples_surrounded_by_ownership_and_exact_units(self):
        backend=Fake(); value=P.collect(backend)
        self.assertIs(P.validate(value),value)
        self.assertEqual(backend.events[:3],[('snapshot',n) for n in P.COMPONENTS])
        self.assertEqual(backend.events[-3:],[('snapshot',n) for n in P.COMPONENTS])
        self.assertEqual([x[0] for x in backend.events[3:-3]],['sample','sleep','sample'])
        self.assertEqual(backend.events[4],('sleep',1))
        ml,lab,serving=value['components']
        self.assertEqual(ml['sample'],dict(cpu_time_delta_ms='60',elapsed_ns='1020000000',rss_bytes=str(203*1024)))
        self.assertEqual(lab['identity']['kernel_count'],2)
        self.assertEqual(lab['identity']['process_count'],3)
        self.assertEqual(lab['sample']['cpu_time_delta_ms'],'90')
        self.assertEqual(serving['identity']['process_count'],1)
        self.assertEqual(P.parse(P._canonical(value)),value)
        self.assertNotIn('command_sha256',json.dumps(value))
        self.assertNotIn('start_time',json.dumps(value))

    def test_observed_zero_is_distinct_from_stopped_unavailable_and_unknown(self):
        backend=Fake()
        backend.change=lambda values,read:[v.update(cpu_ms=1000,rss_bytes=0) for v in values.values()]
        zero=P.collect(backend)['components'][0]
        self.assertEqual(zero['state'],'observed');self.assertEqual(zero['sample']['cpu_time_delta_ms'],'0')
        for state,reason in [('stopped','STOPPED'),('unavailable','UNINITIALIZED'),
                             ('ownership-unknown','OWNERSHIP_UNKNOWN')]:
            with self.subTest(state=state):
                backend=Fake(); item=dict(state=state,reason=reason,identities={},kernel_count=0)
                backend.before['mlflow']=item;backend.after['mlflow']=deepcopy(item)
                row=P.collect(backend)['components'][0]
                self.assertEqual((row['state'],row['reason']),(state,reason))
                self.assertIsNone(row['sample']);self.assertIsNone(row['identity'])
                self.assertNotIn(101,backend.events[3][1])

    def test_reused_pid_or_changed_parent_command_or_kernel_set_is_not_counted(self):
        for field,new in [('start_time','Sun Sep 20 08:31:00 2026'),('ppid',55),
                          ('command_sha256','b'*64),('pgid',55)]:
            with self.subTest(field=field):
                backend=Fake();backend.after['mlflow']['identities'][101][field]=new
                rows=P.collect(backend)['components']
                self.assertEqual(rows[0]['reason'],'IDENTITY_CHANGED');self.assertIsNone(rows[0]['sample'])
                self.assertEqual(rows[1]['state'],'observed')
        backend=Fake();backend.after['notebook-lab']['identities'].pop(203)
        self.assertEqual(P.collect(backend)['components'][1]['reason'],'IDENTITY_CHANGED')

    def test_mid_sample_pid_reuse_and_cpu_rollback_refuse(self):
        for field,new,reason in [('start_time','Sun Sep 20 09:00:00 2026','IDENTITY_CHANGED'),
                                 ('cpu_ms',999,'SAMPLE_INVALID')]:
            backend=Fake()
            backend.change=lambda values,read,k=field,v=new:values[101].update({k:v}) if read==2 else None
            row=P.collect(backend)['components'][0]
            self.assertEqual(row['reason'],reason);self.assertIsNone(row['sample'])

    def test_duplicate_owned_pid_never_double_counted(self):
        backend=Fake()
        backend.before['model-serving']=snapshot([101]);backend.after=deepcopy(backend.before)
        rows=P.collect(backend)['components']
        self.assertEqual(rows[0]['reason'],'OWNERSHIP_UNKNOWN')
        self.assertEqual(rows[2]['reason'],'OWNERSHIP_UNKNOWN')
        self.assertEqual(rows[1]['state'],'observed')

    def test_limits_window_consistency_and_unknown_shapes(self):
        good=P.collect(Fake())
        changes=[lambda v:v['components'][0]['sample'].update(elapsed_ns='1'),
                 lambda v:v['components'][0]['sample'].update(rss_bytes=str(P.MAX_RSS_BYTES+1)),
                 lambda v:v['components'][0]['sample'].update(cpu_time_delta_ms=True),
                 lambda v:v['components'][0]['sample'].update(cpu_time_delta_ms='01'),
                 lambda v:v['components'][0]['identity'].update(process_count=129),
                 lambda v:v['components'][1]['identity'].update(kernel_count=0),
                 lambda v:v['components'][2].update(observed_at='2026-09-20T09:00:02Z'),
                 lambda v:v['components'][0].update(reason='READ_FAILED'),
                 lambda v:v.update(private_command='secret')]
        for change in changes:
            value=deepcopy(good);change(value)
            with self.assertRaises(P.ProcessError):P.validate(value)
        value=P.unknown_all('READ_FAILED');value['components'][0]['sample']=good['components'][0]['sample']
        with self.assertRaises(P.ProcessError):P.validate(value)
        with self.assertRaises(P.ProcessError):P.unknown_all('arbitrary secret')
        with self.assertRaises(P.ProcessError):P.parse(b'{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(P.ProcessError,'READ_LIMIT'):P.parse(b' '*(P.MAX_PUBLIC_BYTES+1))

    def test_ps_parse_cpu_duration_and_rss_kib(self):
        for duration,expected in [('0:01.23',1230),('61:00.00',3660000),
                                  ('01:02:03.456',3723456),('2-01:02:03.4',176523400)]:
            raw=('101 '+START+' 2048 '+duration+'\n').encode()
            row=P.parse_ps(raw,{101})[101]
            self.assertEqual(row['cpu_ms'],expected);self.assertEqual(row['rss_bytes'],2097152)
        for raw in [b'',('999 '+START+' 1 0:00.00\n').encode(),
                    ('101 '+START+' 1 0:61.00\n').encode(),
                    ('101 '+START+' -1 0:00.00\n').encode(),
                    ('101 '+START+' 1 0:00.00\n') .encode()*2]:
            with self.assertRaises(P.ProcessError):P.parse_ps(raw,{101})

    def test_ps_call_is_fixed_pid_only_no_credentials_or_arbitrary_argv(self):
        raw=('101 '+START+' 2048 0:00.10\n').encode()
        with patch.object(P.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=raw)) as called:
            P.Backend.sample({101})
        argv=called.call_args.args[0];kw=called.call_args.kwargs
        self.assertEqual(argv,['/bin/ps','-p','101','-o','pid=','-o','lstart=','-o','rss=','-o','time='])
        self.assertEqual(kw['env'],{'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','LC_ALL':'C'})
        self.assertEqual(kw['timeout'],2)
        with patch.object(P.subprocess,'run',side_effect=subprocess.TimeoutExpired(argv,2)):
            with self.assertRaisesRegex(P.ProcessError,'READ_TIMEOUT'):P.Backend.sample({101})

    def test_sampling_failure_is_partial_and_preserves_first_interrupt(self):
        backend=Fake();backend.error=P.ProcessError('READ_TIMEOUT')
        value=P.collect(backend)
        self.assertTrue(all(row['reason']=='READ_TIMEOUT' and row['sample'] is None for row in value['components']))
        backend=Fake();original=KeyboardInterrupt();backend.error=original
        with self.assertRaises(KeyboardInterrupt) as caught:P.collect(backend)
        self.assertIs(caught.exception,original)
        self.assertNotIn(('sleep',1),backend.events)

    def test_fixed_error_and_valid_partial_exit_zero_without_raw_message(self):
        backend=Fake();backend.before['notebook-lab']=OSError('private-token-and-command')
        backend.after['notebook-lab']=OSError('private-token-and-command')
        output=io.StringIO();self.assertEqual(P.main(['--worker'],stdout=output,backend=backend),0)
        value=json.loads(output.getvalue());P.validate(value)
        self.assertEqual(value['components'][1]['reason'],'READ_FAILED')
        self.assertEqual(value['components'][0]['state'],'observed')
        self.assertNotIn('private-token',output.getvalue())

    def test_no_runtime_import_in_default_process(self):
        original=__import__
        def guarded(name,*args,**kwargs):
            if 'jupyter' in name or name.endswith('serving_runtime') or name.endswith('notebook_lab.runtime'):
                raise AssertionError('runtime import outside worker')
            return original(name,*args,**kwargs)
        with patch('builtins.__import__',side_effect=guarded):
            importlib.reload(P)
            P.validate(P.unknown_all('READ_FAILED'))
        self.assertEqual(P.arguments(),[str(P.PYTHON),'-I','-B',str(P.WORKER),'--worker'])

    def test_backend_uses_only_owned_group_members(self):
        owned=dict(pid=101,ppid=10,pgid=101,uid=os.getuid(),start_time=START,command='fixed owned',state='S')
        foreign=dict(owned,pid=999,pgid=999,command='unrelated private command')
        group=SimpleNamespace(status=lambda:dict(state='running'),processes=lambda:{101:owned,999:foreign},
            _record=lambda:dict(leader=owned),_same=lambda a,b:a==b,
            _owned_group=lambda record,rows:[rows[101]])
        backend=P.Backend.__new__(P.Backend);backend.mlflow=group;backend.lab=None;backend.serving=None
        result=backend.snapshot('mlflow')
        self.assertEqual(set(result['identities']),{101})
        self.assertNotIn('fixed owned',json.dumps(result));self.assertNotIn('unrelated',json.dumps(result))
        self.assertEqual(result['identities'][101]['command_sha256'],P.hashlib.sha256(b'fixed owned').hexdigest())

    def test_adapter_ml_group_lab_thirteen_kernels_and_serving_reuse_own_guards(self):
        def process(pid,parent):
            return dict(pid=pid,ppid=parent,pgid=pid,uid=os.getuid(),start_time=START,
                        command='fixed '+str(pid),state='S')
        rows={pid:process(pid,201 if 202<=pid<=214 else 101 if pid==102 else 10)
              for pid in [101,102,*range(201,215),301,999]}
        rows[102]['pgid']=101
        calls=[]; mlrecord=dict(leader=rows[101],members=[rows[102]])
        def group(record,observed):
            calls.append('ml-owned-group');self.assertIs(record,mlrecord);self.assertIs(observed,rows)
            return [rows[101],rows[102]]
        ml=SimpleNamespace(status=lambda:dict(state='running',store_id='fixed-store'),
            processes=lambda:rows,_record=lambda:mlrecord,_same=lambda a,b:a==b,_owned_group=group)
        labowner=dict(instance_id='a'*32); labrecord=dict(leader=rows[201]); ids=list(range(13))
        def kernels(record,selected,observed):
            calls.append('lab-owned-kernels');self.assertIs(record,labrecord);self.assertEqual(selected,ids)
            self.assertIs(observed,rows)
            return {str(i):rows[202+i] for i in ids}
        lab=SimpleNamespace(status=lambda:dict(state='running'),_owner=lambda:labowner,
            _record=lambda owner:labrecord,_leader=lambda record,observed:observed[201],
            _kernels=lambda:ids,_kernel_processes=kernels)
        serverrecord=dict(leader=rows[301]);serverowner=dict(instance_id='b'*32)
        def serving_owned(record,observed):
            calls.append('serving-owned');self.assertIs(record,serverrecord);self.assertIs(observed,rows)
            return rows[301]
        server=SimpleNamespace(status=lambda:dict(state='running'),_owner=lambda:serverowner,
            _intent=lambda owner:dict(launch_id='c'*32),_record=lambda owner,launch:serverrecord,_owned=serving_owned)
        backend=P.Backend.__new__(P.Backend);backend.mlflow=ml;backend.lab=lab;backend.serving=server
        values=[backend.snapshot(name) for name in P.COMPONENTS]
        self.assertEqual([len(value['identities']) for value in values],[2,14,1])
        self.assertEqual(values[1]['kernel_count'],13)
        self.assertEqual(calls,['ml-owned-group','lab-owned-kernels','serving-owned'])
        self.assertFalse(any(999 in value['identities'] for value in values))
        old=values[1]['ownership_sha256'];labowner['instance_id']='d'*32
        self.assertNotEqual(backend.snapshot('notebook-lab')['ownership_sha256'],old)

    def test_owner_record_change_invalidates_same_process_sample(self):
        backend=Fake();backend.before['mlflow']['ownership_sha256']='a'*64
        backend.after['mlflow']['ownership_sha256']='b'*64
        row=P.collect(backend)['components'][0]
        self.assertEqual(row['reason'],'IDENTITY_CHANGED');self.assertIsNone(row['sample'])

    def test_global_pid_cap_refuses_before_any_measurement(self):
        backend=Fake();backend.before['mlflow']=snapshot(range(1000,1129));backend.after=deepcopy(backend.before)
        value=P.collect(backend)
        self.assertTrue(all(row['reason']=='SCOPE_INVALID' for row in value['components']))
        self.assertEqual(backend.reads,0)


if __name__=='__main__': unittest.main()
