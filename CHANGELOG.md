# Changelog

Versions follow [Semantic Versioning](https://semver.org/). Add each user-visible change under **Unreleased** as it merges, under the heading that sets the next version: **Breaking** (a change users must act on) for a major, **Added** or **Removed** for a minor, **Changed**, **Fixed**, **Deprecated** or **Security** for a patch. Dependabot updates are listed at release time. Each release is a `vX.Y.Z` tag with a GitHub Release built from its section below.

## [Unreleased]

## [0.4.0] - 2026-11-11

### Security
- The Percepxion client no longer follows HTTP redirects. A redirect from the endpoint could carry the session token, and on a 307 or 308 the login request, to another host. A redirect now fails the sync with "was redirected".
- An existing Nautobot device is now adopted only when it's in `default_location` or a location under it. Before, a device in any location or tenant could be linked and renamed on a serial or name match. Use the new `adoption_locations` setting to adopt elsewhere.
- A device-reported model can no longer put a Markdown image or link in the job log.

### Added
- Setting `adoption_locations`: the location names where existing devices may be adopted, or `["*"]` for any location.

### Changed
- The job sets a soft time limit of 2 hours and a hard limit 5 minutes later, in place of Nautobot's defaults of 5 and 10 minutes, which could stop a sync of a few hundred console servers partway through its writes.

### Fixed
- A port that Nautobot validation rejects is skipped with a warning. Before, it stopped the sync partway, with earlier devices already written.
- A refused Percepxion call after a good login is reported as a read failure, and a session that ends mid-sync logs in again once. Before, both were reported as a rejected login.
- The README no longer says every secret in a Golden Config backup is proven masked. The check covers prompted secrets; the README now says what is masked and what to review.

## [0.3.2] - 2026-11-11

### Fixed
- Syncs no longer fail with "POST /v1/telemetry/result/search returned HTTP 400" when the Percepxion account is a project administrator. Reading stored `show sysconfig` output now sends the organization ID, as the job search already did.
- A failed sync now logs its reason as a **Sync failed** line in the job log, and to the worker log under `nautobot_ssot_lantronix.sync`. Before, the reason was only in the job result's traceback. The README has a new Troubleshooting section.

## [0.3.1] - 2026-11-10

### Changed
- Published on PyPI. Install with `pip install nautobot-app-ssot-lantronix`.

## [0.3.0] - 2026-11-10

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

[Unreleased]: https://github.com/Lantronix/nautobot-app-ssot-lantronix/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/Lantronix/nautobot-app-ssot-lantronix/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/Lantronix/nautobot-app-ssot-lantronix/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/Lantronix/nautobot-app-ssot-lantronix/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Lantronix/nautobot-app-ssot-lantronix/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Lantronix/nautobot-app-ssot-lantronix/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Lantronix/nautobot-app-ssot-lantronix/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Lantronix/nautobot-app-ssot-lantronix/releases/tag/v0.1.0
