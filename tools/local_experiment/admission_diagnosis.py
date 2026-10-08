"""Read-only L0 candidate/image evidence diagnosis, never an admission decision.

Reuse strict artifact validators without exceptions. Successful record checks
are not fresh cryptographic verification or runtime admission enforcement.
There is no activation CLI, network fetch, policy mutation or runtime write.
"""
import hashlib

from scripts import verify_supply_chain as V
from tools.local_experiment import source_candidate as C


def diagnose(root, candidate, items, baseline):
    """Require exact inhibited candidate/observation equality before evaluation.

    The caller retains the fresh probe separately. Artifact hashes bind the
    diagnostic inputs, not independent truth of retained publisher statements.
    """
    C.S.R.require(candidate == C.build(root, candidate['identity']['commit'], items, baseline),
                  'ADMISSION_CANDIDATE_DRIFT')
    ledger_path = root/'security/resident-images.json'
    ledger_sha256 = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
    ledger = V.load_json(ledger_path)
    C.S.R.require(ledger.get('schema_version') == 1 and isinstance(ledger.get('images'), list),
                  'ADMISSION_LEDGER_INVALID')
    rows = []
    for controller in candidate['controllers']:
        matches = [i for i in ledger['images'] if i.get('component') == controller['name']]
        C.S.R.require(len(matches) == 1, 'ADMISSION_LEDGER_IDENTITY_MISSING')
        image = matches[0]
        C.S.R.require(controller['images'] == [image['reference']] and
                      image.get('platform') == 'linux/arm64', 'ADMISSION_IMAGE_IDENTITY_DRIFT')
        paths = {field: V.evidence_artifact_path(ledger_path, root, image[field], field)
                 for field in ('sbom_artifact', 'scan_artifact', 'supply_chain_artifact')}
        hashes = {field: hashlib.sha256(p.read_bytes()).hexdigest() for field, p in paths.items()}
        checks = {}
        def check(label, operation):
            try:
                operation()
                checks[label] = {'record_check': 'pass'}
            except V.PolicyError as error:
                checks[label] = {'record_check': 'blocked', 'reason': str(error)}
        check('sbom', lambda: V.check_sbom(paths['sbom_artifact']))
        check('strict_scan', lambda: V.check_trivy(paths['scan_artifact'],
                                                 expected_image_reference=image['reference']))
        check('publisher_record', lambda: V.check_supply_chain_evidence(
            paths['supply_chain_artifact'], repository=root,
            component=image['component'], reference=image['reference'],
            version=image['version'], source=image['source'],
            sbom_path=paths['sbom_artifact'], scan_path=paths['scan_artifact'],
            sbom_generator=image['sbom_generator'], scanner_version=image['scanner_version'],
            vulnerability_db_updated_at=image['vulnerability_db_updated_at']))
        records = V.load_json(paths['supply_chain_artifact'])['images']
        records = [r for r in records if r.get('component') == image['component'] and
                   r.get('reference') == image['reference']]
        C.S.R.require(len(records) == 1, 'ADMISSION_PUBLISHER_IDENTITY_MISSING')
        C.S.R.require(hashes == {field: hashlib.sha256(p.read_bytes()).hexdigest()
                                for field, p in paths.items()}, 'ADMISSION_ARTIFACT_CHANGED')
        signature = records[0].get('signature', {})
        rows.append({'component': image['component'], 'reference': image['reference'],
                     'platform': image['platform'], 'artifact_sha256': hashes,
                     'checks': checks,
                     'recorded_publisher': {k: signature.get(k) for k in
                         ('issuer', 'identity', 'workflow_repository', 'workflow_ref', 'commit')},
                     'fresh_cryptographic_verification': False,
                     'canonical_retained_bundle_check': (image['component'], image['version'])
                         in V.CANONICAL_SIGNED_INDEX_PUBLICATIONS})
    C.S.R.require(ledger_sha256 == hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
                  'ADMISSION_LEDGER_CHANGED')
    return {'schema_version': 1, 'mode': 'read_only_local_admission_diagnosis',
            'candidate_identity': candidate['identity'],
            'candidate_sha256': C.digest(candidate), 'ledger_sha256': ledger_sha256,
            'controllers': rows,
            'strict_scan_all_pass': all(r['checks']['strict_scan']['record_check'] == 'pass'
                                        for r in rows),
            'source_evidence': candidate['evidence'],
            'missing_local_evidence': [
                'commit-bound transitive rendered inputs and complete image/workload inventory',
                'explicit local-only risk decision bound to image evidence and exact HIGH findings',
                'reviewed local transport and verified fetched artifact bytes/commit',
                'fresh scoped admission enforcement and applied revision observations'],
            'admission_verified': False, 'gate_acceptance': False,
            'limitations': ['publisher record checks are not fresh cryptographic verification',
                            'Git source commit is not a controller publisher build commit',
                            'native digest syntax is not publisher or vulnerability admission',
                            'no expired exception renewal or strict-policy waiver']}
