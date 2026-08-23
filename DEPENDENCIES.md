# Dependencies

Blender Render Queue uses only the Python standard library. There are no Python packages to download from PyPI, so `requirements.txt` is intentionally empty.

## Linux

The application needs Python 3, Tkinter for native file dialogs, and curl for ntfy.sh notifications.

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install python3 python3-tk curl
```

### Fedora

```bash
sudo dnf install python3 python3-tkinter curl
```

### Arch Linux

```bash
sudo pacman -S python tk curl
```

Verify the installation:

```bash
python3 --version
python3 -m tkinter
curl --version
```

The Tkinter command should open a small test window.

## macOS

The recommended option is the current Python installer from python.org. It includes a macOS-native build of Tkinter. curl is included with macOS.

If Python is managed by Homebrew, install the Tk formula matching that Python version. For example:

```bash
brew install python@3.13 python-tk@3.13
```

Then verify:

```bash
python3 --version
python3 -m tkinter
curl --version
```

## Blender

Blender must be installed separately. Set the full executable path in `configuration.yaml` or in the Settings page. Typical paths are:

```text
/usr/bin/blender
/Applications/Blender.app/Contents/MacOS/Blender
```

MP4 and WEBM rendering uses FFmpeg bundled with Blender. A separate FFmpeg installation is not required.
