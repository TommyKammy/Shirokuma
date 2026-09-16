"""Provider-neutral configuration with an explicit no-cloud execution boundary."""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PROFILE_FIELDS = {"kind", "enabled", "provider", "model", "endpoint", "api_key_env",
                  "budget_usd", "timeout_seconds", "max_output_tokens"}
MOCK_MODEL = "shirokuma-deterministic-v1"


class ProviderError(RuntimeError):
    pass


def validate_endpoint(endpoint: str, *, cloud: bool = False) -> str:
    if not isinstance(endpoint, str) or len(endpoint) > 256:
        raise ProviderError("endpoint must be a bounded URL")
    parsed = urllib.parse.urlsplit(endpoint)
    try:
        port = parsed.port
    except ValueError as error:
        raise ProviderError("invalid provider endpoint port") from error
    if (parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path not in {"/v1", "/v1/"} or not parsed.hostname
            or port is not None and not 1 <= port <= 65535):
        raise ProviderError("endpoint must use /v1 without credentials, query or fragment")
    if cloud:
        if parsed.scheme != "https":
            raise ProviderError("future cloud endpoint must use HTTPS")
    elif parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1", "host.lima.internal"}:
        raise ProviderError("local endpoint must use HTTP on loopback or host.lima.internal")
    return endpoint.rstrip("/")


def validate_config(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "active_provider", "providers"}:
        raise ProviderError("unexpected provider configuration fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ProviderError("unsupported provider configuration schema")
    profiles = value["providers"]
    if not isinstance(profiles, dict) or set(profiles) != {"mock", "local", "cloud"}:
        raise ProviderError("configuration must define mock, local and pending cloud profiles")
    for name, profile in profiles.items():
        if not isinstance(profile, dict) or set(profile) != PROFILE_FIELDS:
            raise ProviderError("profile contains missing or unknown fields; inline secrets are not accepted")
        if profile["kind"] != {"mock": "mock", "local": "local-compatible", "cloud": "cloud"}[name]:
            raise ProviderError("provider kind does not match profile")
        if type(profile["enabled"]) is not bool:
            raise ProviderError("provider enabled flag must be a boolean")
        for key in ("provider", "model"):
            item = profile[key]
            if item is not None and (not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", item)):
                raise ProviderError("provider and model names must be bounded identifiers")
        secret_name = profile["api_key_env"]
        if secret_name is not None and (not isinstance(secret_name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", secret_name)):
            raise ProviderError("api_key_env must be an environment variable name, never a credential")
        budget = profile["budget_usd"]
        if budget is not None and (type(budget) not in {int, float} or not math.isfinite(budget) or not 0 < budget <= 1_000_000):
            raise ProviderError("budget_usd must be a positive finite configured limit or null")
        for key, maximum in (("timeout_seconds", 120), ("max_output_tokens", 4096)):
            if type(profile[key]) is not int or not 1 <= profile[key] <= maximum:
                raise ProviderError("provider request bounds are invalid")
        if profile["endpoint"] is not None:
            validate_endpoint(profile["endpoint"], cloud=name == "cloud")
        if name == "cloud":
            if profile["enabled"]:
                raise ProviderError("cloud execution is pending explicit provider, model and spending authorization")
        elif secret_name is not None or budget is not None:
            raise ProviderError("local baseline does not consume credential variables or cloud budgets")
        if name == "mock" and (profile["provider"] != "shirokuma-mock" or profile["model"] != MOCK_MODEL
                                or profile["endpoint"] is not None):
            raise ProviderError("mock identity and ephemeral endpoint are fixed")
    active = value["active_provider"]
    if active not in {"mock", "local"} or not profiles[active]["enabled"]:
        raise ProviderError("active provider must be an enabled local profile")
    if active == "local" and (not profiles[active]["provider"] or not profiles[active]["model"] or not profiles[active]["endpoint"]):
        raise ProviderError("local provider execution requires an explicit provider, model and endpoint")
    return value


def load_config(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) > 64 * 1024:
        raise ProviderError("provider configuration exceeds the size limit")
    return validate_config(json.loads(raw))


def configuration_presence(config: dict[str, Any]) -> dict[str, bool]:
    # Enumerate names only; do not retrieve provider credential values.
    present = set(os.environ)
    names = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SHIROKUMA_LLM_PROVIDER", "SHIROKUMA_LLM_MODEL"}
    names.update(p["api_key_env"] for p in config["providers"].values() if p["api_key_env"] is not None)
    return {name: name in present for name in sorted(names)}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError("local model requests must not redirect")


class LocalProvider:
    def __init__(self, profile: dict[str, Any], mock_endpoint: str | None = None):
        if profile["kind"] not in {"mock", "local-compatible"} or not profile["enabled"]:
            raise ProviderError("cloud and disabled providers cannot execute in this local baseline")
        self.profile = profile
        self.endpoint = validate_endpoint(mock_endpoint if profile["kind"] == "mock" else profile["endpoint"])
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def complete(self, phase: str, tool_status: str | None = None) -> dict[str, Any]:
        if phase not in {"plan", "summarize"}:
            raise ProviderError("unknown bounded workflow phase")
        body = {"model": self.profile["model"], "temperature": 0,
                "max_tokens": self.profile["max_output_tokens"], "stream": False,
                "messages": [{"role": "system", "content": "Return a JSON object for the bounded Shirokuma workflow. Planning may call only dbt_list_models with empty arguments; summarize only successful tool evidence."},
                             {"role": "user", "content": json.dumps({"phase": phase, "tool_status": tool_status})}]}
        request = urllib.request.Request(self.endpoint + "/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        started = time.monotonic()
        try:
            with self.opener.open(request, timeout=self.profile["timeout_seconds"]) as response:
                raw = response.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                raise ProviderError("model response exceeds the bounded limit")
            result = json.loads(raw)
            if result.get("model") != self.profile["model"]:
                raise ProviderError("response model identity differs from configuration")
            choices = result["choices"]
            if len(choices) != 1 or choices[0].get("finish_reason") != "stop":
                raise ProviderError("model did not return one complete response")
            content = choices[0]["message"]["content"]
            if not isinstance(content, str) or len(content) > 4096:
                raise ProviderError("model content is not bounded structured text")
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ProviderError("model content must be a JSON object")
            usage = result.get("usage") or {}
            for key in ("prompt_tokens", "completion_tokens"):
                count = usage.get(key)
                if count is not None and (type(count) is not int or not 0 <= count <= 2**64 - 1):
                    raise ProviderError("model returned invalid usage counts")
        except ProviderError:
            raise
        except Exception as error:
            # Do not surface provider response bodies, prompts, or endpoint details.
            raise ProviderError("local model request failed or returned an invalid response") from error
        return {"content": parsed, "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"), "inference_cost_estimate": None,
                "duration_ms": max(0, int((time.monotonic() - started) * 1000))}
