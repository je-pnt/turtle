"""
Hardware Service Run Manifest

Run type for hardware service replays with:
- Music on/off timestamps (separate lists)
- Signal selection by constellation
- Driver bundle export

NOTE: Run manifests are PRESENTATION artifacts (per-user, NOT truth).
      This is distinct from card manifests which define truth entity display.
"""

from nova.core.manifests.runs.registry import RunManifest, RunField, FieldType


# Signal options embedded in manifest - no separate API needed
# These are the GNSS signals available for selection, grouped by constellation
AVAILABLE_SIGNALS = {
    'GPS': ['L1CA', 'L2C', 'L5', 'L1P', 'L2P', 'L1C'],
    'GLONASS': ['L1CA', 'L2CA', 'L1P', 'L2P', 'L3'],
    'Galileo': ['E1', 'E5a', 'E5b', 'E6', 'E5AltBOC'],
    'BeiDou': ['B1I', 'B1C', 'B2I', 'B2a', 'B2b', 'B3I'],
    'QZSS': ['L1CA', 'L2C', 'L5', 'L6', 'L1C', 'L1S'],
    'SBAS': ['L1CA', 'L5'],
    'NAVIC': ['L5']
}


RUN_MANIFEST = RunManifest(
    runType="hardwareService",
    title="Hardware Service",
    icon="📡",
    color="#2196F3",
    description="Hardware service replay with signal selection and music times.",
    
    fields=[
        # Music On times - simple array of datetime values
        RunField(
            fieldId="musicOnTimes",
            label="Music On",
            fieldType=FieldType.ARRAY,
            required=False,
            default=[],
            config={
                "itemType": "datetime",  # Array of simple datetime values
                "addLabel": "Add Music On",
                "inline": True,  # Render inline (button + times) not collapsible
            }
        ),
        
        # Music Off times - simple array of datetime values
        RunField(
            fieldId="musicOffTimes",
            label="Music Off",
            fieldType=FieldType.ARRAY,
            required=False,
            default=[],
            config={
                "itemType": "datetime",
                "addLabel": "Add Music Off",
                "inline": True,
            }
        ),
        
        # Signal selection - special widget with options from manifest
        RunField(
            fieldId="signals",
            label="Signals",
            fieldType=FieldType.SIGNALS,
            required=False,
            default={},
            config={
                "collapsible": True,
                "defaultCollapsed": True,
                # Signal options embedded in manifest - client reads from here
                "availableSignals": AVAILABLE_SIGNALS,
            }
        ),
    ],
    
    sections=[
        {"id": "times", "label": "Time Window", "collapsed": False},
        {"id": "music", "label": "Music Times", "collapsed": False},
        {"id": "signals", "label": "Signal Selection", "collapsed": True},
        {"id": "notes", "label": "Notes", "collapsed": False},
    ],
    
    exportEnabled=True,
    exportHandler="nova.core.export.hardwareServiceExport"
)
