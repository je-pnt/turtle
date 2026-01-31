# Phase 2 — Transport + Producer Adapter Contract (v1)

## Architecture check (what this must preserve)
- Producers may integrate **without the SDK**: a **public routing + envelope contract** exists.
- `sdk.transport` is still the preferred boundary, but it must implement (not invent) the public contract.
- Core ingests **append-only truth**, dedupes by **EventId**, and uses the **single ordering contract** (timebase → lane priority → within-lane → EventId).
- Payload NOVA filters by `scopeId`; Ground subscribes to all scopes.

---

## Public routing keys
RouteKey = `(scopeId, lane, identityKey, schemaVersion)`

- `scopeId`: **alphanumeric** (`[A-Za-z0-9]+`)
- `lane`: `Raw | Parsed | UI | Command | Metadata`
- `identityKey`: lane-specific primary identifier (see below)
- `schemaVersion`: integer (1, 2, …)

### Deterministic transport address (version suffix)
Suggested subject/path format:
- `nova.{scopeId}.{lane}.{identityKey}.v{schemaVersion}`

Notes:
- Version is **last** so Core can subscribe to multiple versions (e.g., `*.v1` + `*.v2`) simultaneously.
- `identityKey` must be encoded so it never contains separators (recommend URL-safe chars: `[A-Za-z0-9_\-:.]+`).

`sdk.transport` should provide `formatAddressV1(routeKey)` (pure function) and unit-test it for determinism.
3rd parties can implement the same formatter without the SDK.

---

## Envelope v1 (truth message)
Every message carries a self-contained envelope (JSON lanes = JSON; Raw = bytes/base64 as needed):

Required fields:
- `schemaVersion` (int)
- `eventId` (stable content hash)
- `scopeId` (alphanumeric)
- `lane`
- `identityKey`
- `sourceTruthTime` (producer time)
- `payload` (lane-specific)

Core assigns:
- `canonicalTruthTime` at ingest (receive time)

### Trust policy (prefer “don’t drop data”)
Core should **trust envelope fields** as authoritative for `scopeId/lane/identityKey/schemaVersion`.
The transport address is treated as routing only; if mismatch occurs, ingest using the envelope and log/counter it.

---

## entityIdentityKey (universal)
The unified entity identity for all lanes:
- `systemId|containerId|uniqueId`

This replaces per-lane identity keys. All lanes use the same entity identity triplet.

---

## EventId (producer optional)
- Producer may compute EventId before publish; NOVA computes if missing using stable hash.
- NOVA rejects if envelope EventId conflicts with derived EventId (no silent mutation).
- Use RFC 8785 JCS canonicalization for JSON lanes for cross-language stability.
- Hash construction (v1): `SHA256(EID_v1 + scopeId + lane + entityIdentityKey + sourceTruthTime + canonicalPayload)` where entityIdentityKey = `systemId|containerId|uniqueId`
- Raw lane: hash raw bytes directly (no JSON).

---

## Phase 2 “how to implement” (suggestive, not exhaustive)
- Core:
  - subscribe via `sdk.transport` using scope filter (payload) or all scopes (ground)
  - validate envelope required fields; assign `canonicalTruthTime`; ingest (atomic dedupe + insert)
- hardwareService:
  - add `novaAdapter` that hooks existing outputs (wrap ioLayer/device plugin outputs)
  - publish Raw + Parsed + Metadata envelopes via `sdk.transport.publish(routeKey, envelope)`

---

## Phase 2 tests (recommended “medium”)
- Publish sample **Raw + Parsed + Metadata** → Core ingests → DB rows exist in correct lane tables.
- Dedupe: publish the same envelope twice → one DB entry (EventId dedupe).
- Uniqueness: publish two distinct envelopes → two DB entries with different EventIds.
