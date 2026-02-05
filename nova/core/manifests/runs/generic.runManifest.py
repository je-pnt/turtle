"""
Generic Run Manifest

Base run type with minimal required fields.
All runs have: name, startTimeSec, stopTimeSec, analystNotes (implicit core fields).
Generic adds no extra fields - it's the simplest run type.
"""

from nova.core.manifests.runs.registry import RunManifest, RunField, FieldType


RUN_MANIFEST = RunManifest(
    runType="generic",
    title="Generic Run",
    icon="🎬",
    color="#9c27b0",
    description="Basic replay with time window and notes.",
    
    # Core fields (name, start/stop times, notes) are always present.
    # Generic run adds no additional fields.
    fields=[],
    
    sections=[
        {"id": "times", "label": "Time Window", "collapsed": False},
        {"id": "notes", "label": "Notes", "collapsed": False},
    ],
    
    exportEnabled=True,
    exportHandler=None  # Use default export (time-window of truth data)
)
