"""
Host capability profiler for the table-enhancement tier selection (plan A).

Detects host hardware/software capability ONCE on first use, caches the result
to ``host_profile.local.json`` (git-ignored via the ``*.local.*`` rule), and
maps it to one of three enhancement tiers:

  - "vision"    : GPU/MPS available OR large free memory AND a local vision
                  model is usable -> strongest enhancement (offline VLM).
  - "gridboost" : CPU-only but PaddleOCR/PP-Structure usable -> pure-OpenCV
                  preprocessing + PP-Structure (current host lands here).
  - "manual"    : no PP-Structure or too few resources -> no enhancement,
                  keep plan B main output + low-confidence warning (human review).

The profile only decides WHICH backend to use *if* enhancement is enabled; it
never changes the default (enhancement stays off unless explicitly enabled).
A cached profile is reused as-is; pass force_rescan=True (or use the --rescan
entry point) to rebuild it.
"""

import json
import logging
import os
import shutil
from typing import Any, Dict, Optional

logger = logging.getLogger("ocr_pipeline")

PROFILE_FILENAME = "host_profile.local.json"
VISION_MIN_FREE_GB = 8.0          # free RAM threshold to consider a VLM viable
PROFILE_SCHEMA_VERSION = 1

TIER_VISION = "vision"
TIER_GRIDBOOST = "gridboost"
TIER_MANUAL = "manual"


def _detect_memory_gb():
    """Return (total_gb, free_gb) best-effort; (0.0, 0.0) if unknown."""
    try:
        import psutil
        vm = psutil.virtual_memory()
        return round(vm.total / (1024 ** 3), 2), round(vm.available / (1024 ** 3), 2)
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


def _detect_cpu_count():
    try:
        return os.cpu_count() or 0
    except Exception:  # noqa: BLE001
        return 0


def _detect_gpu():
    """Return a dict describing accelerator availability.

    Keys: cuda (bool), mps (bool), kind (str). Uses torch when present; falls
    back to 'none' silently so a missing torch never breaks profiling.
    """
    info = {"cuda": False, "mps": False, "kind": "none"}
    try:
        import torch
        if torch.cuda.is_available():
            info["cuda"] = True
            info["kind"] = "cuda"
            return info
        mps = getattr(getattr(torch, "backends", None), "mps", None)
        if mps is not None and mps.is_available():
            info["mps"] = True
            info["kind"] = "mps"
            return info
    except Exception:  # noqa: BLE001
        pass
    return info


def _lib_available(module_name):
    try:
        import importlib.util
        return importlib.util.find_spec(module_name) is not None
    except Exception:  # noqa: BLE001
        return False


def _detect_ollama_vision_models():
    """Return a list of locally available ollama vision model tags (best-effort).

    Never blocks the pipeline: if the ollama CLI is missing or slow, returns [].
    """
    exe = shutil.which("ollama")
    if not exe:
        return []
    try:
        import subprocess
        out = subprocess.run(
            [exe, "list"], capture_output=True, text=True, timeout=8,
        )
    except Exception:  # noqa: BLE001
        return []
    if out.returncode != 0 or not out.stdout:
        return []
    vision_keys = ("vl", "llava", "vision", "bakllava", "minicpm-v", "moondream")
    models = []
    for line in out.stdout.splitlines()[1:]:
        name = line.split()[0] if line.split() else ""
        low = name.lower()
        if any(k in low for k in vision_keys):
            models.append(name)
    return models


def detect_capabilities():
    """Run all detectors and return a raw metrics dict (no tier decision)."""
    total_gb, free_gb = _detect_memory_gb()
    gpu = _detect_gpu()
    caps = {
        "cpu_count": _detect_cpu_count(),
        "total_memory_gb": total_gb,
        "free_memory_gb": free_gb,
        "gpu": gpu,
        "has_paddleocr": _lib_available("paddleocr"),
        "has_torch": _lib_available("torch"),
        # Vision-model probe is deferred: only hosts with hardware potential run
        # it (see decide_tier), because `ollama list` can cold-start a server
        # and block. None => "not probed yet".
        "ollama_vision_models": None,
    }
    return caps


def vision_capable(caps):
    """True when the host has hardware potential to run a local vision model.

    Potential = an accelerator (CUDA/MPS) OR enough free RAM. This is about
    hardware capacity; whether the software/model is actually installed is a
    separate check (see missing_vision_requirements).
    """
    gpu = caps.get("gpu", {}) or {}
    if gpu.get("cuda") or gpu.get("mps"):
        return True
    return float(caps.get("free_memory_gb", 0.0)) >= VISION_MIN_FREE_GB


def missing_vision_requirements(caps):
    """Return a list of missing pieces needed to actually run the vision tier.

    Empty list => vision tier is ready. Non-empty => host has potential but is
    missing software/model, which is the case where the user should be asked
    whether to install.
    """
    missing = []
    if not caps.get("has_torch"):
        missing.append("torch (deep-learning runtime)")
    if not caps.get("ollama_vision_models"):  # None or [] both mean "not usable"
        missing.append("a local vision model (e.g. `ollama pull qwen2.5vl`)")
    return missing


def decide_tier(caps):
    """Map capabilities to a tier plus a reason and any missing requirements.

    Returns dict: {tier, reason, vision_potential, missing}.
    """
    vision_potential = vision_capable(caps)
    if vision_potential and caps.get("ollama_vision_models") is None:
        # Lazily probe local vision models only for capable hosts.
        caps["ollama_vision_models"] = _detect_ollama_vision_models()
    missing = missing_vision_requirements(caps) if vision_potential else []

    if vision_potential and not missing:
        return {"tier": TIER_VISION, "reason": "accelerator or ample RAM + local vision model available",
                "vision_potential": True, "missing": []}

    # Not vision-ready: choose gridboost when PP-Structure is usable, else manual.
    if caps.get("has_paddleocr"):
        reason = "CPU-only PP-Structure preprocessing (gridboost)"
        if vision_potential and missing:
            reason = "vision-capable host but missing: " + ", ".join(missing) + "; using gridboost until installed"
        return {"tier": TIER_GRIDBOOST, "reason": reason,
                "vision_potential": vision_potential, "missing": missing}

    return {"tier": TIER_MANUAL, "reason": "no PP-Structure / insufficient resources; human review only",
            "vision_potential": vision_potential, "missing": missing}


def build_profile(caps=None):
    """Assemble a full, serializable profile (metrics + decision + timestamp)."""
    import datetime as _dt
    caps = caps if caps is not None else detect_capabilities()
    decision = decide_tier(caps)
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "detected_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "capabilities": caps,
        "tier": decision["tier"],
        "reason": decision["reason"],
        "vision_potential": decision["vision_potential"],
        "missing_vision_requirements": decision["missing"],
    }


def _profile_path(base_dir):
    return os.path.join(base_dir or ".", PROFILE_FILENAME)


def load_or_create_profile(base_dir=".", force_rescan=False):
    """Return the host profile, reading the cache unless force_rescan is set.

    On first run (or --rescan) it detects capabilities and writes the cache.
    A malformed/old-schema cache is transparently rebuilt.
    """
    path = _profile_path(base_dir)
    if not force_rescan and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                cached = json.load(fh)
            if cached.get("schema_version") == PROFILE_SCHEMA_VERSION and cached.get("tier"):
                return cached
            logger.info("Host profile schema changed; rescanning.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Host profile cache unreadable (%s); rescanning.", exc)

    profile = build_profile()
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(profile, fh, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write host profile cache: %s", exc)
    return profile