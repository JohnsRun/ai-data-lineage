# Rust Scanner Tuning and Deployment Guide

This guide explains how to tune and build the standalone PL/SQL registry
scanner for use with the PL/SQL Lineage Orchestrator agent. The scanner is a
Rust executable; the deployment computer does not need Python, Rust, or the
project's Python dependencies to run it.

## Project layout

```text
native/
├── Cargo.toml
├── Cargo.lock
└── src/
    ├── bin/
    │   └── plsql-lineage-scanner.rs
    ├── lib.rs
    └── registry.rs

.github/agents/
├── plsql-lineage-orchestrator.agent.md
└── plsql-lineage-orchestrator/
    ├── plsql-lineage-scanner-macos-arm64
    └── plsql-lineage-scanner-windows-x64.bin
```

- `registry.rs` implements source discovery, comment/string masking, routine
  discovery, object registration, diagnostics, and JSON output.
- `plsql-lineage-scanner.rs` is the command-line entry point.
- `Cargo.lock` pins Rust crate versions. Keep it under version control and use
  `--locked` for reproducible builds.
- The orchestrator agent selects the executable matching the host OS and
  architecture.

## Scanner usage

Pass one or more source files or directories and a required output path:

```bash
.github/agents/plsql-lineage-orchestrator/plsql-lineage-scanner-macos-arm64 \
  src/datalineague/pkg.sql -o object-registry.json
```

```powershell
& ".github/agents/plsql-lineage-orchestrator/plsql-lineage-scanner-windows-x64.bin" `
  "src/datalineague/pkg.sql" -o "object-registry.json"
```

The command writes a deterministic JSON registry containing `entities` and
`diagnostics`. It exits with a non-zero status and writes an error to stderr
when the arguments, source files, or output path are invalid. Use `--help` or
`--version` to inspect the executable.

## Safe performance tuning

1. **Establish a baseline.** Use representative PL/SQL files and directories.
   Record wall-clock time, input size, entity and diagnostic counts, and the
   exact build target and command.
2. **Preserve a reference output.** Save a registry produced by the current
   executable before changing the implementation.
3. **Find the measured hot path.** Time the stages or use a Rust profiler on
   realistic inputs. Do not optimize a function based only on how often it is
   called or how expensive it appears.
4. **Make one focused change at a time.** Keep parsing deterministic, preserve
   source evidence and line coordinates, and avoid speculative object
   relationships.
5. **Check behavior before comparing speed.** Compare complete JSON output
   against the reference, including IDs, parent IDs, signatures, line ranges,
   evidence, and diagnostics. Add a focused Rust test for every fixed edge case.
6. **Measure the release executable again.** Repeat the same runs and report
   the median, counts, and speedup. A faster run is not an improvement if it
   changes registry results unexpectedly.

Run the Rust checks from the repository root:

```bash
cargo fmt --manifest-path native/Cargo.toml -- --check
cargo test --locked --manifest-path native/Cargo.toml --release
```

The scanner has Rust unit tests for nested routines, parameters, variables,
quoted source, and malformed comments. Also run the release executable against
the project package and compare its JSON with the checked reference output.
Python package tests exercise the optional extension, not the standalone
executable.

## Build for deployment

Build a release executable on a suitable Rust toolchain. The first build
requires network access to retrieve the crates pinned by `Cargo.lock`; later
builds can use `--offline` after those crates are cached.

### macOS Apple silicon (ARM64)

On an Apple-silicon Mac:

```bash
cargo build --locked --release \
  --manifest-path native/Cargo.toml \
  --bin plsql-lineage-scanner
cp native/target/release/plsql-lineage-scanner \
  .github/agents/plsql-lineage-orchestrator/plsql-lineage-scanner-macos-arm64
chmod 755 .github/agents/plsql-lineage-orchestrator/plsql-lineage-scanner-macos-arm64
```

### Windows x64

The checked-in Windows executable is built for the `x86_64-pc-windows-gnu`
target. Build it on Windows with the GNU Rust target and a compatible MinGW-w64
linker, or build it on another host configured for that cross target:

```powershell
rustup target add x86_64-pc-windows-gnu
cargo build --locked --release --target x86_64-pc-windows-gnu `
  --manifest-path native/Cargo.toml --bin plsql-lineage-scanner
Copy-Item native/target/x86_64-pc-windows-gnu/release/plsql-lineage-scanner.exe `
  .github/agents/plsql-lineage-orchestrator/plsql-lineage-scanner-windows-x64.bin
```

For the GNU target, make sure the linker is available on `PATH`. If building on
Windows with the Microsoft toolchain instead, select an MSVC target and name
the output to clearly identify that target; do not label an MSVC build as
`windows-x64-gnu`.

### Other platforms

Use a Rust target matching the deployment OS and architecture, then name the
artifact to reflect that target. Add the corresponding executable name to the
agent instructions before deploying it. Do not run a macOS binary on Windows
or Linux, or an x64 binary on ARM64.

## Deployment checklist

- Copy the agent definition, its subagent definitions, and required lineage
  resources along with the scanner binary matching the deployment host.
- Preserve the executable permission on Unix-like systems. The Windows PE
  executable is stored with a `.bin` suffix as requested; use the Windows
  launch method supported by the deployment environment.
- Run `--version`, then scan a small known PL/SQL source and inspect the JSON
  before handing the bundle to users.
- Validate the Windows executable on a Windows x64 machine before release; a
  successful cross-compilation does not verify target-host runtime behavior.
- Rebuild and retest after changing Rust code, dependencies, compiler target,
  or scanner CLI behavior.

Rust and build tools are needed to create or update these executables, not to
run an already-built executable. The current Windows GNU executable uses
Windows system libraries and the Windows Universal C runtime; the macOS ARM64
executable links only the macOS system library.
