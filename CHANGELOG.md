# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versions before 1.0.0 are pre-release: breaking changes may land in a minor bump, and
they are called out explicitly where they do.

## [Unreleased]

## [0.4.2] - 2026-08-18

Packaging only — no functional change to the integration. Cut so that the contents HACS
sees include the validation workflows and the brand icon, which [0.4.1] predates.

### Added

- Brand icon shipped with the integration, in `brand/icon.png` and `brand/icon@2x.png`.
  Home Assistant 2026.3 lets custom integrations carry their own brand images, so no
  submission to `home-assistant/brands` is needed; older Home Assistant simply shows no
  icon. The artwork is original — a luminaire over water casting the SC20's three real
  channels — deliberately not the vendor's logo.
- CI: a `Validate` workflow running the HACS action and hassfest, and a `Tests` workflow
  running ruff and pytest. Both run weekly as well as on push, because those validators
  track upstream releases and a repository that passes today can fail next month without
  anything here changing.

### Changed

- `hacs.json` trimmed to keys that are still in the schema. `render_readme` has been
  removed upstream, and `content_in_root`/`zip_release` only restated defaults.
- Repository topics set, which HACS requires for inclusion in its default store.

### Removed

- `custom_components/__init__.py`, which was never needed and is not part of the layout
  Home Assistant expects.

## [0.4.1] - 2026-08-14

### Fixed

- **Reported brightness froze at its last polled value.** Home Assistant would show the
  lights at, say, 90 % while the schedule had actually dimmed the tank; the value had been
  stuck since the connection was established.

  The coordinator published pushed state with `async_set_updated_data`, which by design
  resets the refresh timer. The client heartbeats every 3 seconds and the device answers
  each one, so the 30-second timer was reset ten times per interval and the poll never
  fired. The live channel values are the one thing that is only ever polled. Pushes now
  notify entities without touching the refresh schedule.

- The client now reports a change only when a value actually differs. The heartbeat
  returns an identical frame indefinitely; treating each as news rewrote every entity's
  state twenty times a minute and filled the recorder with nothing.

## [0.4.0] - 2026-08-14

### Added

- **Install button on the firmware update entities.** Sends `START_FOTA`, after which the
  controller downloads from the vendor, flashes and reboots.

  It is guarded: it refuses unless the controller is connected and reports a revision
  newer than it runs, then follows the device through the reboot and confirms the revision
  actually moved rather than assuming success. A timeout is reported as "did not report a
  new version in time" rather than as failure, because the device may still be writing.

  Installing carries real risk — the controller is off the network for minutes, there is
  no cancel, and a failed write needs a manual reflash. It is a supervised action, not one
  for an automation.

### Fixed

- The install progress indicator was inert. Home Assistant only surfaces `in_progress`
  when the entity declares `UpdateEntityFeature.PROGRESS`, so mid-flash the entity would
  have looked idle.

## [0.3.0] - 2026-08-14

### Changed

- **Breaking: the moonlight window entities moved from `number` to `time`.**
  `number.<tank>_moonlight_start` / `_end` are now `time.<tank>_moonlight_start` / `_end`.
  Anything referencing the old ids needs updating. They held minutes since midnight, which
  is how the device stores them but not how anyone thinks about 22:00; they are now proper
  HH:MM fields. The old entities were removed rather than kept alongside, because two
  entities editing one field would fight.
- Relicensed to Apache-2.0 from AGPL-3.0-or-later. Permissive, and the licence Home
  Assistant core uses, so proposing the integration upstream is possible again.

### Added

- Firmware update check, as `update` entities for the controller firmware and the web app.
  The controller polls `data.daytime.de` itself and reports what it finds, so these
  surface its verdict; Home Assistant makes no request to the vendor. The two are tracked
  separately because the device updates them independently.

### Fixed

- A window that wraps past midnight is left alone. This hardware ships configured 22:00 to
  06:00, so an end earlier than the start is normal, not an error to correct.
- When the device cannot reach the vendor it reports revision `[0, 0]`. Taken literally
  that reads as "version 00.0" and showed a permanent phantom downgrade; it now falls back
  to the installed version.

## [0.2.0] - 2026-08-14

### Added

- **Configuration frontend**, reachable from Configure on the integration's card, with
  pages laid out like the controller's own web interface: Daycycle, Moonlight, Cloud
  simulation, Acclimatisation, Connection. Only Connection stores anything in Home
  Assistant; the rest write straight to the lamp and re-read it when the page opens.

  The Daycycle page reproduces the vendor app's easy mode. Feeding a device its own stored
  settings through the generator reproduces the schedule it is actually running, setpoint
  for setpoint. Saving backs up the previous programme first and warns before flattening a
  hand-edited expert curve.

### Fixed

- With per-colour brightness off, the daycycle now reads `intensity` rather than a
  possibly stale `intensities`. A real device was observed holding `intensities:85,85,85`
  alongside `intensity:90` while running 90.
- Acclimatisation's device-maintained day counter is preserved on save, so editing the
  settings no longer restarts a weeks-long ramp.

## [0.1.0] - 2026-08-14

First release.

### Added

- Home Assistant integration for the daytime SC20 aquarium LED controller, over its local
  WebSocket. No cloud and no account — the device offers neither.
- Lights: a master plus one per channel (white, blue, red).
- A mode select for daycycle versus manual, which is how the controller actually works:
  the two are mutually exclusive, and setting a level has no effect in scheduled mode.
- Switches for moonlight, cloud simulation and acclimatisation, with number entities for
  their parameters.
- Diagnostic sensors: mode, live output level, programmed level, firmware version, uptime,
  free memory, operating hours, mesh clients.
- Services `set_daycycle`, `get_daycycle`, `load_scenario`, `save_scenario`,
  `preview_curve` and `set_clock`. Anything that overwrites the schedule snapshots the
  previous one first, because the controller has no undo and does not acknowledge writes.
- English and German translations.
- The recovered protocol reference under `docs/protocol/`, labelling every claim
  CONFIRMED, INFERRED or UNKNOWN, and `tools/sc20_probe.py`, which produced the captures.

[Unreleased]: https://github.com/dc0sk/daytime/compare/v0.4.2...HEAD
[0.4.2]: https://github.com/dc0sk/daytime/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/dc0sk/daytime/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/dc0sk/daytime/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/dc0sk/daytime/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/dc0sk/daytime/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/dc0sk/daytime/releases/tag/v0.1.0
