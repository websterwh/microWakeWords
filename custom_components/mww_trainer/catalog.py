"""Publish trained models to Home Assistant's documented custom_wake_words catalog.

EchoLocal and techo5 download Home Assistant's offered custom wake words
through their ESPHome-compatible native API. HA reads this folder on start,
so publication is NOT confirmation that any satellite installed the model.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from homeassistant.exceptions import HomeAssistantError

_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
_MAX_MODEL = 1024 * 1024  # techo5's documented limit


def _write_pair(root: Path, name: str, manifest: dict, model: bytes):
    if not _NAME.fullmatch(name):
        raise HomeAssistantError("Invalid model filename")
    if len(model) > _MAX_MODEL or not model:
        raise HomeAssistantError("Model exceeds the 1 MiB satellite limit")
    if manifest.get("type") != "micro":
        raise HomeAssistantError("Only microWakeWord models are supported")
    root.mkdir(parents=True, exist_ok=True)
    manifest = dict(manifest)
    manifest["model"] = name + ".tflite"
    if not manifest.get("wake_word"):
        raise HomeAssistantError("Manifest has no wake_word")
    payload = (json.dumps(manifest, indent=2) + "\n").encode()
    # Each file is atomically replaced to avoid a truncated model. Keeping
    # the JSON replacement last minimizes mismatch during concurrent reads.
    for destination, data in ((root / (name + ".tflite"), model),
                              (root / (name + ".json"), payload)):
        fd, temporary = tempfile.mkstemp(dir=root, prefix=".mww-")
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    digest = hashlib.sha256((root / (name + ".tflite")).read_bytes()).hexdigest()
    if digest != hashlib.sha256(model).hexdigest():
        raise HomeAssistantError("Published model hash verification failed")
    return digest


async def publish(hass, name: str, manifest: dict, model: bytes):
    """Write under HA's config directory using an executor."""
    destination = Path(hass.config.path("custom_wake_words"))
    return await hass.async_add_executor_job(_write_pair, destination, name, manifest, model)
