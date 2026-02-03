"""
GNSS Receiver Card Manifest

Defines the card layout for GNSS receiver entities.
EntityTypes: gnss-receiver, ubx, mosaic-x5, septentrio
"""

from nova.core.manifests.cards import CardManifest, Widget, WidgetType, Action


MANIFEST = CardManifest(
    cardType="gnss-receiver-card",
    title="GNSS Receiver",
    icon="📡",
    color="#2196F3",
    onlineIndicator=True,
    entityTypes=["gnss-receiver", "ubx", "mosaic-x5", "septentrio"],
    widgets=[
        # POSITION TABLE - 2x2 grid: row1=Time/Alt, row2=Lat/Lon
        Widget(WidgetType.TABLE, "positionTable", "", {"section": "position", "rows": [
            {"label": "Time", "binding": "gnssTime", "type": "timestamp"},
            {"label": "Alt", "binding": "alt", "type": "number", "precision": 1, "unit": "m"},
            {"label": "Lat", "binding": "lat", "type": "coord", "precision": 8},
            {"label": "Lon", "binding": "lon", "type": "coord", "precision": 8},
        ]}),
        
        # PRIMARY section - fix info
        Widget(WidgetType.STATUS, "fixType", "Fix", {"section": "primary", "mapping": {
            0: "No Fix", 1: "DR", 2: "2D", 3: "3D", 4: "GNSS+DR", 5: "Time"
        }}),
        Widget(WidgetType.NUMBER, "numSv", "SVs", {"precision": 0, "section": "primary"}),
        Widget(WidgetType.NUMBER, "cn04th", "C/N₀₄", {"precision": 1, "unit": "dB", "section": "primary"}),
        
        # SECONDARY section - lesser data below
        Widget(WidgetType.NUMBER, "avgCn0", "Avg C/N₀", {"precision": 1, "unit": "dB", "section": "secondary"}),
        Widget(WidgetType.NUMBER, "hAcc", "H.Acc", {"precision": 2, "unit": "m", "section": "secondary"}),
        Widget(WidgetType.NUMBER, "vAcc", "V.Acc", {"precision": 2, "unit": "m", "section": "secondary"}),
        Widget(WidgetType.NUMBER, "pDOP", "PDOP", {"precision": 2, "section": "secondary"}),
        
        # Collapsible satellite tables
        Widget(WidgetType.SV_TABLE, "svInfo", "Satellites", {"collapsible": True, "section": "tables"}),
        Widget(WidgetType.SV_TABLE, "sigInfo", "Signals", {"collapsible": True, "section": "tables"}),
    ],
    actions=[
        Action("configure", "Configure", "uploadConfig", icon="⚙️", confirm=False),
        Action("hotStart", "Hot", "hotStart", icon="🔄", confirm=False),
        Action("warmStart", "Warm", "warmStart", icon="🔃", confirm=False),
        Action("coldStart", "Cold", "coldReset", icon="⚡", confirm=False),
    ]
)
