# Local Data Activator experiment

This connects one fixed Grafana rule to an actual Dagster sensor and the existing
deterministic SRE reader. Run from the active local checkout:

```sh
rtk proxy make local-activation-enable
rtk proxy make local-activation-status
rtk proxy make local-activation-run
```

Enabling Alerting stops the owned Grafana container long enough to save a private
cold volume backup and check that its database contains zero alert rules. The
original container is retained stopped, and the same pinned ARM64 image, volume,
credentials, reader permissions, networks and loopback port are reused. Backup
and operation receipts live under `tmp/local-experiment/grafana/alerting-operations/`.
This adds one retained container and a full Grafana volume tar plus a database copy.

A run creates a fresh `activation-<token>` task. The fixed rule starts paused;
only after the empty contact point and its dedicated route are verified does it
evaluate the latest status of that task's `activation_fixture` Pawprints. Existing
notification configuration is saved and retained. Grafana explicitly supports
[contact points with no integrations](https://grafana.com/docs/grafana/latest/alerting/fundamentals/notifications/contact-points/),
which send no notifications. No notification test is sent.
Notification writes use the pinned Grafana 13.2.2
`notifications.alerting.grafana.app/v1beta1` Receiver and RoutingTree APIs,
including the current routing tree resource version. The legacy aggregate
configuration endpoint is read only to verify that existing settings remain intact.

The bounded sequence is Normal → append a new failed fixture → Firing → one
Dagster job → duplicate sensor tick with no new job → append a new success fixture
→ Normal. The job runs `shirokuma_doctor`, `k8s_read_pods`, and `k8s_read_events`
through the existing audited SRE reader. It uses no LLM and performs no repair.
Its output is a general local diagnostic, not proof of the synthetic failure's
cause. There is no resident sensor daemon or unattended monitoring after the
command exits.

Private intents, API responses, sensor claims and results are retained under
`tmp/local-experiment/activation/`. A durable claim is written before a diagnostic
starts. The same alert fingerprint does not run twice. A lost reply, incomplete
claim or unresolved prior run blocks new work rather than automatically retrying.
Failures preserve their original evidence; inspect and reconcile that specific
operation before continuing. Do not delete claims to manufacture a retry.

The migration supports explicit resume and configuration rollback:

```sh
rtk proxy python3 -B tools/local_experiment/activation/runtime.py resume OPERATION_TOKEN
rtk proxy python3 -B tools/local_experiment/activation/runtime.py rollback OPERATION_TOKEN
```

Rollback retains the new container stopped and restarts the original container
with Alerting disabled, using the same volume. It does not rewind the database or
restore over retained data. The backup is preserved for separate investigation.
`make test-local-activation` runs offline tests; `make verify-local-experiment`
includes them. Actual runtime evidence is recorded in
[WP-L5-ACT-001](../../../docs/design/06_WorkPackages/L5/WP-L5-ACT-001_Data_Activator_style_alerts.md).
