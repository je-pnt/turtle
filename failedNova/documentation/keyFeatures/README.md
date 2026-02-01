# NOVA Key Features

**Comprehensive Feature Documentation**

This folder contains detailed documentation for all major NOVA features. Each feature is documented in its own file for clarity and maintainability.

---

## Feature Index

1. **[Stateless Replay](statelessReplay.md)** - Client-driven HTTP playback without server sessions
2. **[Lane Architecture](laneArchitecture.md)** - Multi-rate views over single truth database
3. **[Time-Versioned Metadata](timeVersionedMetadata.md)** - Priority-based metadata with full history
4. **[Single Database Truth](singleDatabaseTruth.md)** - Archive as authoritative source
5. **[Deterministic Messages](deterministicMessages.md)** - Stable hashing and reproducible ingestion
6. **[Command Pipeline](commandPipeline.md)** - Manifest-driven commands with audit trail
7. **[Scope Model](scopeModel.md)** - Authorization boundaries and network isolation
8. **[Receive-Time Authority](receiveTimeAuthority.md)** - novaArchive timestamps as ground truth
9. **[Three-Level Hierarchy](threeLevelHierarchy.md)** - System → Container → Asset organization
10. **[Change-Only Metadata](changeOnlyMetadata.md)** - Efficient metadata propagation

---

## Quick Reference

### Core Principles

- **Single Source of Truth**: novaArchive database is authoritative
- **Stateless Servers**: No server-side session/mode tracking
- **Client-Driven Playback**: Browser controls time, pulls data via HTTP
- **Deterministic Ingestion**: Alphabetically ordered JSON, stable hashing
- **Receive-Time Authority**: novaArchive timestamps override device timestamps

### Architecture Patterns

- **Lane Model**: Views over truth (UI 1-2Hz, firehose native rate)
- **Time-Versioned Metadata**: Full history, priority-based overrides
- **Command Manifests**: JSON-defined actions with IPC fallback
- **Scope Validation**: Authorization boundaries enforced at every layer

### Data Flows

- **Live Mode**: Producer → Archive → novaCore → Browser (WebSocket)
- **Playback Mode**: Browser → novaCore → Archive (HTTP) → Browser
- **Commands**: Browser → novaCore → NATS → GEM → hardwareService → Device

---

## Reading Guide

**New to NOVA?** Start here:
1. [Single Database Truth](singleDatabaseTruth.md) - Understand the data model
2. [Lane Architecture](laneArchitecture.md) - Understand data filtering
3. [Stateless Replay](statelessReplay.md) - Understand playback

**Implementing Producers?** Read:
1. [Deterministic Messages](deterministicMessages.md) - Message format requirements
2. [Change-Only Metadata](changeOnlyMetadata.md) - Metadata publishing
3. [Receive-Time Authority](receiveTimeAuthority.md) - Timestamp handling

**Implementing Commands?** Read:
1. [Command Pipeline](commandPipeline.md) - End-to-end command flow
2. [Scope Model](scopeModel.md) - Authorization and validation

**Implementing UI/Analysis Tools?** Read:
1. [Stateless Replay](statelessReplay.md) - HTTP replay API
2. [Time-Versioned Metadata](timeVersionedMetadata.md) - Metadata queries
3. [Lane Architecture](laneArchitecture.md) - Choosing the right lane

---

**Related Documents**:
- [nova architecture.md](../nova%20architecture.md) - System architecture overview
- [nova api.md](../nova%20api.md) - Complete API reference
- [gem architecture.md](../gem%20architecture.md) - GEM implementation details
