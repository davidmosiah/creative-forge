# Changelog

All notable changes to creative-forge are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-07-12

### Added

- First public release of the agent-driven paid-creative workflow
  (research → brief → localized creatives → sealed QA → PAUSED-only publish).
- PyPI package `creative-forge` with CLI entry points `creative-forge` and `forge`.
- Bundled `sunrise-demo` workspace inside the wheel so
  `pip install creative-forge && creative-forge preflight --app sunrise-demo`
  works without cloning (image pipeline). Remotion video still needs the full
  git checkout + `remotion/` `npm ci`.
- Workspace root resolution via `CREATIVE_FORGE_ROOT`, checkout detection, or
  the bundled wheel workspace (`scripts.paths.default_root`).
- GitHub Release `v0.1.0` + CHANGELOG as the distribution front door.
- CI workflow `publish-pypi.yml` (Trusted Publisher / OIDC) for release publishes.

### Notes

- License: AGPL-3.0
- Tests: 284 unit tests in CI
- Not an MCP server — distribute via PyPI / GitHub / agent skills, not
  Smithery/Glama/MCP registries

[Unreleased]: https://github.com/davidmosiah/creative-forge/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/davidmosiah/creative-forge/releases/tag/v0.1.0
