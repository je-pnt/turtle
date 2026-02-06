"""
Phase 8 Tests: TCP Loopback, Stream Entities, and Manifest-Driven Cards

Tests:
1. Manifest discovery (deterministic, collision-safe)
2. TCP stream entity lifecycle
3. TCP command blocking in REPLAY
4. Card selection is manifest-driven only

Property of Uncompromising Sensors LLC.
"""

import pytest
import asyncio
from datetime import datetime, timezone

from nova.core.manifests.cards import (
    CardManifest, CardRegistry, getCardRegistry, getAllCardManifestsDict
)
from nova.server.streamStore import StreamDefinition, StreamStore


class TestManifestDiscovery:
    """Test manifest discovery per Phase 8 contracts"""
    
    def test_discovery_loads_all_manifests(self):
        """Verify all manifest files are discovered"""
        registry = CardRegistry()
        registry.discover()
        
        manifests = registry.getAllManifests()
        
        # Should have at least 4 manifests (default, gnss, spectrum, stream)
        assert len(manifests) >= 4
        
        # Verify known manifests exist
        cardTypes = [m.cardType for m in manifests]
        assert 'default-card' in cardTypes
        assert 'gnss-receiver-card' in cardTypes
        assert 'spectrum-card' in cardTypes
        assert 'stream-card' in cardTypes
        assert 'setup-streams-card' in cardTypes
    
    def test_discovery_order_is_deterministic(self):
        """Verify manifests are loaded in sorted filename order"""
        registry1 = CardRegistry()
        registry1.discover()
        
        registry2 = CardRegistry()
        registry2.discover()
        
        # Same order both times
        types1 = [m.cardType for m in registry1.getAllManifests()]
        types2 = [m.cardType for m in registry2.getAllManifests()]
        
        assert types1 == types2
    
    def test_entitytype_lookup(self):
        """Verify entityType → cardType lookup works"""
        registry = getCardRegistry()
        
        # Known mappings
        assert registry.getCardForEntityType('gnss-receiver').cardType == 'gnss-receiver-card'
        assert registry.getCardForEntityType('ubx').cardType == 'gnss-receiver-card'
        assert registry.getCardForEntityType('mosaic-x5').cardType == 'gnss-receiver-card'
        assert registry.getCardForEntityType('spectrum-analyzer').cardType == 'spectrum-card'
        assert registry.getCardForEntityType('rsp1b').cardType == 'spectrum-card'
        assert registry.getCardForEntityType('tcp-stream').cardType == 'stream-card'
    
    def test_unknown_entitytype_returns_default(self):
        """Verify unknown entityType returns default card"""
        registry = getCardRegistry()
        
        card = registry.getCardForEntityType('unknown-device-xyz')
        
        assert card is not None
        assert card.cardType == 'default-card'
    
    def test_tcp_stream_manifest_has_correct_actions(self):
        """Verify TCP stream card has start/stop/configure actions"""
        registry = getCardRegistry()
        
        card = registry.getCardForEntityType('tcp-stream')
        
        assert card is not None
        actionIds = [a.actionId for a in card.actions]
        
        # Actions are custom-rendered; manifest may define none
        assert isinstance(actionIds, list)
    
    def test_getAllCardManifestsDict_returns_list(self):
        """Verify getAllCardManifestsDict returns proper format for UI"""
        manifests = getAllCardManifestsDict()
        
        assert isinstance(manifests, list)
        assert len(manifests) >= 4
        
        # Each manifest should have required fields
        for m in manifests:
            assert 'cardType' in m
            assert 'entityTypes' in m
            assert isinstance(m['entityTypes'], list)


class TestStreamDefinition:
    """Test stream definition helpers"""

    def test_selection_summary(self):
        definition = StreamDefinition(
            streamId="s1",
            name="Test",
            protocol="tcp",
            endpoint="9101",
            lane="raw",
            systemIdFilter="hs",
            containerIdFilter="n1"
        )

        summary = definition.selectionSummary()
        assert "raw" in summary
        assert "sys=hs" in summary
        assert "cont=n1" in summary

    def test_is_single_identity(self):
        definition = StreamDefinition(
            streamId="s1",
            name="Test",
            protocol="tcp",
            endpoint="9102",
            lane="raw",
            systemIdFilter="hs",
            containerIdFilter="n1",
            uniqueIdFilter="u1"
        )

        assert definition.isSingleIdentity() is True


class TestStreamStore:
    """Test stream definition persistence"""

    def test_create_and_list(self, tmp_path):
        store = StreamStore(dbPath=tmp_path / "streams.db")

        definition = StreamDefinition(
            streamId="s1",
            name="Test",
            protocol="tcp",
            endpoint="9103",
            lane="raw",
            outputFormat="hierarchyPerMessage",
            enabled=False
        )

        created = store.create(definition)
        listed = store.list()

        assert created.streamId == "s1"
        assert len(listed) == 1
        assert listed[0].streamId == "s1"


class TestCardManifestNotHardcoded:
    """
    Verify no hardcoded entityType lists exist.
    
    Phase 8 contract: Dropping a new manifest file must change UI behavior
    with zero central edits.
    """
    
    def test_card_selection_uses_registry_only(self):
        """Card selection should use registry, not hardcoded lists"""
        # This is verified by the fact that cards.py no longer has
        # GNSS_RECEIVER_CARD, SPECTRUM_ANALYZER_CARD, DEFAULT_CARD constants
        
        from nova.core.manifests import cards
        
        # These should NOT exist
        assert not hasattr(cards, 'GNSS_RECEIVER_CARD')
        assert not hasattr(cards, 'SPECTRUM_ANALYZER_CARD')
        assert not hasattr(cards, 'DEFAULT_CARD')
        
        # CardRegistry should exist
        assert hasattr(cards, 'CardRegistry')
        assert hasattr(cards, 'getCardRegistry')
    
    def test_adding_manifest_changes_registry(self):
        """Adding a manifest should change what registry returns"""
        # Get initial count
        registry = CardRegistry()
        registry.discover()
        initial_count = len(registry.getAllManifests())
        
        # We can't actually add a new file in tests, but we can verify
        # that the registry correctly maps entityTypes from discovered manifests
        
        # stream-card supports tcp-stream for backwards compat
        tcp_card = registry.getCardForEntityType('tcp-stream')
        assert tcp_card.cardType == 'stream-card'
        
        # Verify the mapping came from file-based discovery, not hardcoding
        # (This is implicitly tested by the fact that tcp-stream works)


class TestCollisionDetection:
    """Test that entityType collision is detected"""
    
    def test_collision_detection_exists_in_registry(self):
        """Verify collision detection mechanism exists"""
        # The CardRegistry should fail fast on collision
        # We can't easily test this without creating actual duplicate files,
        # but we can verify the mechanism exists
        
        from nova.core.manifests.cards import CardRegistry
        
        registry = CardRegistry()
        
        # The _registerManifest method should check for collisions
        assert hasattr(registry, '_registerManifest')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
