"""
Subject Patterns: Centralized NATS subject naming conventions.

Defines consistent subject patterns for streams, events, sessions, and tasks.

Property of Uncompromising Sensors LLC.
"""


class SubjectPatterns:
    """NATS subject patterns for all message types"""
    
    # ===== Live Streams (High Volume) =====
    LIVE_STREAM = 'stream.{scopeId}.{entityId}.{messageType}'
    LIVE_STREAM_WILDCARD = 'stream.*.*.*'
    LIVE_STREAM_SCOPE = 'stream.{scopeId}.>'
    LIVE_STREAM_ENTITY = 'stream.{scopeId}.{entityId}.*'
    LIVE_STREAM_TYPE = 'stream.*.*.{messageType}'
    
    # ===== Live Events (Low Volume) =====
    LIVE_EVENT = 'event.{scopeId}.{eventType}'
    LIVE_EVENT_WILDCARD = 'event.*.*'
    LIVE_EVENT_SCOPE = 'event.{scopeId}.*'
    LIVE_EVENT_TYPE = 'event.*.{eventType}'
    
    # ===== Session Replay Streams =====
    SESSION_STREAM = 'session.{sessionId}.stream.{entityId}.{messageType}'
    SESSION_STREAM_WILDCARD = 'session.{sessionId}.stream.*.*'
    SESSION_STREAM_ENTITY = 'session.{sessionId}.stream.{entityId}.*'
    
    # ===== Session Replay Events =====
    SESSION_EVENT = 'session.{sessionId}.event.{scopeId}.{eventType}'
    SESSION_EVENT_WILDCARD = 'session.{sessionId}.event.*.*'
    SESSION_EVENT_SCOPE = 'session.{sessionId}.event.{scopeId}.*'
    
    # ===== Task Control =====
    TASK = 'task.{scopeId}.{command}'
    TASK_WILDCARD = 'task.*.*'
    TASK_SCOPE = 'task.{scopeId}.*'
    
    # ===== Session Control =====
    SESSION_CONTROL = 'session.{sessionId}.control'
    
    @staticmethod
    def liveStream(scopeId: str, entityId: str, messageType: str) -> str:
        """Generate live stream subject
        
        Args:
            scopeId: Scope identifier (e.g., 'payload-1')
            entityId: Entity identifier (e.g., 'ubx-receiver-001')
            messageType: Message type (e.g., 'llas-row')
            
        Returns:
            'stream.payload-1.ubx-receiver-001.llas-row'
        """
        return f'stream.{scopeId}.{entityId}.{messageType}'
    
    @staticmethod
    def liveEvent(scopeId: str, eventType: str) -> str:
        """Generate live event subject
        
        Args:
            scopeId: Scope identifier (e.g., 'payload-1')
            eventType: Event type (e.g., 'entity.created')
            
        Returns:
            'event.payload-1.entity.created'
        """
        return f'event.{scopeId}.{eventType}'
    
    @staticmethod
    def sessionStream(sessionId: str, entityId: str, messageType: str) -> str:
        """Generate session replay stream subject
        
        Args:
            sessionId: Session identifier (e.g., 'abc123')
            entityId: Entity identifier
            messageType: Message type
            
        Returns:
            'session.abc123.stream.ubx-receiver-001.llas-row'
        """
        return f'session.{sessionId}.stream.{entityId}.{messageType}'
    
    @staticmethod
    def sessionEvent(sessionId: str, scopeId: str, eventType: str) -> str:
        """Generate session replay event subject
        
        Args:
            sessionId: Session identifier
            scopeId: Scope identifier
            eventType: Event type
            
        Returns:
            'session.abc123.event.payload-1.entity.created'
        """
        return f'session.{sessionId}.event.{scopeId}.{eventType}'
    
    @staticmethod
    def task(scopeId: str, command: str) -> str:
        """Generate task control subject
        
        Args:
            scopeId: Scope identifier (e.g., 'payload-1')
            command: Command name (e.g., 'start-recording')
            
        Returns:
            'task.payload-1.start-recording'
        """
        return f'task.{scopeId}.{command}'
    
    @staticmethod
    def sessionControl(sessionId: str) -> str:
        """Generate session control subject
        
        Args:
            sessionId: Session identifier
            
        Returns:
            'session.abc123.control'
        """
        return f'session.{sessionId}.control'
    
    @staticmethod
    def subscribeAllStreams() -> str:
        """Subscribe to all live streams
        
        Returns:
            'stream.*.*'
        """
        return 'stream.*.*'
    
    @staticmethod
    def subscribeAllEvents() -> str:
        """Subscribe to all live events
        
        Returns:
            'event.*.*'
        """
        return 'event.*.*'
    
    @staticmethod
    def subscribeEntityStreams(entityId: str) -> str:
        """Subscribe to all streams from one entity
        
        Args:
            entityId: Entity identifier
            
        Returns:
            'stream.ubx-receiver-001.*'
        """
        return f'stream.{entityId}.*'
    
    @staticmethod
    def subscribeScopeEvents(scopeId: str) -> str:
        """Subscribe to all events in one scope
        
        Args:
            scopeId: Scope identifier
            
        Returns:
            'event.payload-1.*'
        """
        return f'event.{scopeId}.*'
    
    @staticmethod
    def subscribeSessionAll(sessionId: str) -> str:
        """Subscribe to all replay messages in a session
        
        Args:
            sessionId: Session identifier
            
        Returns:
            'session.abc123.>'
        """
        return f'session.{sessionId}.>'
