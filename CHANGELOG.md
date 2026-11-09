# Changelog

Versions follow [Semantic Versioning](https://semver.org/). Add each user-visible change under **Unreleased** as it merges, under the heading that sets the next version: **Breaking** (a change users must act on) for a major, **Added** or **Removed** for a minor, **Changed**, **Fixed**, **Deprecated** or **Security** for a patch. Dependabot updates are listed at release time. Each release is a `vX.Y.Z` tag with a GitHub Release built from its section below.

## [Unreleased]

### Added
- Devices carry the running firmware Percepxion reports, in a new `lx_firmware_version` custom field.

### Fixed
- The package now requires Python 3.11 or later, matching Nautobot 3.x. It listed Python 3.10, where Nautobot 3 can't be installed.

## [0.2.0] - 2026-11-06

### Added
- A Docker quick start in `docker/` that runs Nautobot with nautobot-ssot and the app, built from this repository.
- SLC and EMG device type definitions in `docker/device-types`, with a setup script that imports them and creates the default location.

### Fixed
- The install steps now use a release archive, and the README's device type steps point to definitions in this repository.

## [0.1.1] - 2026-11-05

### Fixed
- A port whose status is Disconnected now reports nothing attached, even while the SLC still keeps the managed device record, so pulling a console cable shows up as a detach.

## [0.1.0] - 2026-10-31

### Added
- First release. Nautobot SSoT integration that syncs Lantronix console servers, console ports and attached devices from Percepxion, with ServiceNow console paths and Golden Config backups and compliance for SLC devices.

[Unreleased]: https://github.com/Lantronix/nautobot-app-ssot-lantronix/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Lantronix/nautobot-app-ssot-lantronix/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Lantronix/nautobot-app-ssot-lantronix/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Lantronix/nautobot-app-ssot-lantronix/releases/tag/v0.1.0
