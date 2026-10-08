"""Commit-bound retained Flux finding diagnosis; no fetch/scan/write capability."""
import copy
from datetime import datetime, timezone
import hashlib
import json

from tools.local_experiment import admission_contract as A

S, R = A.C.S, A.R


def age(value, now):
    """Reject unknown/naive/future clocks rather than claiming fresh evidence."""
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        assert stamp.tzinfo is not None and now.tzinfo is not None
        seconds = (now - stamp).total_seconds()
        assert seconds >= 0
    except (AttributeError, TypeError, ValueError, AssertionError):
        raise R.RunnerError('FLUX_FINDINGS_TIMESTAMP_INVALID') from None
    return seconds


def diagnose(root, commit, items, baseline, network, now):
    """Live inhibition is required; CVE presence never proves exploitability.

    Package fix strings are scanner claims, not Flux release recommendations.
    No freshness threshold waives HIGH rejection, even for a zero-age record.
    """
    S.validate(items, baseline, S.READY)
    R.require(network.get('kind') == 'List' and isinstance(network.get('items'), list)
              and not network.get('metadata', {}).get('continue'), 'FLUX_NETWORK_INCOMPLETE')
    R.require(all(i.get('metadata', {}).get('namespace') == 'flux-system' and
                  i.get('kind') in {'Service', 'Endpoints', 'Ingress', 'NetworkPolicy'}
                  for i in network['items']), 'FLUX_NETWORK_SCOPE_INVALID')
    ledger_path = root/'security/resident-images.json'
    raw = ledger_path.read_bytes()
    A.commit_blob(root, commit, ledger_path, raw)
    ledger = json.loads(raw, object_pairs_hook=R.unique_pairs)
    images = [i for i in ledger['images'] if i['component'] in S.FLUX]
    R.require(len(images) == 4 and {i['component'] for i in images} == S.FLUX,
              'FLUX_FINDINGS_LEDGER_SCOPE')
    snapshots = [(ledger_path, raw)]
    rows = []
    for image in sorted(images, key=lambda i: i['component']):
        name = image['component']
        deployments = [i for i in items if i['kind'] == 'Deployment' and
                       i['metadata'].get('namespace') == 'flux-system' and i['metadata']['name'] == name]
        R.require(len(deployments) == 1, 'FLUX_FINDINGS_DEPLOYMENT_SCOPE')
        pod = deployments[0]['spec']['template']['spec']
        containers = pod['containers']
        R.require(len(containers) == 1 and containers[0]['image'] == image['reference'],
                  'FLUX_FINDINGS_IMAGE_DRIFT')
        artifacts = {}
        for field in ('sbom_artifact', 'scan_artifact', 'supply_chain_artifact'):
            path = A.D.V.evidence_artifact_path(ledger_path, root, image[field], field)
            evidence = path.read_bytes()
            artifacts[field] = {'path': A.commit_blob(root, commit, path, evidence),
                                'sha256': hashlib.sha256(evidence).hexdigest()}
            snapshots.append((path, evidence))
            if field == 'scan_artifact':
                scan = json.loads(evidence, object_pairs_hook=R.unique_pairs)
        findings = A.blocking_findings(scan, image['reference'])
        services = [i for i in network['items'] if i['kind'] == 'Service' and
                    i.get('spec', {}).get('selector') and
                    all(deployments[0]['spec']['template']['metadata']['labels'].get(k) == v
                        for k, v in i['spec']['selector'].items())]
        rows.append({'component': name, 'version': image['version'], 'reference': image['reference'],
                     'artifacts': artifacts, 'blocking_findings': findings,
                     'blocking_counts': {s: sum(f['record']['Severity'] == s for f in findings)
                                         for s in ('HIGH', 'CRITICAL')},
                     'freshness': {'scan_created_at': scan.get('CreatedAt'),
                                   'scan_age_seconds': age(scan.get('CreatedAt'), now),
                                   'db_updated_at': image['vulnerability_db_updated_at'],
                                   'db_age_seconds': age(image['vulnerability_db_updated_at'], now),
                                   'scanner_version': image['scanner_version'],
                                   'current_advisories_verified': False},
                     'exposure': {'deployment_uid': deployments[0]['metadata']['uid'],
                                  'args': copy.deepcopy(containers[0].get('args', [])),
                                  'ports': copy.deepcopy(containers[0].get('ports', [])),
                                  'security_context': copy.deepcopy(containers[0].get('securityContext', {})),
                                  'service_account': pod.get('serviceAccountName'),
                                  'host_network': pod.get('hostNetwork', False),
                                  'services': copy.deepcopy(services),
                                  'exploitability': 'not_established',
                                  'suspension_eliminates_process_exposure': False}})
    R.require(all(path.read_bytes() == evidence for path, evidence in snapshots),
              'FLUX_FINDINGS_EVIDENCE_CHANGED')
    return {'schema_version': 1, 'mode': 'retained_flux_findings_diagnosis',
            'commit': commit, 'observed_at': now.astimezone(timezone.utc).isoformat(),
            'ledger_sha256': hashlib.sha256(raw).hexdigest(), 'controllers': rows,
            'network_sha256': A.C.digest(network),
            'network_objects': copy.deepcopy(network['items']),
            'riskDecision': None, 'strict_high_rejection': True,
            'admission_verified': False, 'activation_authorized': False, 'gate_acceptance': False,
            'dependency': 'Separately authorize evidence refresh for each exact ARM64 digest with '
                          'current DB/scanner and publisher/SBOM binding; if findings persist, select '
                          'new pinned Flux images proving recorded fixed packages. No safe replacement '
                          'digest is established by retained package FixedVersion strings.',
            'limitations': ['no fresh scan, image bytes, signature or advisory verification',
                            'network objects do not prove reachability, policy enforcement or no public exposure',
                            'package presence does not prove vulnerable code paths execute',
                            'suspended sources/Jobs do not stop controller HTTP/metrics/health listeners']}
