"""Non-activating local evidence contract; never an approval or apply manifest.

Bind inhibited source identity, retained publisher/scan bytes and exact blocking
findings. Revalidation needs fresh observations; a saved contract grants nothing.
"""
import copy
import hashlib
import json

from tools.local_experiment import admission_diagnosis as D

C = D.C
R = C.S.R


def blocking_findings(scan, reference):
    R.require(isinstance(scan, dict) and scan.get('SchemaVersion') == 2 and
              scan.get('ArtifactName') == reference and
              isinstance(scan.get('Results'), list) and scan['Results'],
              'LOCAL_EVIDENCE_SCAN_INVALID')
    findings = []
    for result in scan['Results']:
        R.require(isinstance(result, dict) and isinstance(result.get('Target'), str),
                  'LOCAL_EVIDENCE_SCAN_INVALID')
        vulnerabilities = result.get('Vulnerabilities')
        if vulnerabilities is None:
            vulnerabilities = []
        R.require(isinstance(vulnerabilities, list), 'LOCAL_EVIDENCE_SCAN_INVALID')
        for vulnerability in vulnerabilities:
            R.require(isinstance(vulnerability, dict) and vulnerability.get('Severity') in
                      ('UNKNOWN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'),
                      'LOCAL_EVIDENCE_SCAN_INVALID')
            if vulnerability['Severity'] not in ('HIGH', 'CRITICAL'):
                continue
            R.require(all(isinstance(vulnerability.get(k), str) and vulnerability[k]
                          for k in ('VulnerabilityID', 'PkgName', 'InstalledVersion')),
                      'LOCAL_EVIDENCE_FINDING_INVALID')
            # Preserve the complete record and result scope, including multiplicity,
            # unfixed versions, package paths and data-source metadata.
            findings.append({'target': result['Target'], 'class': result.get('Class'),
                             'type': result.get('Type'), 'record': copy.deepcopy(vulnerability)})
    return sorted(findings, key=lambda f: json.dumps(f, sort_keys=True))


def commit_blob(root, commit, path, raw):
    relative = C.path(path.relative_to(root).as_posix())
    oid = C.git(root, 'rev-parse', '--verify', commit+':'+relative)
    R.require(C.git(root, 'cat-file', '-t', oid) == 'blob' and
              hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == oid,
              'LOCAL_EVIDENCE_COMMIT_BLOB_DRIFT')
    return relative


def build(root, candidate, items, baseline):
    """Read local bytes only; strict failures remain evidence, never waived.

    diagnose revalidates the exact candidate against fresh complete inhibited
    observations. Evidence bytes must also be blobs in the selected source commit.
    No risk decision can be supplied to this builder.
    """
    diagnosis = D.diagnose(root, candidate, items, baseline)
    rows = diagnosis['controllers']
    R.require(len(rows) == 4 and {r['component'] for r in rows} == C.S.FLUX,
              'LOCAL_EVIDENCE_CONTROLLER_SCOPE')
    ledger_path = root/'security/resident-images.json'
    raw = ledger_path.read_bytes()
    R.require(hashlib.sha256(raw).hexdigest() == diagnosis['ledger_sha256'],
              'LOCAL_EVIDENCE_LEDGER_DRIFT')
    ledger = json.loads(raw, object_pairs_hook=R.unique_pairs)
    commit = candidate['identity']['commit']
    commit_blob(root, commit, ledger_path, raw)
    snapshots = [(ledger_path, raw)]
    controllers = []
    for row in sorted(rows, key=lambda r: r['component']):
        matches = [i for i in ledger['images'] if i['component'] == row['component']]
        R.require(len(matches) == 1 and matches[0]['reference'] == row['reference'],
                  'LOCAL_EVIDENCE_IMAGE_DRIFT')
        image = matches[0]
        artifacts = {}
        scan = None
        for field, expected in row['artifact_sha256'].items():
            path = D.V.evidence_artifact_path(ledger_path, root, image[field], field)
            raw = path.read_bytes()
            R.require(hashlib.sha256(raw).hexdigest() == expected, 'LOCAL_EVIDENCE_ARTIFACT_DRIFT')
            artifacts[field] = {'path': commit_blob(root, commit, path, raw), 'sha256': expected}
            snapshots.append((path, raw))
            if field == 'scan_artifact':
                scan = json.loads(raw, object_pairs_hook=R.unique_pairs)
        findings = blocking_findings(scan, row['reference'])
        counts = {s: sum(f['record']['Severity'] == s for f in findings) for s in ('HIGH', 'CRITICAL')}
        controllers.append(dict(copy.deepcopy(row), artifacts=artifacts,
            scanner_version=image['scanner_version'],
            vulnerability_db_updated_at=image['vulnerability_db_updated_at'],
            scan_created_at=scan.get('CreatedAt'), blocking_findings=findings, blocking_counts=counts))
    R.require(all(path.read_bytes() == raw for path, raw in snapshots),
              'LOCAL_EVIDENCE_CHANGED')
    return {'schema_version': 1, 'mode': 'non_activating_local_admission_evidence',
            'candidate': copy.deepcopy(candidate), 'candidate_sha256': C.digest(candidate),
            'ledger_sha256': diagnosis['ledger_sha256'], 'controllers': controllers,
            'diagnosis': diagnosis,
            'local_risk_decision': {'status': 'missing', 'decision': None},
            'transitive_render_inventory': {'status': 'missing', 'inventory_sha256': None},
            'admission_verified': False, 'gate_acceptance': False,
            'activation_authorized': False,
            'limitations': ['retained scans are not a fresh advisory rescan',
                            'publisher records are not fresh cryptographic verification',
                            'no approval, exception renewal, transport selection or enforcement',
                            'render inventory and risk decision require separate review']}


def validate(root, contract, candidate, items, baseline):
    """Exact regeneration rejects any approval, omitted finding or stale binding."""
    R.require(contract == build(root, candidate, items, baseline), 'LOCAL_EVIDENCE_CONTRACT_DRIFT')
    return True


def build_complete(root, candidate, items, baseline, staging, binary='kubectl'):
    """Bind every rendered occurrence and unavailable input; grant no admission.

    Local import avoids the diagnostic's dependency on the four-controller
    contract. Both the inventory and diagnosis are regenerated from local bytes,
    not accepted from caller-supplied completeness or approval claims.
    """
    from tools.local_experiment import inventory_admission as D
    inventory = D.I.build(root, candidate, staging, binary)
    diagnosis = D.diagnose(root, candidate, inventory, items, baseline, staging, binary)
    return {'schema_version': 1, 'mode': 'non_activating_complete_inventory_admission_evidence',
            'candidate': copy.deepcopy(candidate), 'candidate_sha256': C.digest(candidate),
            'transitive_render_inventory': {
                'status': 'repository_complete_runtime_gaps' if inventory['repository_inventory_complete'] else 'incomplete',
                'inventory_sha256': C.digest(inventory), 'inventory': inventory},
            'inventory_admission_diagnosis': diagnosis,
            'diagnosis_sha256': C.digest(diagnosis),
            'local_risk_decision': {'status': 'missing', 'decision': None},
            'runtime_inputs_complete': False, 'admission_verified': False,
            'gate_acceptance': False, 'activation_authorized': False,
            'fetched_commit': None, 'applied_commit': None,
            'limitations': copy.deepcopy(diagnosis['limitations'])}


def validate_complete(root, contract, candidate, items, baseline, staging, binary='kubectl'):
    """Regenerate all images/findings/references and fresh inhibition guards."""
    R.require(contract == build_complete(root, candidate, items, baseline, staging, binary),
              'LOCAL_EVIDENCE_COMPLETE_CONTRACT_DRIFT')
    return True
