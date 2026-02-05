"""
NOVA Presentation Store

Per-user and admin-default presentation overrides.
Presentation is NOT truth - it is view-only appearance customization.

Phase 10 (phase9-11Updated.md):
- Presentation affects rendering/export labeling only
- Never modifies telemetry truth
- Per-user overrides > admin defaults > factory defaults
- Allowed keys: displayName, modelRef, color, scale

Storage Layout:
- User overrides: data/users/<username>/presentation.json
- Admin defaults: data/presentation/defaults/<scopeId>.json
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict

from sdk.logging import getLogger


# Phase 10 allowed keys (no expansion)
ALLOWED_KEYS = {'displayName', 'modelRef', 'color', 'scale'}

# Factory defaults
FACTORY_DEFAULTS = {
    'displayName': None,  # Falls back to uniqueId
    'modelRef': None,     # No model by default
    'color': [0, 212, 255],  # Neon blue accent (NOVA theme)
    'scale': 1.0
}


@dataclass
class EntityPresentation:
    """Presentation overrides for a single entity."""
    displayName: Optional[str] = None
    modelRef: Optional[str] = None
    color: Optional[List[int]] = None  # RGB triple [r, g, b]
    scale: Optional[float] = None
    
    def toDict(self) -> Dict[str, Any]:
        """Return only non-None values."""
        return {k: v for k, v in asdict(self).items() if v is not None}
    
    @classmethod
    def fromDict(cls, data: Dict[str, Any]) -> 'EntityPresentation':
        """Create from dict, filtering to allowed keys only."""
        filtered = {k: v for k, v in data.items() if k in ALLOWED_KEYS}
        return cls(**filtered)


class PresentationStore:
    """
    Manages presentation overrides storage.
    
    Not truth - stateless view-layer customization.
    """
    
    def __init__(self, dataPath: str = './nova/data'):
        self.log = getLogger()
        self.dataPath = Path(dataPath)
        self.usersPath = self.dataPath / 'users'
        self.defaultsPath = self.dataPath / 'presentation' / 'defaults'
        
        # Ensure directories exist
        self.usersPath.mkdir(parents=True, exist_ok=True)
        self.defaultsPath.mkdir(parents=True, exist_ok=True)
    
    def _scopeToFilename(self, scopeId: str) -> str:
        """Convert scopeId to safe filename (| is invalid on Windows)."""
        return scopeId.replace('|', '_')
    
    def _filenameToScope(self, filename: str) -> str:
        """Convert filename back to scopeId."""
        return filename.replace('_', '|')
    
    # =========================================================================
    # User Overrides
    # =========================================================================
    
    def getUserOverrides(self, username: str, scopeId: str) -> Dict[str, EntityPresentation]:
        """
        Get user's presentation overrides for a scope.
        
        Returns: Dict of uniqueId → EntityPresentation
        """
        filePath = self.usersPath / username / 'presentation.json'
        if not filePath.exists():
            return {}
        
        try:
            with open(filePath, 'r') as f:
                data = json.load(f)
            
            # Get scope-specific overrides
            scopeData = data.get(scopeId, {})
            return {
                uniqueId: EntityPresentation.fromDict(overrides)
                for uniqueId, overrides in scopeData.items()
            }
        except (json.JSONDecodeError, IOError) as e:
            self.log.warning(f"[Presentation] Failed to load user overrides: {e}")
            return {}
    
    def getAllUserOverrides(self, username: str) -> Dict[str, Dict[str, EntityPresentation]]:
        """
        Get ALL user's presentation overrides across all scopes.
        
        Used when user has 'ALL' access to retrieve overrides from all scopes.
        
        Returns: Dict of scopeId → { uniqueId → EntityPresentation }
        """
        filePath = self.usersPath / username / 'presentation.json'
        if not filePath.exists():
            return {}
        
        try:
            with open(filePath, 'r') as f:
                data = json.load(f)
            
            result = {}
            for scopeId, scopeData in data.items():
                if isinstance(scopeData, dict):
                    result[scopeId] = {
                        uniqueId: EntityPresentation.fromDict(overrides)
                        for uniqueId, overrides in scopeData.items()
                        if isinstance(overrides, dict)
                    }
            return result
        except (json.JSONDecodeError, IOError) as e:
            self.log.warning(f"[Presentation] Failed to load all user overrides: {e}")
            return {}
    
    def setUserOverride(
        self, 
        username: str, 
        scopeId: str, 
        uniqueId: str, 
        overrides: Dict[str, Any]
    ) -> bool:
        """
        Set user's presentation override for an entity.
        
        Last write wins. Validates keys against ALLOWED_KEYS.
        Returns True on success.
        """
        # Filter to allowed keys
        filtered = {k: v for k, v in overrides.items() if k in ALLOWED_KEYS}
        if not filtered:
            return False
        
        # Validate color format
        if 'color' in filtered:
            color = filtered['color']
            if not (isinstance(color, list) and len(color) == 3 and 
                    all(isinstance(c, int) and 0 <= c <= 255 for c in color)):
                self.log.warning(f"[Presentation] Invalid color format: {color}")
                del filtered['color']
        
        # Validate scale
        if 'scale' in filtered:
            scale = filtered['scale']
            if not (isinstance(scale, (int, float)) and 0.1 <= scale <= 10.0):
                self.log.warning(f"[Presentation] Invalid scale: {scale}")
                del filtered['scale']
        
        # Load existing
        userDir = self.usersPath / username
        userDir.mkdir(parents=True, exist_ok=True)
        filePath = userDir / 'presentation.json'
        
        data = {}
        if filePath.exists():
            try:
                with open(filePath, 'r') as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError):
                data = {}
        
        # Update
        if scopeId not in data:
            data[scopeId] = {}
        
        if uniqueId not in data[scopeId]:
            data[scopeId][uniqueId] = {}
        
        data[scopeId][uniqueId].update(filtered)
        
        # Remove None values (user clearing an override)
        data[scopeId][uniqueId] = {
            k: v for k, v in data[scopeId][uniqueId].items() if v is not None
        }
        
        # Write
        try:
            with open(filePath, 'w') as f:
                json.dump(data, f, indent=2)
            return True
        except IOError as e:
            self.log.error(f"[Presentation] Failed to save user override: {e}")
            return False
    
    def deleteUserOverride(
        self, 
        username: str, 
        scopeId: str, 
        uniqueId: str,
        key: Optional[str] = None
    ) -> bool:
        """
        Delete user's presentation override.
        
        If key is None, deletes all overrides for the entity.
        If key is provided, deletes only that key.
        """
        filePath = self.usersPath / username / 'presentation.json'
        if not filePath.exists():
            return True
        
        try:
            with open(filePath, 'r') as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            return True
        
        if scopeId not in data:
            return True
        
        if uniqueId not in data[scopeId]:
            return True
        
        if key:
            data[scopeId][uniqueId].pop(key, None)
        else:
            del data[scopeId][uniqueId]
        
        try:
            with open(filePath, 'w') as f:
                json.dump(data, f, indent=2)
            return True
        except IOError as e:
            self.log.error(f"[Presentation] Failed to delete user override: {e}")
            return False
    
    # =========================================================================
    # Admin Defaults
    # =========================================================================
    
    def getAdminDefaults(self, scopeId: str) -> Dict[str, EntityPresentation]:
        """
        Get admin default overrides for a scope.
        
        Returns: Dict of uniqueId → EntityPresentation
        """
        filePath = self.defaultsPath / f'{self._scopeToFilename(scopeId)}.json'
        if not filePath.exists():
            return {}
        
        try:
            with open(filePath, 'r') as f:
                data = json.load(f)
            
            return {
                uniqueId: EntityPresentation.fromDict(overrides)
                for uniqueId, overrides in data.items()
            }
        except (json.JSONDecodeError, IOError) as e:
            self.log.warning(f"[Presentation] Failed to load admin defaults: {e}")
            return {}
    
    def getAllAdminDefaults(self) -> Dict[str, Dict[str, EntityPresentation]]:
        """
        Get ALL admin default overrides across all scopes.
        
        Used when user has 'ALL' access to retrieve defaults from all scopes.
        
        Returns: Dict of scopeId → { uniqueId → EntityPresentation }
        """
        result = {}
        
        try:
            # Scan all JSON files in defaults directory
            for filePath in self.defaultsPath.glob('*.json'):
                scopeId = self._filenameToScope(filePath.stem)  # Convert filename back to scopeId
                try:
                    with open(filePath, 'r') as f:
                        data = json.load(f)
                    result[scopeId] = {
                        uniqueId: EntityPresentation.fromDict(overrides)
                        for uniqueId, overrides in data.items()
                        if isinstance(overrides, dict)
                    }
                except (json.JSONDecodeError, IOError) as e:
                    self.log.warning(f"[Presentation] Failed to load admin defaults for {scopeId}: {e}")
        except Exception as e:
            self.log.warning(f"[Presentation] Failed to scan admin defaults: {e}")
        
        return result

    def setAdminDefault(
        self, 
        scopeId: str, 
        uniqueId: str, 
        overrides: Dict[str, Any]
    ) -> bool:
        """
        Set admin default override for an entity (scope-wide).
        
        Admin-only operation. Last write wins.
        """
        # Filter to allowed keys
        filtered = {k: v for k, v in overrides.items() if k in ALLOWED_KEYS}
        if not filtered:
            return False
        
        # Validate color format
        if 'color' in filtered:
            color = filtered['color']
            if not (isinstance(color, list) and len(color) == 3 and 
                    all(isinstance(c, int) and 0 <= c <= 255 for c in color)):
                del filtered['color']
        
        # Validate scale
        if 'scale' in filtered:
            scale = filtered['scale']
            if not (isinstance(scale, (int, float)) and 0.1 <= scale <= 10.0):
                del filtered['scale']
        
        # Load existing
        filePath = self.defaultsPath / f'{self._scopeToFilename(scopeId)}.json'
        
        data = {}
        if filePath.exists():
            try:
                with open(filePath, 'r') as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError):
                data = {}
        
        # Update
        if uniqueId not in data:
            data[uniqueId] = {}
        
        data[uniqueId].update(filtered)
        
        # Remove None values
        data[uniqueId] = {k: v for k, v in data[uniqueId].items() if v is not None}
        
        # Write
        try:
            with open(filePath, 'w') as f:
                json.dump(data, f, indent=2)
            return True
        except IOError as e:
            self.log.error(f"[Presentation] Failed to save admin default: {e}")
            return False
    
    def deleteAdminDefault(
        self, 
        scopeId: str, 
        uniqueId: str,
        key: Optional[str] = None
    ) -> bool:
        """Delete admin default override."""
        filePath = self.defaultsPath / f'{self._scopeToFilename(scopeId)}.json'
        if not filePath.exists():
            return True
        
        try:
            with open(filePath, 'r') as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            return True
        
        if uniqueId not in data:
            return True
        
        if key:
            data[uniqueId].pop(key, None)
        else:
            del data[uniqueId]
        
        try:
            with open(filePath, 'w') as f:
                json.dump(data, f, indent=2)
            return True
        except IOError as e:
            self.log.error(f"[Presentation] Failed to delete admin default: {e}")
            return False
    
    # =========================================================================
    # Resolution (Inheritance)
    # =========================================================================
    
    def resolvePresentation(
        self, 
        username: Optional[str], 
        scopeId: str, 
        uniqueId: str
    ) -> Dict[str, Any]:
        """
        Resolve effective presentation for an entity.
        
        Inheritance order (per Phase 10):
        1. User override (if username provided)
        2. Admin default
        3. Factory default
        
        Returns complete presentation dict with all keys.
        """
        result = dict(FACTORY_DEFAULTS)
        
        # Layer 2: Admin defaults
        adminDefaults = self.getAdminDefaults(scopeId)
        if uniqueId in adminDefaults:
            for key, value in adminDefaults[uniqueId].toDict().items():
                result[key] = value
        
        # Layer 1: User overrides (highest priority)
        if username:
            userOverrides = self.getUserOverrides(username, scopeId)
            if uniqueId in userOverrides:
                for key, value in userOverrides[uniqueId].toDict().items():
                    result[key] = value
        
        return result
    
    def resolvePresentationBulk(
        self, 
        username: Optional[str], 
        scopeId: str, 
        uniqueIds: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Resolve presentation for multiple entities efficiently.
        
        Returns: Dict of uniqueId → presentation dict
        """
        # Load all at once
        adminDefaults = self.getAdminDefaults(scopeId)
        userOverrides = self.getUserOverrides(username, scopeId) if username else {}
        
        result = {}
        for uniqueId in uniqueIds:
            pres = dict(FACTORY_DEFAULTS)
            
            # Admin defaults
            if uniqueId in adminDefaults:
                for key, value in adminDefaults[uniqueId].toDict().items():
                    pres[key] = value
            
            # User overrides
            if uniqueId in userOverrides:
                for key, value in userOverrides[uniqueId].toDict().items():
                    pres[key] = value
            
            result[uniqueId] = pres
        
        return result
    
    # =========================================================================
    # Model Discovery
    # =========================================================================
    
    def getAvailableModels(self, modelsPath: str = './nova/ui/assets/models') -> List[str]:
        """
        Get list of available .gltf/.glb models.
        
        Phase 10: Only .gltf and .glb allowed.
        """
        path = Path(modelsPath)
        if not path.exists():
            return []
        
        models = []
        for file in path.iterdir():
            if file.suffix.lower() in {'.gltf', '.glb'}:
                models.append(file.name)
        
        return sorted(models)
