"""
NOVA Manifest Base Class

Defines the base manifest structure that all view manifests inherit from.
Manifests define UI semantics: allowed keys, types, validation, and layout hints.

Architecture (nova architecture.md):
- UI meaning is NOVA-owned and manifest-defined
- Manifests are versioned (manifestId + manifestVersion)
- UiUpdate/UiCheckpoint must reference a valid manifest

Design (guidelines.md):
- Small, well-named abstractions and inheritance patterns
- Explicit, deterministic logic
- No schema creep
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Any, Optional


class FieldType(str, Enum):
    """Data types for manifest fields."""
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    TIMESTAMP = "timestamp"
    POSITION = "position"  # {lat, lon, alt}
    OBJECT = "object"
    ARRAY = "array"


@dataclass
class FieldDef:
    """
    Definition of a single field in a manifest.
    
    Attributes:
        name: Field key name
        fieldType: Data type
        displayName: Human-readable label
        unit: Optional unit (e.g., "deg", "m/s")
        precision: Decimal precision for numbers
        required: Whether field must be present
        category: Grouping category for layout
    """
    name: str
    fieldType: FieldType
    displayName: str
    unit: Optional[str] = None
    precision: Optional[int] = None
    required: bool = False
    category: str = "default"
    
    def toDict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "name": self.name,
            "fieldType": self.fieldType.value,
            "displayName": self.displayName,
            "unit": self.unit,
            "precision": self.precision,
            "required": self.required,
            "category": self.category
        }


@dataclass
class Manifest(ABC):
    """
    Base manifest class for UI view definitions.
    
    All view manifests must inherit from this and define:
    - manifestId: unique identifier (e.g., "telemetry.gnss")
    - manifestVersion: semantic version (e.g., "1.0.0")
    - viewId: the view this manifest defines
    - fields: list of allowed fields
    
    Manifests are NOVA-owned and define what keys can appear
    in UiUpdate events for a given viewId.
    """
    manifestId: str
    manifestVersion: str
    viewId: str
    displayName: str
    description: str
    fields: List[FieldDef] = field(default_factory=list)
    categories: List[str] = field(default_factory=lambda: ["default"])
    
    def getAllowedKeys(self) -> Dict[str, FieldDef]:
        """Get dict of field name → FieldDef."""
        return {f.name: f for f in self.fields}
    
    def validateData(self, data: Dict[str, Any]) -> List[str]:
        """
        Validate data dict against manifest fields.
        
        Args:
            data: UiUpdate data dict
            
        Returns:
            List of validation error messages (empty = valid)
        """
        errors = []
        allowedKeys = self.getAllowedKeys()
        
        # Check required fields
        for fieldDef in self.fields:
            if fieldDef.required and fieldDef.name not in data:
                errors.append(f"Missing required field: {fieldDef.name}")
        
        # Check for unknown keys
        for key in data:
            if key not in allowedKeys:
                errors.append(f"Unknown field: {key}")
        
        # Type validation (basic)
        for key, value in data.items():
            if key in allowedKeys:
                fieldDef = allowedKeys[key]
                if not self._validateType(value, fieldDef.fieldType):
                    errors.append(f"Invalid type for {key}: expected {fieldDef.fieldType.value}")
        
        return errors
    
    def _validateType(self, value: Any, fieldType: FieldType) -> bool:
        """Basic type validation."""
        if value is None:
            return True  # None is allowed (partial updates)
        
        if fieldType == FieldType.STRING:
            return isinstance(value, str)
        elif fieldType == FieldType.NUMBER:
            return isinstance(value, (int, float))
        elif fieldType == FieldType.BOOLEAN:
            return isinstance(value, bool)
        elif fieldType == FieldType.TIMESTAMP:
            return isinstance(value, str)  # ISO8601 string
        elif fieldType == FieldType.POSITION:
            return isinstance(value, dict) and 'lat' in value and 'lon' in value
        elif fieldType == FieldType.OBJECT:
            return isinstance(value, dict)
        elif fieldType == FieldType.ARRAY:
            return isinstance(value, list)
        return True
    
    def toDict(self) -> Dict[str, Any]:
        """Convert manifest to dict for ManifestPublished event."""
        return {
            "manifestId": self.manifestId,
            "manifestVersion": self.manifestVersion,
            "viewId": self.viewId,
            "displayName": self.displayName,
            "description": self.description,
            "fields": [
                {
                    "name": f.name,
                    "fieldType": f.fieldType.value,
                    "displayName": f.displayName,
                    "unit": f.unit,
                    "precision": f.precision,
                    "required": f.required,
                    "category": f.category
                }
                for f in self.fields
            ],
            "categories": self.categories
        }
    
    @classmethod
    def fromDict(cls, data: Dict[str, Any]) -> 'Manifest':
        """Create manifest from dict (for loading from DB)."""
        fields = [
            FieldDef(
                name=f["name"],
                fieldType=FieldType(f["fieldType"]),
                displayName=f["displayName"],
                unit=f.get("unit"),
                precision=f.get("precision"),
                required=f.get("required", False),
                category=f.get("category", "default")
            )
            for f in data.get("fields", [])
        ]
        
        # Return a concrete instance (GenericManifest for loaded manifests)
        return GenericManifest(
            manifestId=data["manifestId"],
            manifestVersion=data["manifestVersion"],
            viewId=data["viewId"],
            displayName=data["displayName"],
            description=data.get("description", ""),
            fields=fields,
            categories=data.get("categories", ["default"])
        )


@dataclass
class GenericManifest(Manifest):
    """
    Generic manifest for dynamically loaded manifests.
    Used when loading ManifestPublished events from DB.
    """
    pass
