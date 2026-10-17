# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-10-17

### Added

- Multi-row queue selection and keyboard shortcuts.
- Context menus for editing, duplicating, opening output folders, modifying commands, and deleting tasks.
- Queue preflight checks before Blender starts.
- Persistent failed and cancelled task states with reasons.
- Optional rotating application and Blender output logs.
- Cross-platform GitHub Actions checks.
- Drag-and-drop reordering for individual tasks and selected groups.

### Changed

- The Windows launcher now discovers the installed Python interpreter instead of relying on `C:\Windows\pyw.exe`.
- Shutdown and suspend actions are mutually exclusive, unique, and always kept at the end of the queue.
- Output folders opened from the Windows context menu are brought to the foreground.
- The queue is constrained to the available viewport height and scrolls internally with a sticky header.
- Successful preflight checks now start rendering immediately without an extra confirmation dialog.
- Render success now requires the expected output to be created or updated, even when Blender exits with code 0.
- Enabled logging adds a current-log download section to Settings.
- ntfy.sh notifications can now be enabled and configured directly from Settings.
- Add/Edit Task includes optional per-task resolution percentage and sample overrides.
- Active render status displays any per-task resolution and sample overrides beside the filename.
- Progress now advances from sample-only Blender output when tile counters are unavailable.
- Still-image tasks default to an output name without a frame-number suffix; animation names retain `_####`.
- The currently rendering queue row is highlighted and automatically scrolled into view.
- Add New Task supports selecting multiple `.blend` files and configuring them sequentially.
- Queue selection and context menus are locked while rendering, leaving only the active-task highlight visible.
- Shutdown and suspend are live switch controls, hidden from queue rows, that can be changed or removed while renders are running.
- Render result updates no longer restore a shutdown or suspend action disabled during an active render.

## [0.1.0] - 2026-08-23

### Added

- Initial public release of BlenderCMD Render GUI.
- Browser-based render queue for still images and animations.
- JPEG, PNG, OpenEXR, PNG sequence, MP4, and WebM output formats.
- Reordering, deleting, and clearing queued tasks.
- Detection of completed renders and partial PNG sequences.
- Choice between skipping existing animation frames and overwriting them.
- Live Blender output, tile/sample progress, and remaining-time estimates.
- Whole-number render progress for a cleaner status display.
- Missing-file detection with queue warnings and render blocking.
- First-run setup prompt when required paths are empty.
- Optional first-run detection of the system Blender executable and local output directories.
- Successfully detected system defaults are saved automatically.
- Project licensing changed from The Unlicense to the MIT License.
- Native macOS file pickers replace the crash-prone Tk dialog implementation.
- Windows file pickers no longer flash a PowerShell console window.
- Platform launchers use explicit Windows, Linux, and macOS filenames.
- Reliable single-instance port handling and tray shutdown fallback.
- Dark and light themes with the selected theme remembered locally.
- Separate output directories and custom Blender arguments for images and animations.
- Per-animation output subdirectories.
- Asynchronous ntfy.sh notifications after successful renders.
- Optional shutdown and suspend queue actions.
- Dark and light themes with persistent browser preference.
- Configurable application host and port.
- YAML configuration and queue persistence.
- Windows background launch and system tray integration.
- Linux and macOS terminal launchers.
- Interactive GitHub Pages demo.

[Unreleased]: https://github.com/rbaszak/blendercmd-gui/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/rbaszak/blendercmd-gui/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/rbaszak/blendercmd-gui/releases/tag/v0.1.0
