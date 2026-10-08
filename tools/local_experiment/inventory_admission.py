"""Diagnose every rendered image occurrence; no network or activation capability."""
import copy
import hashlib
import json

from tools.local_experiment import admission_contract as A
from tools.local_experiment import render_inventory as I

C, R, V = A.C, A.R, A.D.V


def diagnose(root, candidate, inventory, items, baseline, staging, binary='kubectl'):
    # Regenerate instead of trusting a saved inventory's completeness flags.
    I.validate(root, inventory, candidate, staging, binary)
    contract = A.build(root, candidate, items, baseline)
    ledger_path = root/'security/resident-images.json'
    raw = ledger_path.read_bytes()
    R.require(hashlib.sha256(raw).hexdigest() == contract['ledger_sha256'],
              'INVENTORY_ADMISSION_LEDGER_DRIFT')
    commit = candidate['identity']['commit']
    A.commit_blob(root, commit, ledger_path, raw)
    ledger = json.loads(raw, object_pairs_hook=R.unique_pairs)
    snapshots = [(ledger_path, raw)]
    rows = []
    for occurrence in inventory['images']:
        reference = occurrence['reference']
        matches = [i for i in ledger['images'] if i.get('reference') == reference]
        row = {'occurrence': copy.deepcopy(occurrence), 'artifacts': {}, 'checks': {},
               'gaps': [], 'fresh_cryptographic_verification': False,
               'image_bytes_verified': False}
        rows.append(row)
        if len(matches) != 1:
            row['gaps'].append('missing_or_ambiguous_exact_reference_ledger_identity')
            continue
        image = matches[0]
        row.update(component=image['component'], platform=image.get('platform'))
        if image.get('platform') != 'linux/arm64':
            row['gaps'].append('ledger_platform_not_linux_arm64')
        paths = {}
        for field in ('sbom_artifact', 'scan_artifact', 'supply_chain_artifact'):
            path = V.evidence_artifact_path(ledger_path, root, image[field], field)
            evidence = path.read_bytes()
            row['artifacts'][field] = {'path': A.commit_blob(root, commit, path, evidence),
                                       'sha256': hashlib.sha256(evidence).hexdigest()}
            snapshots.append((path, evidence))
            paths[field] = path
        def check(label, operation):
            try:
                operation()
                row['checks'][label] = {'record_check': 'pass'}
            except V.PolicyError as error:
                row['checks'][label] = {'record_check': 'blocked', 'reason': str(error)}
        check('sbom', lambda: V.check_sbom(paths['sbom_artifact']))
        check('strict_scan', lambda: V.check_trivy(paths['scan_artifact'],
                                                expected_image_reference=reference))
        check('publisher_record', lambda: V.check_supply_chain_evidence(
            paths['supply_chain_artifact'], repository=root,
            component=image['component'], reference=reference, version=image['version'],
            source=image['source'], sbom_path=paths['sbom_artifact'], scan_path=paths['scan_artifact'],
            sbom_generator=image['sbom_generator'], scanner_version=image['scanner_version'],
            vulnerability_db_updated_at=image['vulnerability_db_updated_at']))
        scan = json.loads(paths['scan_artifact'].read_bytes(), object_pairs_hook=R.unique_pairs)
        row['blocking_findings'] = A.blocking_findings(scan, reference)
        row['blocking_counts'] = {s: sum(f['record']['Severity'] == s for f in row['blocking_findings'])
                                  for s in ('HIGH', 'CRITICAL')}
        row['scanner_version'] = image['scanner_version']
        row['scan_created_at'] = scan.get('CreatedAt')
        row['vulnerability_db_updated_at'] = image['vulnerability_db_updated_at']
        records = json.loads(paths['supply_chain_artifact'].read_bytes(),
                             object_pairs_hook=R.unique_pairs)['images']
        records = [r for r in records if r.get('component') == image['component'] and
                   r.get('reference') == reference]
        R.require(len(records) == 1, 'INVENTORY_ADMISSION_PUBLISHER_IDENTITY_MISSING')
        row['recorded_publisher'] = {k: records[0].get('signature', {}).get(k) for k in
                                    ('issuer', 'identity', 'workflow_repository', 'workflow_ref', 'commit')}
        row['canonical_retained_bundle_check'] = (image['component'], image['version']) in V.CANONICAL_SIGNED_INDEX_PUBLICATIONS
    R.require(all(path.read_bytes() == evidence for path, evidence in snapshots),
              'INVENTORY_ADMISSION_EVIDENCE_CHANGED')
    return {'schema_version': 1, 'mode': 'read_only_complete_inventory_admission_diagnosis',
            'candidate_sha256': C.digest(candidate), 'inventory_sha256': C.digest(inventory),
            'candidate_identity': copy.deepcopy(candidate['identity']),
            'ledger_sha256': contract['ledger_sha256'], 'images': rows,
            'image_occurrences': len(rows), 'unique_image_references': len({i['reference'] for i in inventory['images']}),
            'repository_inventory_complete': inventory['repository_inventory_complete'],
            'unavailable_runtime_references': [copy.deepcopy(r) for r in inventory['runtime_input_references']
                                               if not r['repository_available']],
            'strict_scan_all_pass': bool(rows) and all(
                r['checks'].get('strict_scan', {}).get('record_check') == 'pass' and not r['gaps'] for r in rows),
            'local_risk_decision': {'status': 'missing', 'decision': None},
            'runtime_inputs_complete': False, 'admission_verified': False,
            'activation_authorized': False, 'gate_acceptance': False,
            'fetched_commit': None, 'applied_commit': None,
            'limitations': ['ledger ARM64 label is not verified image bytes',
                            'publisher record checks are not fresh cryptographic verification',
                            'retained scans are not a fresh advisory rescan',
                            'repository-unavailable references do not prove runtime absence',
                            'no Secret values inspected, fetch, transport selection, risk approval or activation']}
