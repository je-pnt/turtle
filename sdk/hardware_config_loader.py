"""Utility helpers for loading hardware-config.json with shared fallbacks."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Optional, Tuple, Callable

from .hardware_config_defaults import DEFAULT_HARDWARE_CONFIG

HardwareConfigResult = Tuple[dict, str, bool]


def _validate_hardware_config(config: dict) -> None:
    if not isinstance(config, dict):
        raise ValueError('hardware-config is not a JSON object')
    hardware = config.get('hardware')
    if not isinstance(hardware, dict):
        raise ValueError("Missing 'hardware' section")
    receivers = hardware.get('receivers')
    if not isinstance(receivers, list):
        raise ValueError("Missing 'receivers' list")


def load_hardware_config(path: str | Path, log: Optional[object] = None,
                         *, flush_on_error: Optional[Callable[[], None]] = None) -> HardwareConfigResult:
    """Load hardware-config.json, falling back to immutable defaults on error."""
    cfg_path = Path(path)

    try:
        with cfg_path.open('r', encoding='utf-8-sig') as f:
            config = json.load(f)
        _validate_hardware_config(config)
        version = config.get('configVersion', '1.0')
        if log:
            log.info('Loaded hardware-config.json', event='hardwareConfigLoad', component='HardwareConfig',
                     configPath=str(cfg_path), configVersion=version)
        return config, version, False
    except Exception as exc:
        if log:
            log.error('Failed to load hardware-config.json', event='hardwareConfigLoadError', component='HardwareConfig',
                      configPath=str(cfg_path), errorClass=type(exc).__name__, errorMsg=str(exc))
        if flush_on_error:
            try:
                flush_on_error()
            except Exception as flush_exc:
                if log:
                    log.error('Failed to flush hardware config state', event='hardwareConfigFlushError',
                              component='HardwareConfig', errorClass=type(flush_exc).__name__, errorMsg=str(flush_exc))

        fallback = copy.deepcopy(DEFAULT_HARDWARE_CONFIG)
        version = fallback.get('configVersion', 'backup')
        if log:
            log.warning('Loaded hardware-config backup defaults', event='hardwareConfigBackupLoad',
                        component='HardwareConfig', configVersion=version, flushed=bool(flush_on_error))
        return fallback, version, True
