"""Shared hardware-config backup used by SVS and hardwareService."""

DEFAULT_HARDWARE_CONFIG = {
    "_comment_purpose": "Single source of truth for hardware configuration, shared by consumer app and hardwareService.",
    "_comment_configVersion": "Bump configVersion when adding/removing required fields or changing schema structure.",
    "_comment_serialNumber": "Optional serial number for exact device matching. Use null for type-based matching. Fill in for multiple boards of same type.",
    "_comment_name": "CRITICAL: Unique receiver name used throughout the system. This name MUST match keys in config/config.json for tcpPorts and telemetryPriority! Use unique names for multiple boards of same type (e.g., 'X5-Primary', 'X5-Backup').",
    "_comment_oscope": "hardwareService loads this single oscope configuration.",
    "_comment_ppsWiring": "Physical PPS channel wiring. Each entry has 'name' (signal label) and 'editable' (user can rename). Connected receivers should have editable=false.",
    "configVersion": "1.0",
    "hardware": {
        "receivers": [
            {"type": "F9", "serialNumber": None, "name": "F9"},
            {"type": "X5", "serialNumber": None, "name": "X5"}
        ],
        "receiversOld": [
            {"type": "X5", "serialNumber": None, "name": "X5"},
            {"type": "M9", "serialNumber": None, "name": "M9"}
        ],
        "oscope": {"type": "digital", "triggerChannel": 0}
    },
    "ppsWiring": {
        "0": {"name": "PPS In", "editable": False},
        "1": {"name": "X5", "editable": False},
        "2": {"name": "M9", "editable": False},
        "3": {"name": "Channel 3", "editable": True},
        "4": {"name": "Channel 4", "editable": True},
        "5": {"name": "Channel 5", "editable": True},
        "6": {"name": "Channel 6", "editable": True},
        "7": {"name": "Channel 7", "editable": True}
    }
}
