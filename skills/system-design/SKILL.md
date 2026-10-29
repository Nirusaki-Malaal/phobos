---
name: system-design
description: "System design framework and methodology. Load for architecture planning, capacity estimation, trade-off analysis, and standardizing real-world system design. Integrates with mermaid for visualization and pai_adr_ops for decision records."
---

# System Design

> Load this skill whenever asked to design a system, evaluate an architecture, or make high-level technical choices.
> **Primary Goal:** Standardize real-world architecture design, emphasize simplicity and cost-awareness, and persist decisions via ADRs.

---

## 1. The 7-Step Design Framework

Never jump straight to a solution. Always progress through these steps iteratively, asking the user for clarification before moving to the next.

1. **Clarify Requirements**
   - **Functional:** What exactly must the system do? Define the core use cases (e.g., "User can upload a video").
   - **Non-Functional:** What are the constraints? (Latency, throughput, availability, consistency, durability).
   - **Out of Scope:** Explicitly define what you will *not* build to keep the design focused.

2. **Estimate Scale & Capacity (Back-of-the-envelope)**
   - Calculate Traffic (RPS), Storage, and Bandwidth. (See Section 2).
   - Use these numbers to justify architectural choices (e.g., "At 50 RPS, a single Postgres instance is sufficient; we don't need Cassandra").

3. **API Contract**
   - Define the primary interactions between clients and the system (REST, GraphQL, gRPC).
   - E.g., `POST /v1/videos` -> Returns `202 Accepted` with `video_id`.

4. **Data Model & Access Patterns**
   - Identify core entities and their relationships.
   - Choose the database paradigm based on the access pattern (Read-heavy vs. Write-heavy, Relational vs. Document).

5. **High-Level Design**
   - Draw the architecture using `mermaid`.
   - Show the flow from Client -> Load Balancer -> Gateway -> Service -> Cache/DB.

6. **Deep Dive & Bottlenecks**
   - Identify the hardest technical challenge (e.g., "How do we handle a celebrity with 10M followers tweeting?").
   - Propose specific solutions (e.g., Fan-out on read vs. Fan-out on write).

7. **Trade-offs**
   - Every choice has a cost. Explicitly state what you sacrificed (e.g., "We chose eventual consistency to ensure high availability during network partitions").

---

## 2. Back-of-the-Envelope Math Cheatsheet

Use these numbers to ground your designs in reality.

### Powers of 2
- 1 KB = 10^3 bytes
- 1 MB = 10^6 bytes
- 1 GB = 10^9 bytes
- 1 TB = 10^12 bytes

### Time & Availability
- 1 day = 86,400 seconds (round to 100,000 for quick math)
- 99.9% ("Three Nines") = ~43 minutes downtime / month
- 99.99% ("Four Nines") = ~4 minutes downtime / month

### Latency Numbers Every Engineer Should Know
- L1 cache reference: 0.5 ns
- L2 cache reference: 7 ns
- Mutex lock/unlock: 100 ns
- Main memory reference: 100 ns
- Read 1 MB sequentially from memory: 250,000 ns (250 µs)
- Read 1 MB sequentially from SSD: 1,000,000 ns (1 ms)
- Round trip within same datacenter: 500,000 ns (0.5 ms)
- Send packet CA -> Netherlands -> CA: 150,000,000 ns (150 ms)

### Standard Defaults (If not specified)
- Read/Write Ratio: Assume 100:1 for social/content apps.
- User size: Assume a user record is ~1KB.

---

## 3. Trade-off Matrix

### Architecture Patterns
| Pattern | Pros | Cons | When to use |
|---|---|---|---|
| **Modular Monolith** | Easy to deploy, low latency, simple debugging | Hard boundary enforcement | Default for most new systems in 2026 |
| **Microservices** | Independent scaling, team autonomy | Network overhead, complex operations | Large orgs, strict independent deployment needs |

### Database Selection
| Type | Example | Pros | Cons |
|---|---|---|---|
| **RDBMS** | Postgres | ACID, relational integrity | Hard to scale writes horizontally |
| **NoSQL (KV/Doc)** | DynamoDB / Mongo | Infinite horizontal scale | No complex joins, eventual consistency |
| **Cache** | Redis | Sub-millisecond latency | Ephemeral (data loss on restart) |
| **Search** | Elasticsearch | Full-text, fuzzy search | Expensive, complex to manage |

### Communication
| Paradigm | Pros | Cons | When to use |
|---|---|---|---|
| **Synchronous (REST/gRPC)** | Simple, immediate feedback | Cascading failures, tight coupling | User-facing APIs, immediate consistency |
| **Asynchronous (RabbitMQ/SQS)**| Decoupled, absorbs traffic spikes | Delayed processing, complex tracing | Background jobs, email sending |
| **Event Streaming (Kafka)** | Replayable, highly scalable | Operational complexity | Real-time analytics, event-sourcing |

---

## 4. Integration with Native Tools

When executing a system design task, you **MUST** use these tools:

### 1. `mermaid` for High-Level Design
Always load `skill_view(name="mermaid")` before drawing the architecture.
- **Rule:** Use `flowchart LR` for the High-Level Design (L2 Container diagram).
- **Rule:** Do not mix abstraction levels. Keep it to Services, Databases, and Queues.

### 2. `planning-with-files` for Drafting
For complex designs, do not try to output the entire design in one chat message.
- Draft the requirements, capacity math, and API contracts in `<EXECUTION_DIR>/.project/planning/<slug>/design_draft.md`.

### 3. `pai_adr_ops` for Persistence
Once the design is finalized and confirmed by the user, **you must write an ADR** using `pai_adr_ops(action="create_adr")`.
- The ADR must capture the Context, the Decision, and the Consequences (Trade-offs).
- Include the Mermaid diagram in the ADR.
- Use `@symbol:` anchors if the architecture touches existing code symbols.
