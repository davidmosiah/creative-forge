# Changelog

All notable changes to creative-forge are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Added Creative Latitude with separate concept `lineage_ref` and recipe
  `execution_ref`: only competitor-pattern execution requires structural swipe
  matching, while original execution retains free creative direction.
- Image QA now seals full-resolution notes per artifact; video QA seals one
  native-resolution midpoint frame per scene. Rubber-stamp `--confirm-all` was
  removed from both paths.
- Receipt verification now rechecks manifest version/provider, live readiness,
  a real read-only readback tool, every requested `PAUSED` status, and the live
  `PAUSED` readback even when an attacker recomputes the canonical digest. The
  required readiness set is rederived from the sealed canonical app config,
  anchored to a required app slug supplied outside the manifest.
- QA and publish rebind concept/execution lineage to the sealed brief, recipe,
  and research files; a competitor reference cannot be relabeled as an own
  winner by recomputing local digests.
- Installed-wheel routing and CI smoke coverage now exercise the real bundled
  image quick start.
- Expanded the regression suite from the released 284 tests to 321 tests.

### Fixed

- Serialized full Chrome launches by default on macOS, where concurrent fresh
  profiles can stall under load; an explicit environment override remains.
- Corrected the public video proof market from nonexistent `br`/`pt-BR` to the
  configured `us`/`en-US` demo market.
- Stopped claiming a live PyPI package while Trusted Publisher remains
  unconfigured externally.

## [0.1.0] - 2026-07-12

### Added

- First public release of the agent-driven paid-creative workflow
  (research → brief → localized creatives → sealed QA → PAUSED-only publish).
- Python wheel definition with CLI entry points `creative-forge` and `forge`.
- Bundled `sunrise-demo` workspace inside the wheel so
  an installed wheel can run `creative-forge preflight --app sunrise-demo`
  without a source checkout (image pipeline). Remotion video still needs the
  full git checkout + `remotion/` `npm ci`.
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
