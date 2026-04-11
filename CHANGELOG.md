# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] — 2026-04-11

### Fixed

- Bugfix: `--sandbox` flag to all `claude -p` subprocess invocations (`src/agents/worker.py`, `src/agents/orchestrator.py`) — worker agents now run with restricted filesystem and network access by default. Intended in 0.1.0 but missed in the initial release.

---

## [0.1.0] — 2026-04-10

### Added

- Initial project structure with Claude Harness framework.
- Master Orchestrator agent for task decomposition.
- Worker agents for parallel task execution.
- Rate limiting safeguards.
- Prompt templates for orchestrator and worker roles.
- PTY-based subprocess spawning for real TTY simulation.
- Artifact persistence (live and archived artifacts).
- `harness run` CLI entrypoint with `--mode` configuration.
- Homebrew formula for easy installation.
