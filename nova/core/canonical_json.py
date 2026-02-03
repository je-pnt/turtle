"""
NOVA Canonical JSON (RFC 8785 JCS)

Wrapper for RFC 8785 JSON Canonicalization Scheme for cross-language EventId stability.

Uses canonicaljson library which implements RFC 8785 subset:
- Sorted object keys
- Minimal whitespace
- UTF-8 encoding
- Number normalization (shortest decimal that round-trips)

Architecture Contract:
- EventId MUST be stable across languages (Python, C++, JavaScript, etc.)
- Same content → same eventId (idempotency for dedupe)
- Different content → different eventId (uniqueness)
"""

import canonicaljson


def canonicalJson(obj: any) -> str:
    """
    Canonical JSON serialization for EventId stability (RFC 8785 JCS).
    
    This function wraps the canonicaljson library to provide cross-language
    stable JSON serialization. Future changes to canonicalization stay contained.
    
    Args:
        obj: Python object to serialize (dict, list, str, int, float, bool, None)
        
    Returns:
        Canonical JSON string (UTF-8, sorted keys, minimal whitespace,
        normalized numbers per RFC 8785)
        
    Raises:
        TypeError: If obj contains non-serializable types
        
    Examples:
        >>> canonicalJson({"b": 2, "a": 1})
        '{"a":1,"b":2}'
        
        >>> canonicalJson({"num": 1.5})
        '{"num":1.5}'
        
        >>> canonicalJson([3, 1, 2])
        '[3,1,2]'
    
    Notes:
        - Object keys are sorted lexicographically
        - No insignificant whitespace
        - Numbers: integers as integers, floats as shortest decimal
        - Strings: UTF-8 encoded
        - This is the ONLY function that should be used for EventId payload canonicalization
    """
    # canonicaljson.encode_canonical_json returns bytes
    canonical_bytes = canonicaljson.encode_canonical_json(obj)
    
    # Decode to UTF-8 string
    return canonical_bytes.decode('utf-8')


def canonicalJsonBytes(obj: any) -> bytes:
    """
    Canonical JSON serialization returning bytes directly.
    
    Useful for hashing without decode/encode round-trip.
    
    Args:
        obj: Python object to serialize
        
    Returns:
        Canonical JSON as UTF-8 bytes
    """
    return canonicaljson.encode_canonical_json(obj)
