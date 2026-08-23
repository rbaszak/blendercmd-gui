# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: https://github.com/rbaszak/blendercmd-gui/releases/tag/v0.1.0
