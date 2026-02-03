"""
Spectrum Analyzer Card Manifest

Defines the card layout for spectrum analyzer entities.
EntityTypes: spectrum-analyzer, rsp1b
"""

from nova.core.manifests.cards import CardManifest, Widget, WidgetType, Action


MANIFEST = CardManifest(
    cardType="spectrum-card",
    title="Spectrum Analyzer",
    icon="📊",
    color="#9C27B0",
    onlineIndicator=True,
    entityTypes=["spectrum-analyzer", "rsp1b"],
    widgets=[
        Widget(WidgetType.NUMBER, "centerFreq", "Center Freq", {"precision": 3, "unit": "MHz"}),
        Widget(WidgetType.NUMBER, "span", "Span", {"precision": 3, "unit": "MHz"}),
        Widget(WidgetType.NUMBER, "rbw", "RBW", {"precision": 1, "unit": "kHz"}),
        Widget(WidgetType.NUMBER, "peakPower", "Peak Power", {"precision": 1, "unit": "dBm"}),
    ],
    actions=[
        Action("configure", "Configure", "configure", icon="⚙️", confirm=False),
    ]
)
