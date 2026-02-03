"""
Setup Streams Card Manifest

Entry point for output stream management (TCP, WebSocket, UDP).
EntityType: setup-streams
Identity: systemId=stream, containerId=system, uniqueId=setupStreams

Custom rendered in cards.js (renderSetupStreamsCard).
"""

from nova.core.manifests.cards import CardManifest, Widget, WidgetType, Action


MANIFEST = CardManifest(
    cardType="setup-streams-card",
    title="Setup Streams",
    icon="/ui/icons/stream.svg",
    color="#2196F3",
    onlineIndicator=False,
    entityTypes=["setup-streams"],
    widgets=[],  # Custom rendered
    actions=[]   # Custom rendered
)
