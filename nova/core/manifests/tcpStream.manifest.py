"""
Stream Card Manifest

Individual output stream entity (TCP, WebSocket, UDP protocols).
EntityType: stream
Identity: systemId=stream, containerId=streams, uniqueId=<streamId>

Custom rendered in cards.js (renderTcpStreamCard).
"""

from nova.core.manifests.cards import CardManifest, Widget, WidgetType, Action


MANIFEST = CardManifest(
    cardType="stream-card",
    title="Stream",
    icon="/ui/icons/stream.svg",
    color="#4CAF50",
    onlineIndicator=True,
    entityTypes=["stream", "tcp-stream"],  # stream is new, tcp-stream for backwards compat
    widgets=[],  # Custom rendered
    actions=[]   # Custom rendered
)
