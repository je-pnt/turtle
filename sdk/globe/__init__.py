"""
sdk.globe - Geodetic and orbital calculations module

Public API:
    - Globe: Main geodetic and orbital calculations class
    - KeplerianOrbit: Keplerian orbital elements dataclass
    - EcefOrbit: ECEF orbital state dataclass
    - MapVisualization: Real-time 3D map visualization (optional, from visualization submodule)
"""

from .globe import Globe, KeplerianOrbit, EcefOrbit

# Import MapVisualization if visualization module is available (currently commented out)
try:
    from .visualization.visualization import MapVisualization
    __all__ = ['Globe', 'KeplerianOrbit', 'EcefOrbit', 'MapVisualization']
except (ImportError, AttributeError):
    # MapVisualization not available (implementation is commented out)
    __all__ = ['Globe', 'KeplerianOrbit', 'EcefOrbit']
