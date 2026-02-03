"""
NOVA Core Package

Core process owns all database writes/reads, ingest normalization,
deterministic ordering, and playback/query/export functionality.

Architecture Invariants:
- Single truth DB per instance
- Append-only semantics
- Deterministic ordering with explicit tie-breaks
- EventId-based global dedupe
"""

__version__ = "2.0.0-phase1"
