"""Caller-query evidence after wake; audit spans never backdate query execution.

The fixed caller SELECT is distinct from the existing wake probes. Its measured
duration includes wake/probes and cannot establish cold-query performance.
"""
import copy
import json
import sys
import uuid

from . import telemetry as T
from .client import SELECT, QUERY_ID, expected_rows
from .support import digest, read, token

QUERY_KEYS = {"warehouse", "request_id", "case", "query_id", "source", "user", "sql_sha256",
              "rows_observed", "rows_sha256", "post_count", "response_complete", "matched",
              "wake_verified", "terminal_verified", "all_results_consumed", "wake_wait_ms", "query_client_ms"}
PUBLIC_KEYS = {"case", "query_id", "rows_observed", "rows_sha256", "post_count", "response_complete", "matched"}
require = T.require


def project_query(value, duration_ms, run_token):
    token(run_token)
    require(type(value) is dict and set(value) == QUERY_KEYS, "QUERY_AUDIT_PROJECTION_INVALID")
    try:
        request = uuid.UUID(value["request_id"])
        require(type(value["request_id"]) is str and str(request) == value["request_id"], "QUERY_AUDIT_REQUEST_INVALID")
    except (ValueError, TypeError, AttributeError):
        raise T.TelemetryError("QUERY_AUDIT_REQUEST_INVALID") from None
    require(value["warehouse"] == "XS" and value["case"] == "xs_caller"
        and type(value["query_id"]) is str and QUERY_ID.fullmatch(value["query_id"])
        and value["source"] == "shirokuma-wl-" + run_token + "-xs_caller-" + request.hex
        and value["user"] == "shirokuma-wl-" + run_token + "-worker"
        and value["sql_sha256"] == digest(SELECT.encode())
        and value["rows_sha256"] == digest(expected_rows(run_token, "xs")), "QUERY_AUDIT_IDENTITY_INVALID")
    require(type(value["rows_observed"]) is int and value["rows_observed"] == 25
        and type(value["post_count"]) is int and value["post_count"] == 1
        and all(value[k] is True for k in ("response_complete", "matched", "wake_verified", "terminal_verified", "all_results_consumed")),
        "QUERY_AUDIT_RESULT_UNVERIFIED")
    require(type(duration_ms) is int and 0 <= duration_ms < 2**64
        and all(type(value[k]) is int and 0 <= value[k] <= duration_ms for k in ("wake_wait_ms", "query_client_ms"))
        and value["wake_wait_ms"] + value["query_client_ms"] <= duration_ms, "QUERY_AUDIT_DURATION_INVALID")
    return copy.deepcopy(value)


class LifecycleQueryTelemetry(T.LifecycleTelemetry):
    """Optional short-lived query session; the original lifecycle path is unchanged."""

    def query(self, projection, duration_ms):
        from opentelemetry.trace import Status, StatusCode
        require(self.started is not None and not self.closed and not self.completed
            and self.journal["result"] == "running" and self.journal["pending_action"] is None,
            "LIFECYCLE_TELEMETRY_STATE_INVALID")
        records = self.journal["records"]
        require(len(records) == 3 and all(row["event"]["action_type"] == "warehouse.lifecycle." + action
            and row["event"]["status"] == T.STATUSES[action] and row["delivery"] == "verified"
            and row["agenthouse_verified"] is True and row["loki_verified"] is True
            for row,action in zip(records,T.ACTIONS)), "QUERY_AUDIT_ORDER_INVALID")
        value = project_query(projection, duration_ms, self.journal["run_token"])
        self.journal["pending_action"] = "query"
        self.save()
        try:
            with self.tracer.start_as_current_span("warehouse.lifecycle.audit.query",
                    record_exception=False, set_status_on_exception=False) as span:
                attributes = dict(task_id=self.journal["task_id"], action="query", decision_status="success",
                    duration_kind="audit-delivery-not-controller-decision", controller_duration_ms=duration_ms,
                    projection_sha256=digest(value), wake_wait_ms=value["wake_wait_ms"],
                    query_client_ms=value["query_client_ms"], includes_wake_probes=True)
                for key,item in attributes.items(): span.set_attribute(key,item)
                try:
                    self._emit("warehouse.lifecycle.query", "success", duration_ms, span, value)
                    span.set_attribute("delivery_verified", True)
                    span.set_status(Status(StatusCode.OK))
                except BaseException:
                    span.set_attribute("delivery_verified", False)
                    span.set_status(Status(StatusCode.ERROR))
                    raise
        except BaseException:
            self.journal["result"] = "failed"
            raise
        finally:
            failure = sys.exc_info()[1]
            self.journal["pending_action"] = None
            try:
                self.save()
            except BaseException:
                if failure is not None and not isinstance(failure,Exception): raise failure
                raise

    def finish(self, receipt):
        require(self.started is not None and not self.closed and not self.completed
            and self.journal["result"] == "running" and self.journal["pending_action"] is None,
            "LIFECYCLE_RUN_NOT_VERIFIED")
        require(type(receipt) is dict and receipt.get("run_token") == self.journal["run_token"]
            and receipt.get("result") == "verified" and receipt.get("pending") is None
            and type(receipt.get("observations")) is dict and receipt["observations"].get("result") == "verified",
            "LIFECYCLE_RUN_NOT_VERIFIED")
        from .session import verify_query_controller
        verify_query_controller(receipt["observations"], self.journal["run_token"])
        cleanup = receipt.get("cleanup")
        require(type(cleanup) is dict and cleanup.get("verified") is True
            and cleanup.get("conflicts") == [] and cleanup.get("deferred") == [], "LIFECYCLE_RUN_NOT_VERIFIED")
        records = self.journal["records"]
        require(len(records) == 4 and all(row["event"]["action_type"] == "warehouse.lifecycle." + action
            and row["event"]["status"] == (T.STATUSES[action] if action in T.STATUSES else "success")
            and row["delivery"] == "verified" and row["agenthouse_verified"] is True and row["loki_verified"] is True
            for row,action in zip(records,(*T.ACTIONS,"query"))), "LIFECYCLE_DECISIONS_INCOMPLETE")
        caller = receipt["observations"].get("caller")
        keys = {"request_id", "case", "result", "stage", "post_intents", "query", "projection", "wake_wait_ms",
                "query_client_ms", "duration_ms", "audit_attempted", "audit_verified", "failure_code"}
        require(type(caller) is dict and set(caller) == keys and caller["result"] == "verified"
            and caller["stage"] == "complete" and caller["audit_attempted"] is True and caller["audit_verified"] is True
            and caller["failure_code"] is None and type(caller["post_intents"]) is int and caller["post_intents"] == 1,
            "QUERY_CALLER_NOT_VERIFIED")
        value = project_query(caller["projection"],caller["duration_ms"],self.journal["run_token"])
        require(all(caller[k] == value[k] for k in ("request_id","case","wake_wait_ms","query_client_ms"))
            and all(type(caller[k]) is int for k in ("wake_wait_ms","query_client_ms"))
            and digest(value) == digest(records[-1]["projection"])
            and caller["duration_ms"] == records[-1]["event"]["duration_ms"]
            and digest(caller["query"]) == digest({k:value[k] for k in PUBLIC_KEYS}), "QUERY_CALLER_AUDIT_MISMATCH")
        self.record_cleanup(cleanup)
        self.completed = True
        self.save()

    def _verify_spans(self):
        self.journal["saved_spans_verified"] = False
        try:
            self._verify_query_spans()
        except BaseException:
            self.journal["saved_spans_verified"] = False
            raise
        self.journal["saved_spans_verified"] = True

    def _verify_query_spans(self):
        super()._verify_spans()
        rows = [json.loads(line) for line in read(self.spans,1024**2).decode().splitlines()]
        for row in self.journal["records"]:
            if row["event"]["action_type"] != "warehouse.lifecycle.query": continue
            value = project_query(row["projection"],row["event"]["duration_ms"],self.journal["run_token"])
            span = next(item for item in rows if item["span_id"] == row["span_id"])
            attrs = span["attributes"]
            require(all(type(attrs.get(k)) is int for k in ("wake_wait_ms","query_client_ms"))
                and attrs.get("wake_wait_ms") == value["wake_wait_ms"]
                and attrs.get("query_client_ms") == value["query_client_ms"]
                and attrs.get("includes_wake_probes") is True, "QUERY_AUDIT_SPAN_MISMATCH")
