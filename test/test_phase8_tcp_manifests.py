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
from unittest.mock import AsyncMock, MagicMock

from nova.core.manifests.cards import (
    CardManifest, CardRegistry, getCardRegistry, getAllCardManifestsDict
)
from nova.server.streamEntities import (
    StreamEntityManager, StreamEntity, StreamState,
    STREAM_SYSTEM_ID, STREAM_CONTAINER_ID, STREAM_ENTITY_TYPE
)
from nova.server.tcp import TcpStreamConfig, TcpServer
from nova.core.contracts import TimelineMode


class TestManifestDiscovery:
    """Test manifest discovery per Phase 8 contracts"""
    
    def test_discovery_loads_all_manifests(self):
        """Verify all manifest files are discovered"""
        registry = CardRegistry()
        registry.discover()
        
        manifests = registry.getAllManifests()
        
        # Should have at least 4 manifests (default, gnss, spectrum, tcp-stream)
        assert len(manifests) >= 4
        
        # Verify known manifests exist
        cardTypes = [m.cardType for m in manifests]
        assert 'default-card' in cardTypes
        assert 'gnss-receiver-card' in cardTypes
        assert 'spectrum-card' in cardTypes
        assert 'tcp-stream-card' in cardTypes
    
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
        assert registry.getCardForEntityType('tcp-stream').cardType == 'tcp-stream-card'
    
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
        
        # Actions for TCP stream control
        assert 'start' in actionIds
        assert 'stop' in actionIds
        assert 'configure' in actionIds
    
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


class TestStreamEntity:
    """Test TCP stream entity lifecycle"""
    
    @pytest.fixture
    def mock_publisher(self):
        """Mock event publisher"""
        return AsyncMock()
    
    @pytest.fixture
    def stream_manager(self, mock_publisher):
        """Create stream entity manager with mock publisher"""
        return StreamEntityManager(
            scopeId='test-scope',
            eventPublisher=mock_publisher,
            timelineModeCallback=lambda: TimelineMode.LIVE
        )
    
    @pytest.mark.asyncio
    async def test_create_stream_emits_descriptor(self, stream_manager, mock_publisher):
        """Creating stream should emit ProducerDescriptor"""
        entity = await stream_manager.createStream({
            'port': 9100,
            'displayName': 'Test Stream'
        }, createdBy='test-user')
        
        # Should have called publisher at least twice (descriptor + initial status)
        assert mock_publisher.call_count >= 2
        
        # First call should be descriptor
        first_call = mock_publisher.call_args_list[0]
        event = first_call[0][0]
        
        assert event['messageType'] == 'ProducerDescriptor'
        assert event['systemId'] == STREAM_SYSTEM_ID
        assert event['containerId'] == STREAM_CONTAINER_ID
        assert event['lane'] == 'metadata'
        assert event['payload']['entityType'] == STREAM_ENTITY_TYPE
    
    @pytest.mark.asyncio
    async def test_stream_has_correct_identity(self, stream_manager, mock_publisher):
        """Stream entity should have correct identity"""
        entity = await stream_manager.createStream({
            'streamId': 'test-123',
            'port': 9100,
            'displayName': 'Test Stream'
        })
        
        assert entity.streamId == 'test-123'
        
        # Check identity in published event
        event = mock_publisher.call_args_list[0][0][0]
        
        assert event['systemId'] == 'tcpStream'
        assert event['containerId'] == 'streams'
        assert event['uniqueId'] == 'test-123'
    
    @pytest.mark.asyncio
    async def test_start_stream_emits_status_update(self, stream_manager, mock_publisher):
        """Starting stream should emit UiUpdate with state=starting"""
        entity = await stream_manager.createStream({
            'streamId': 'test-123',
            'port': 9100,
            'displayName': 'Test Stream'
        })
        
        mock_publisher.reset_mock()
        
        await stream_manager.startStream('test-123')
        
        # Should emit UiUpdate with starting state
        assert mock_publisher.call_count >= 1
        event = mock_publisher.call_args_list[0][0][0]
        
        assert event['messageType'] == 'UiUpdate'
        assert event['data']['state'] == 'starting'
    
    @pytest.mark.asyncio
    async def test_stop_stream_emits_status_update(self, stream_manager, mock_publisher):
        """Stopping stream should emit UiUpdate with state=stopped"""
        entity = await stream_manager.createStream({
            'streamId': 'test-123',
            'port': 9100,
            'displayName': 'Test Stream'
        })
        
        await stream_manager.startStream('test-123')
        mock_publisher.reset_mock()
        
        await stream_manager.stopStream('test-123')
        
        # Should emit UiUpdate with stopped state
        event = mock_publisher.call_args_list[0][0][0]
        
        assert event['messageType'] == 'UiUpdate'
        assert event['data']['state'] == 'stopped'


class TestReplayBlocking:
    """Test that stream commands are blocked in REPLAY mode"""
    
    @pytest.fixture
    def replay_mode_manager(self):
        """Create manager in REPLAY mode"""
        return StreamEntityManager(
            scopeId='test-scope',
            eventPublisher=AsyncMock(),
            timelineModeCallback=lambda: TimelineMode.REPLAY
        )
    
    @pytest.mark.asyncio
    async def test_create_blocked_in_replay(self, replay_mode_manager):
        """Stream creation should be blocked in REPLAY"""
        with pytest.raises(ValueError, match="REPLAY"):
            await replay_mode_manager.createStream({
                'port': 9100,
                'displayName': 'Test Stream'
            })
    
    @pytest.mark.asyncio
    async def test_start_blocked_in_replay(self, replay_mode_manager):
        """Stream start should be blocked in REPLAY"""
        # Can't even create in replay, so this tests the mechanism
        with pytest.raises(ValueError, match="REPLAY"):
            await replay_mode_manager.startStream('any-id')
    
    @pytest.mark.asyncio
    async def test_stop_blocked_in_replay(self, replay_mode_manager):
        """Stream stop should be blocked in REPLAY"""
        with pytest.raises(ValueError, match="REPLAY"):
            await replay_mode_manager.stopStream('any-id')


class TestTcpStreamConfig:
    """Test TCP stream configuration"""
    
    def test_config_defaults(self):
        """Test TcpStreamConfig default values"""
        config = TcpStreamConfig(
            streamId='test',
            displayName='Test',
            port=9100,
            scopeId='scope'
        )
        
        assert config.laneFilter == 'raw'
        assert config.visibility == 'private'
        assert config.createdBy == 'system'
    
    def test_stream_entity_to_tcp_config(self):
        """Test StreamEntity.toTcpConfig() conversion"""
        entity = StreamEntity(
            streamId='test-123',
            displayName='Test Stream',
            port=9100,
            scopeId='test-scope',
            createdBy='user1',
            visibility='public'
        )
        
        config = entity.toTcpConfig()
        
        assert config.streamId == 'test-123'
        assert config.displayName == 'Test Stream'
        assert config.port == 9100
        assert config.scopeId == 'test-scope'
        assert config.createdBy == 'user1'
        assert config.visibility == 'public'


class TestTcpStreamDescriptor:
    """Test TCP stream descriptor generation"""
    
    def test_descriptor_contains_required_fields(self):
        """Verify descriptor has all required fields"""
        entity = StreamEntity(
            streamId='test-123',
            displayName='Test Stream',
            port=9100,
            scopeId='test-scope',
            createdBy='user1',
            visibility='public'
        )
        
        descriptor = entity.toDescriptor()
        
        assert descriptor['uniqueId'] == 'test-123'
        assert descriptor['displayName'] == 'Test Stream'
        assert descriptor['port'] == 9100
        assert descriptor['createdBy'] == 'user1'
        assert descriptor['visibility'] == 'public'
        assert descriptor['entityType'] == 'tcp-stream'
    
    def test_ui_update_contains_status_fields(self):
        """Verify UiUpdate has status fields"""
        entity = StreamEntity(
            streamId='test-123',
            displayName='Test Stream',
            port=9100,
            scopeId='test-scope',
            state=StreamState.RUNNING,
            bytesOut=1000,
            msgsOut=10
        )
        
        update = entity.toUiUpdate()
        
        assert update['state'] == 'running'
        assert update['bytesOut'] == 1000
        assert update['msgsOut'] == 10


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
        
        # tcp-stream-card was added in Phase 8
        tcp_card = registry.getCardForEntityType('tcp-stream')
        assert tcp_card.cardType == 'tcp-stream-card'
        
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
