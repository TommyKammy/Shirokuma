"""Frozen protocol and resource planning, never resource application."""
import json
import math
from pathlib import Path

GIB = 1024 ** 3
LIMIT = 2_000_000_000_000


def protocol():
    value = json.loads(Path(__file__).with_name("protocol.json").read_text())
    validate(value)
    return value


def validate(value):
    if (value["protocol_version"] != 1 or value["campaign_scope"] != "local-only"
            or value["physical_additional_limit_bytes"] != LIMIT
            or value["logical_limit_bytes"] != LIMIT):
        raise ValueError("unsupported protocol or changed hard ceiling")
    if not math.isfinite(value["host_reserve_bytes"]) or value["host_reserve_bytes"] < 256 * GIB:
        raise ValueError("host reserve below protocol")
    resources = [value["host"]["cpu"], value["host"]["ram_gib"], *value["host_ram_reserves_gib"].values()]
    resources += [v for p in value["profiles"].values() for k, v in p.items() if k != "conditional"]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in resources):
        raise ValueError("positive finite resources required")
    for p in value["profiles"].values():
        if p["vm_cpu"] > 30 or p["vm_ram_gib"] + sum(value["host_ram_reserves_gib"].values()) > value["host"]["ram_gib"]:
            raise ValueError("host resource budget exceeded")
        if not (p["worker_cpu"] < p["vm_cpu"] and p["worker_ram_gib"] < p["vm_ram_gib"]
                and p["query_gib"] + p["headroom_gib"] < p["heap_gib"] < p["worker_ram_gib"]):
            raise ValueError("invalid worker budget")
    return value


def plan(profile="L1"):
    p = protocol()
    requested = p["profiles"][profile]
    return {"applies_changes": False, "actual_observed": p["observed_2026_11_13"],
            "requested": requested, "next_target": "L1",
            "host_ram_reserves_gib": p["host_ram_reserves_gib"],
            "shared_baseline_reserve": {"cpu": 8, "ram_gib": 64}, "conditional_review_required": requested["conditional"],
            "remaining_vm_cpu": requested["vm_cpu"] - requested["worker_cpu"],
            "remaining_vm_ram_gib": requested["vm_ram_gib"] - requested["worker_ram_gib"],
            "remaining_host_ram_gib": p["host"]["ram_gib"] - requested["vm_ram_gib"] - sum(p["host_ram_reserves_gib"].values()),
            "remaining_host_cpu_before_other_vm_and_generator": 32 - requested["vm_cpu"],
            "cpu_note": "other VM 4 vCPU and generator share CPUs; vCPU is not a dedicated P-core"}


def settings(mode="qualification", scale=None, peak=None, timeout=None, repetitions=None, peak_evidence=None):
    if mode not in ("qualification", "performance"):
        raise ValueError("unknown mode")
    if mode == "performance" and any(x is None for x in (scale, peak, timeout, repetitions)):
        raise ValueError("performance needs explicit scale, peak, timeout, repetitions")
    scale = 0.01 if scale is None else scale
    timeout = 120 if timeout is None else timeout
    repetitions = 1 if repetitions is None else repetitions
    if not all(not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x) and x > 0 for x in (scale, timeout)):
        raise ValueError("positive finite scale/timeout required")
    if type(repetitions) is not int or not 1 <= repetitions <= 100:
        raise ValueError("repetitions must be 1..100")
    if scale > LIMIT / 1_000_000_000:
        raise ValueError("logical scale exceeds hard ceiling")
    logical = math.ceil(scale * 1_000_000_000)
    if logical > LIMIT or (mode == "qualification" and logical > 1_000_000_000):
        raise ValueError("logical scale exceeds mode budget")
    # Source DB + export/copy + spill/sort + results. Conservative initial estimate,
    # not a compression prediction; caller may increase it using pilot evidence.
    minimum_peak = max(256 * 1024 ** 2, math.ceil(logical * 6))
    peak = minimum_peak if peak is None else peak
    if type(peak) is not int or not 0 < peak <= LIMIT:
        raise ValueError("positive peak within 2TB ceiling required")
    if peak_evidence is not None and (not isinstance(peak_evidence, str) or not peak_evidence.strip()):
        raise ValueError("peak evidence must be a nonempty estimate/breakdown and pilot evidence")
    if peak < minimum_peak and not peak_evidence:
        raise ValueError("peak below default requires estimate/breakdown and measured pilot evidence")
    return dict(mode=mode, scale=scale, logical_bytes=logical, peak_bytes=peak,
                default_peak_estimate_bytes=minimum_peak, peak_evidence=peak_evidence,
                timeout_s=timeout, repetitions=repetitions, threads=2, memory_gib=4,
                performance_claim=False, final_slo_pass=False)
