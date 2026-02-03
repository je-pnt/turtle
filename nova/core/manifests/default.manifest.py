"""
Default Card Manifest

Fallback card for unknown entity types.
EntityTypes: [] (matches nothing directly; used when no other manifest matches)
"""

from nova.core.manifests.cards import CardManifest, Widget, WidgetType, Action


MANIFEST = CardManifest(
    cardType="default-card",
    title="Entity",
    icon="📦",
    color="#607D8B",
    onlineIndicator=True,
    entityTypes=[],  # Fallback - matches nothing directly
    widgets=[],
    actions=[]
)
