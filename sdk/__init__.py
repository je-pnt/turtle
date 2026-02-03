"""sdk - Software Development Kit for SVS Applications

Contains reusable modules for:
    - parsers: GNSS message parsing (NMEA, SBF, UBX)
    - transport: Inter-process communication (NNG, NATS)
    - globe: Geodetic and orbital calculations
    - logging: Centralized logging and data management
    - hardwareService: Hardware abstraction layer
"""

__version__ = "1.0-beta"
__versionInfo__ = (1, 0, 0, "beta")
__changelog__ = {
    "1.0-beta": "Initial beta release with core functionality"
}