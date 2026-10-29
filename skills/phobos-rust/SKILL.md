---
name: phobos-rust
description: "Rust implementation, ownership, error handling and async boundaries."
---

# phobos-rust

Read Cargo.toml, features, toolchain and workspace members first. Prefer borrowing and explicit domain types. Use Result for expected failures; avoid panic on external input. Keep unsafe blocks minimal with documented invariants. Bound async concurrency and propagate cancellation. After implementing, run cargo fmt, relevant tests and clippy with the project configuration; measure before changing allocation strategy.
