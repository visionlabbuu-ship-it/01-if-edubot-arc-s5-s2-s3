"""Jetson two-host collection sidecar (port 9100).

LiteLLM does not forward headers such as X-Arc-Run-Id. Collection identity
must come from POST /marker. Durations and energy are measured on Jetson;
never subtract ARC monotonic from Jetson monotonic.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

import requests

from . import config

log = logging.getLogger("arc.jetson")

_MARKER_TIMEOUT_S = 5.0
_STATUS_TIMEOUT_S = 5.0
_working_sidecar: str | None = None
_sidecar_lock = threading.Lock()
_run_lock = threading.Lock()
_current_run: contextvars.ContextVar["CollectionRun | None"] = contextvars.ContextVar(
    "arc_jetson_run", default=None
)


def current_run() -> "CollectionRun | None":
    return _current_run.get()


def _sidecar_bases() -> list[str]:
    return [base.rstrip("/") for base in config.sidecar_base_candidates()]


def _request_json(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout: float = _STATUS_TIMEOUT_S,
) -> dict[str, Any]:
    global _working_sidecar
    bases = _sidecar_bases()
    with _sidecar_lock:
        preferred = _working_sidecar
    if preferred and preferred in bases:
        bases = [preferred] + [item for item in bases if item != preferred]

    errors: list[str] = []
    for base in bases:
        url = f"{base}{path}"
        try:
            resp = requests.request(
                method,
                url,
                json=json_body,
                timeout=timeout,
                headers={"Content-Type": "application/json"} if json_body is not None else None,
            )
            resp.raise_for_status()
            with _sidecar_lock:
                _working_sidecar = base
            data = resp.json()
            return data if isinstance(data, dict) else {"ok": True, "raw": data}
        except requests.RequestException as exc:
            errors.append(f"{url}: {exc}")
        except ValueError as exc:
            errors.append(f"{url}: invalid JSON ({exc})")
    raise requests.RequestException("; ".join(errors) or "sidecar unreachable")


_QUANT_RE = re.compile(r"(UD-Q\d+_K_XL|Q\d+_K(?:_XL)?)", re.IGNORECASE)


def describe_loaded_model(status: dict[str, Any] | None) -> dict[str, Any]:
    """Read the live Jetson /status payload and name the loaded Q.

    LiteLLM always exposes the API alias `local-llama`. The quantization
    identity is `condition_id` / the GGUF filename from the sidecar.
    """
    blob = dict(status or {})
    nested = blob.get("status")
    if isinstance(nested, dict):
        merged = dict(nested)
        for key, value in blob.items():
            if key != "status" and value not in (None, ""):
                merged.setdefault(key, value)
        blob = merged
    condition_id = str(blob.get("condition_id") or "").strip() or None
    model_path = str(blob.get("model_path") or "").strip() or None
    filename = None
    if model_path:
        filename = model_path.replace("\\", "/").rsplit("/", 1)[-1] or None
    quant = None
    for text in (condition_id, filename, model_path):
        if not text:
            continue
        match = _QUANT_RE.search(str(text))
        if match:
            quant = match.group(1).upper()
            if quant.startswith("Q") and not quant.startswith("UD-"):
                quant = f"UD-{quant}" if "UD-" in str(condition_id or "") else quant
            break
    if not quant:
        quant = condition_id
    family = None
    haystack = " ".join(part for part in (filename, model_path, condition_id) if part)
    if "Qwen3.8-27B" in haystack or "Qwen3.8" in haystack:
        family = "Qwen3.8-27B"
    policy = blob.get("locked_policy") if isinstance(blob.get("locked_policy"), dict) else {}
    ctx_size = (
        blob.get("ctx_size")
        or blob.get("n_ctx")
        or policy.get("ctx_size")
        or policy.get("n_ctx")
    )
    try:
        ctx_size = int(ctx_size) if ctx_size is not None else None
    except (TypeError, ValueError):
        ctx_size = None
    return {
        "condition_id": condition_id,
        "quantization_label": quant,
        "api_alias": str(blob.get("litellm_model") or blob.get("model") or "local-llama"),
        "model_path": model_path,
        "model_filename": filename,
        "model_family": family,
        "ctx_size": ctx_size,
        "llama_ready": bool(blob.get("llama_ready")),
        "litellm_ready": bool(blob.get("litellm_ready")),
        "server_start_id": blob.get("server_start_id"),
        "condition_snapshot_sha256": blob.get("condition_snapshot_sha256"),
        "jetson_host_id": blob.get("jetson_host_id"),
        "boot_id": blob.get("boot_id"),
    }


def fetch_status(timeout: float = _STATUS_TIMEOUT_S) -> dict[str, Any]:
    return _request_json("GET", "/status", timeout=timeout)


def post_marker(payload: dict[str, Any]) -> dict[str, Any]:
    return _request_json("POST", "/marker", json_body=payload, timeout=_MARKER_TIMEOUT_S)


def fetch_run(run_id: str, timeout: float = _STATUS_TIMEOUT_S) -> dict[str, Any]:
    return _request_json("GET", f"/runs/{run_id}", timeout=timeout)


def wait_run_complete(
    run_id: str,
    timeout_s: float | None = None,
    poll_s: float = 0.4,
) -> dict[str, Any]:
    deadline = time.monotonic() + float(
        config.JETSON_WAIT_BUNDLE_S if timeout_s is None else timeout_s
    )
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            last = fetch_run(run_id)
        except requests.RequestException:
            last = {"run_id": run_id, "status": "unknown"}
        if str(last.get("status") or "") == "complete":
            return last
        time.sleep(poll_s)
    last = dict(last)
    last.setdefault("run_id", run_id)
    last.setdefault("status", "timeout")
    return last


def probe() -> dict[str, Any]:
    status = fetch_status()
    loaded = describe_loaded_model(status)
    return {
        "ok": bool(status.get("ok"))
        and bool(status.get("llama_ready"))
        and bool(status.get("litellm_ready")),
        "sidecar": _working_sidecar or config.JETSON_SIDECAR_BASE_URL,
        "chat_url": status.get("litellm_url")
        or f"{config.SIM_LLM_BASE_URL}/v1/chat/completions",
        "model": loaded.get("api_alias") or config.SIM_LLM_MODEL,
        "condition_id": loaded.get("condition_id") or config.JETSON_CONDITION_ID or None,
        "quantization_label": loaded.get("quantization_label"),
        "api_alias": loaded.get("api_alias"),
        "model_path": loaded.get("model_path"),
        "model_filename": loaded.get("model_filename"),
        "model_family": loaded.get("model_family"),
        "ctx_size": loaded.get("ctx_size"),
        "llama_ready": bool(status.get("llama_ready")),
        "litellm_ready": bool(status.get("litellm_ready")),
        "server_start_id": loaded.get("server_start_id"),
        "condition_snapshot_sha256": loaded.get("condition_snapshot_sha256"),
        "status": status,
        "loaded": loaded,
    }


class CollectionRun:
    """One ARC task = one run_id. Each LLM call is a turn in that run."""

    def __init__(
        self,
        *,
        task_id: str,
        run_id: str | None = None,
        condition_id: str | None = None,
        wait_bundle: bool | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.task_id = str(task_id or "arc-task")
        self.run_id = run_id or f"arc-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self.condition_id = (condition_id or config.JETSON_CONDITION_ID or "").strip()
        self.wait_bundle = config.JETSON_WAIT_BUNDLE if wait_bundle is None else bool(wait_bundle)
        self.enabled = config.JETSON_MARKERS_ENABLED if enabled is None else bool(enabled)
        self._next_turn = 0
        self._token: contextvars.Token | None = None
        self._started = False
        self._finished = False
        self.last_error: str | None = None
        self.status: dict[str, Any] | None = None
        self.loaded: dict[str, Any] | None = None
        self.bundle: dict[str, Any] | None = None

    def __enter__(self) -> "CollectionRun":
        self._token = _current_run.set(self)
        if not self.enabled:
            return self
        with _run_lock:
            try:
                self.status = fetch_status()
            except requests.RequestException as exc:
                self.last_error = str(exc)
                log.warning("Jetson sidecar status failed; chat continues without markers: %s", exc)
                self.enabled = False
                return self
            if not self.status.get("llama_ready") or not self.status.get("litellm_ready"):
                self.last_error = (
                    f"Jetson not ready llama_ready={self.status.get('llama_ready')} "
                    f"litellm_ready={self.status.get('litellm_ready')}"
                )
                log.warning("%s", self.last_error)
            loaded = describe_loaded_model(self.status)
            if not self.condition_id:
                self.condition_id = str(loaded.get("condition_id") or "").strip()
            self.loaded = loaded
            try:
                post_marker(
                    {
                        "marker_type": "run_started",
                        "run_id": self.run_id,
                        "task_id": self.task_id,
                        "condition_id": self.condition_id or "unknown",
                    }
                )
                self._started = True
            except requests.RequestException as exc:
                self.last_error = str(exc)
                log.warning("run_started marker failed; chat continues without markers: %s", exc)
                self.enabled = False
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.finish()
        finally:
            if self._token is not None:
                _current_run.reset(self._token)
                self._token = None

    def finish(self) -> dict[str, Any] | None:
        if self._finished:
            return self.bundle
        self._finished = True
        if not self.enabled or not self._started:
            return None
        try:
            post_marker({"marker_type": "run_finished", "run_id": self.run_id})
        except requests.RequestException as exc:
            self.last_error = str(exc)
            log.warning("run_finished marker failed: %s", exc)
            return None
        if self.wait_bundle:
            self.bundle = wait_run_complete(self.run_id)
        else:
            self.bundle = {"run_id": self.run_id, "status": "finishing"}
        return self.bundle

    @contextmanager
    def request(self) -> Iterator[dict[str, Any]]:
        info = {
            "run_id": self.run_id,
            "request_id": f"req-{uuid.uuid4().hex[:12]}",
            "turn_index": self._next_turn,
        }
        self._next_turn += 1
        if self.enabled and self._started:
            try:
                post_marker(
                    {
                        "marker_type": "request_started",
                        "run_id": self.run_id,
                        "request_id": info["request_id"],
                        "turn_index": info["turn_index"],
                    }
                )
            except requests.RequestException as exc:
                self.last_error = str(exc)
                log.warning("request_started marker failed: %s", exc)
        try:
            yield info
        finally:
            if self.enabled and self._started:
                try:
                    post_marker(
                        {
                            "marker_type": "request_finished",
                            "run_id": self.run_id,
                            "request_id": info["request_id"],
                            "turn_index": info["turn_index"],
                        }
                    )
                except requests.RequestException as exc:
                    self.last_error = str(exc)
                    log.warning("request_finished marker failed: %s", exc)

    def summary(self) -> dict[str, Any]:
        bundle = self.bundle or {}
        loaded = self.loaded or {}
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "condition_id": self.condition_id,
            "quantization_label": loaded.get("quantization_label") or self.condition_id,
            "api_alias": loaded.get("api_alias") or "local-llama",
            "model_filename": loaded.get("model_filename"),
            "markers_enabled": bool(self.enabled and self._started),
            "turn_count": self._next_turn,
            "run_status": bundle.get("status") or ("started" if self._started else "skipped"),
            "bundle_dir": bundle.get("bundle_dir"),
            "status_url": f"{(_working_sidecar or config.JETSON_SIDECAR_BASE_URL).rstrip('/')}/runs/{self.run_id}",
            "error": self.last_error,
        }


def maybe_run(*, task_id: str, **kwargs: Any) -> CollectionRun:
    return CollectionRun(task_id=task_id, **kwargs)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Probe the Jetson collection sidecar")
    parser.add_argument("--chat", action="store_true", help="also send a tiny chat completion")
    parser.add_argument("--wait-bundle", action="store_true")
    args = parser.parse_args()
    try:
        info = probe()
    except requests.RequestException as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    if not args.chat:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0 if info.get("ok") else 2

    from .sim_llm_chat import _chat_completion

    with CollectionRun(task_id="jetson-probe", wait_bundle=args.wait_bundle) as run:
        with run.request():
            completion = _chat_completion(
                [
                    {"role": "system", "content": "ตอบสั้นมาก"},
                    {"role": "user", "content": "สวัสดี ตอบคำเดียว"},
                ],
                use_tools=False,
                max_tokens=32,
            )
    message = ((completion.get("choices") or [{}])[0].get("message") or {}).get("content")
    print(
        json.dumps(
            {
                **info,
                "chat_ok": bool(message),
                "reply": message,
                "jetson": run.summary(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if info.get("ok") and message else 2


if __name__ == "__main__":
    raise SystemExit(main())
