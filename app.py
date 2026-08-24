from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse


APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "configuration.yaml"
LEGACY_DATA_FILE = APP_DIR / "data.json"
STATIC_DIR = APP_DIR / "static"
SYSTEM = platform.system()
IS_WINDOWS = SYSTEM == "Windows"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

state_lock = threading.Lock()
process_lock = threading.Lock()
active_process: subprocess.Popen | None = None
server_instance: ThreadingHTTPServer | None = None
tray_process: subprocess.Popen | None = None
notification_threads = []
notification_lock = threading.Lock()
app_logger = logging.getLogger("blendercmd_gui")
runtime = {
    "status": "Idle",
    "currentTaskId": None,
    "progress": 0,
    "eta": None,
    "log": [],
    "cancelRequested": False,
}


def normalize_queue(queue):
    renders = [dict(task) for task in queue if task.get("kind") == "render"]
    system = next((dict(task) for task in queue if task.get("kind") == "system" and task.get("action") in ("shutdown", "suspend")), None)
    return renders + ([system] if system else [])


def default_data():
    return {
        "server": {
            "host": "localhost",
            "port": 5050,
        },
        "settings": {
            "blenderPath": r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" if IS_WINDOWS else "blender",
            "imageOutputPath": "",
            "animationOutputPath": "",
            "imageCustomArguments": "",
            "animationCustomArguments": "",
        },
        "notifications": {
            "enabled": False,
            "server": "https://ntfy.sh",
            "topic": "",
        },
        "logging": {
            "enabled": False,
        },
        "queue": [],
    }


def load_data():
    if not CONFIG_FILE.exists():
        if LEGACY_DATA_FILE.exists():
            try:
                with LEGACY_DATA_FILE.open("r", encoding="utf-8") as file:
                    data = json.load(file)
                data = merge_defaults(data)
                save_data(data)
            except (OSError, json.JSONDecodeError):
                data = default_data()
                save_data(data)
        else:
            data = default_data()
            save_data(data)
        return merge_defaults(data)
    try:
        return merge_defaults(parse_yaml_config(CONFIG_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return default_data()


def merge_defaults(data):
    defaults = default_data()
    data.setdefault("server", {})
    for key, value in defaults["server"].items():
        data["server"].setdefault(key, value)
    data.setdefault("settings", {})
    for key, value in defaults["settings"].items():
        data["settings"].setdefault(key, value)
    data.setdefault("notifications", {})
    for key, value in defaults["notifications"].items():
        data["notifications"].setdefault(key, value)
    data.setdefault("logging", {})
    for key, value in defaults["logging"].items():
        data["logging"].setdefault(key, value)
    data.setdefault("queue", [])
    data["queue"] = normalize_queue(data["queue"])
    return data


def parse_yaml_scalar(value):
    value = value.strip()
    if value == "":
        return ""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def parse_yaml_config(text):
    data = {"server": {}, "settings": {}, "notifications": {}, "logging": {}, "queue": []}
    section = None
    queue_item = None
    for number, raw_line in enumerate(text.splitlines(), 1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if not raw_line.startswith(" "):
            if ":" not in raw_line:
                raise ValueError(f"Invalid configuration line {number}")
            key, value = raw_line.split(":", 1)
            section = key.strip()
            if section not in data:
                continue
            if section == "queue" and value.strip() == "[]":
                data["queue"] = []
            queue_item = None
            continue
        if section in ("server", "settings", "notifications", "logging") and raw_line.startswith("  "):
            key, value = raw_line.strip().split(":", 1)
            data[section][key.strip()] = parse_yaml_scalar(value)
        elif section == "queue":
            stripped = raw_line.strip()
            if stripped.startswith("- "):
                queue_item = {}
                data["queue"].append(queue_item)
                stripped = stripped[2:]
            if queue_item is not None and ":" in stripped:
                key, value = stripped.split(":", 1)
                queue_item[key.strip()] = parse_yaml_scalar(value)
    return data


def yaml_scalar(value):
    return json.dumps(value, ensure_ascii=False)


def save_data(data):
    lines = ["server:"]
    for key, value in data.get("server", {}).items():
        lines.append(f"  {key}: {yaml_scalar(value)}")
    lines.append("settings:")
    for key, value in data.get("settings", {}).items():
        lines.append(f"  {key}: {yaml_scalar(value)}")
    lines.append("notifications:")
    for key, value in data.get("notifications", {}).items():
        lines.append(f"  {key}: {yaml_scalar(value)}")
    lines.append("logging:")
    for key, value in data.get("logging", {}).items():
        lines.append(f"  {key}: {yaml_scalar(value)}")
    queue = data.get("queue", [])
    if not queue:
        lines.append("queue: []")
    else:
        lines.append("queue:")
        for task in queue:
            items = [(key, value) for key, value in task.items() if key not in ("state", "missing", "stateReason", "command", "generatedCommand")]
            first_key, first_value = items[0]
            lines.append(f"  - {first_key}: {yaml_scalar(first_value)}")
            for key, value in items[1:]:
                lines.append(f"    {key}: {yaml_scalar(value)}")
    temp = CONFIG_FILE.with_suffix(".tmp")
    temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temp.replace(CONFIG_FILE)


def append_log(message):
    clean = message.rstrip()
    if not clean:
        return
    with state_lock:
        runtime["log"].append(clean)
        runtime["log"] = runtime["log"][-400:]
    if app_logger.handlers:
        app_logger.info(clean)


def configure_logging(config):
    app_logger.handlers.clear()
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = False
    if not config.get("logging", {}).get("enabled"):
        return
    log_dir = APP_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "blendercmd-gui.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
    app_logger.addHandler(handler)

    def unhandled_exception(exc_type, exc_value, exc_traceback):
        app_logger.critical("Unhandled application exception", exc_info=(exc_type, exc_value, exc_traceback))

    def unhandled_thread_exception(args):
        app_logger.critical("Unhandled thread exception", exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = unhandled_exception
    threading.excepthook = unhandled_thread_exception
    app_logger.info("Application logging enabled on %s %s", SYSTEM, platform.release())


def send_render_notification(task, config):
    notifications = config.get("notifications", {})
    if not notifications.get("enabled"):
        return
    server = str(notifications.get("server", "")).strip().rstrip("/")
    topic = str(notifications.get("topic", "")).strip()
    parsed = urlparse(server)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or not topic:
        append_log("Notification skipped: configure a valid ntfy server and topic.")
        return
    url = f"{server}/{quote(topic, safe='')}"
    try:
        result = subprocess.run(
            [
                "curl.exe" if IS_WINDOWS else "curl",
                "--fail-with-body",
                "--silent",
                "--show-error",
                "--max-time",
                "10",
                "-H",
                "Title: Render Finished",
                "-H",
                "Content-Type: text/plain; charset=utf-8",
                "--data-binary",
                task["fileName"],
                url,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"curl exit code {result.returncode}")
        append_log(f"Notification sent for {task['fileName']}.")
    except Exception as error:
        append_log(f"Notification could not be sent: {error}")


def schedule_render_notification(task, config):
    def run():
        try:
            send_render_notification(task, config)
        finally:
            with notification_lock:
                notification_threads[:] = [item for item in notification_threads if item is not threading.current_thread()]

    thread = threading.Thread(target=run, name=f"ntfy-{task['id']}")
    with notification_lock:
        notification_threads.append(thread)
    thread.start()


def wait_for_notifications(timeout=12):
    deadline = time.monotonic() + timeout
    with notification_lock:
        pending = list(notification_threads)
    for thread in pending:
        thread.join(max(0, deadline - time.monotonic()))


def stop_active_render():
    global active_process
    with state_lock:
        runtime["cancelRequested"] = True
    with process_lock:
        if active_process and active_process.poll() is None:
            active_process.terminate()


def public_state():
    with state_lock:
        return {key: (list(value) if isinstance(value, list) else value) for key, value in runtime.items() if key != "cancelRequested"}


def executable_from_command(command):
    command = os.path.expandvars(str(command).strip())
    quoted = re.match(r'^"([^"]+\.exe)"', command, re.IGNORECASE)
    unquoted = re.match(r'^(.+?\.exe)(?:\s|$)', command, re.IGNORECASE)
    match = quoted or unquoted
    if not match:
        return None
    executable = Path(match.group(1))
    if executable.name.lower() == "blender-launcher.exe":
        blender = executable.with_name("blender.exe")
        if blender.is_file():
            executable = blender
    return executable if executable.is_file() else None


def windows_blend_association():
    if not IS_WINDOWS:
        return None
    try:
        import winreg
        prog_id = None
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.blend\UserChoice") as key:
                prog_id = winreg.QueryValueEx(key, "ProgId")[0]
        except OSError:
            pass
        if not prog_id:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, ".blend") as key:
                prog_id = winreg.QueryValueEx(key, None)[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command") as key:
            return executable_from_command(winreg.QueryValueEx(key, None)[0])
    except (OSError, TypeError, ValueError):
        return None


def detect_blender_executable():
    associated = windows_blend_association()
    if associated:
        return associated
    on_path = shutil.which("blender") or shutil.which("blender.exe")
    if on_path and Path(on_path).is_file():
        return Path(on_path)
    candidates = []
    if IS_WINDOWS:
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(variable)
            if root:
                candidates.extend(Path(root).glob(r"Blender Foundation\Blender *\blender.exe"))
    elif SYSTEM == "Darwin":
        candidates.append(Path("/Applications/Blender.app/Contents/MacOS/Blender"))
    else:
        candidates.extend(Path(path) for path in ("/usr/bin/blender", "/usr/local/bin/blender", "/snap/bin/blender"))
    existing = [path for path in candidates if path.is_file()]
    existing.sort(key=lambda path: tuple(int(number) for number in re.findall(r"\d+", str(path))), reverse=True)
    return existing[0] if existing else None


def system_default_settings():
    blender = detect_blender_executable()
    return {
        "blenderFound": blender is not None,
        "settings": {
            "blenderPath": str(blender) if blender else "",
            "imageOutputPath": str(APP_DIR / "output"),
            "animationOutputPath": str(APP_DIR / "animations-output"),
        },
    }


def choose_path(kind):
    if SYSTEM == "Darwin":
        if kind == "folder":
            script = 'POSIX path of (choose folder with prompt "Select output directory")'
        elif kind == "exe":
            script = 'POSIX path of (choose file with prompt "Select Blender" of type {"com.apple.application-bundle"})'
        else:
            script = 'POSIX path of (choose file with prompt "Select a Blender project")'
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            error = result.stderr.strip()
            if "-128" in error or "canceled" in error.lower() or "cancelled" in error.lower():
                return ""
            raise RuntimeError(error or "macOS file picker could not be opened.")
        selected = Path(result.stdout.strip().rstrip("/"))
        if kind == "exe" and selected.suffix.lower() == ".app":
            blender = selected / "Contents" / "MacOS" / "Blender"
            if blender.is_file():
                return str(blender)
            raise RuntimeError("The selected application does not contain a Blender executable.")
        return str(selected)
    if not IS_WINDOWS:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            if kind == "folder":
                return filedialog.askdirectory(parent=root) or ""
            filetypes = [("Blender files", "*.blend")] if kind == "file" else [("All files", "*")]
            return filedialog.askopenfilename(parent=root, filetypes=filetypes) or ""
        finally:
            root.destroy()
    if kind in ("file", "exe"):
        filter_text = "Blender files (*.blend)|*.blend" if kind == "file" else "Blender executable (blender.exe)|blender.exe|Executables (*.exe)|*.exe"
        dialog = """
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.StartPosition = 'CenterScreen'
$owner.Size = New-Object System.Drawing.Size(1,1)
$owner.Opacity = 0
$owner.Show()
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Filter = '__FILTER__'
$d.Multiselect = $false
if ($d.ShowDialog($owner) -eq 'OK') { [Console]::OutputEncoding=[Text.Encoding]::UTF8; Write-Output $d.FileName }
$owner.Close()
""".replace("__FILTER__", filter_text)
    else:
        dialog = """
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.StartPosition = 'CenterScreen'
$owner.Size = New-Object System.Drawing.Size(1,1)
$owner.Opacity = 0
$owner.Show()
$d = New-Object System.Windows.Forms.FolderBrowserDialog
if ($d.ShowDialog($owner) -eq 'OK') { [Console]::OutputEncoding=[Text.Encoding]::UTF8; Write-Output $d.SelectedPath }
$owner.Close()
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-STA", "-Command", dialog],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Windows dialog could not be opened.")
    return result.stdout.strip()


def run_system_action(action):
    if IS_WINDOWS:
        command = ["shutdown.exe", "/s", "/t", "0"] if action == "shutdown" else ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]
    elif SYSTEM == "Linux":
        command = ["systemctl", "poweroff"] if action == "shutdown" else ["systemctl", "suspend"]
    elif SYSTEM == "Darwin":
        script = 'tell application "System Events" to shut down' if action == "shutdown" else 'tell application "System Events" to sleep'
        command = ["osascript", "-e", script]
    else:
        raise RuntimeError(f"System action {action} is not supported on {SYSTEM}.")
    subprocess.Popen(command, creationflags=NO_WINDOW)


def task_output_prefix(task, settings):
    override = str(task.get("commandOverride", "")).strip()
    if override:
        try:
            args = split_command(override)
            if "-o" in args and args.index("-o") + 1 < len(args):
                return Path(args[args.index("-o") + 1])
            if "--render-output" in args and args.index("--render-output") + 1 < len(args):
                return Path(args[args.index("--render-output") + 1])
        except ValueError:
            pass
    output_root = settings["imageOutputPath"] if task["type"] == "image" else settings["animationOutputPath"]
    if task["type"] == "animation":
        subdirectory = Path(task["fileName"]).stem or "animation"
        output_name = task["outputName"]
        if task_format(task) in ("MP4", "WEBM"):
            output_name = re.sub(r"#+", "", output_name).rstrip(" _-.") or Path(task["fileName"]).stem
            extension = ".mp4" if task_format(task) == "MP4" else ".webm"
            if not output_name.lower().endswith(extension):
                output_name += extension
        return Path(output_root) / subdirectory / output_name
    return Path(output_root) / task["outputName"]


def task_format(task):
    return task.get("outputFormat") or ("PNG" if task.get("type") == "image" else "PNG_SEQUENCE")


def split_command(command):
    parts = shlex.split(str(command), posix=not IS_WINDOWS)
    if IS_WINDOWS:
        parts = [part[1:-1] if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'" else part for part in parts]
    return parts


def format_command(args):
    return subprocess.list2cmdline(args) if IS_WINDOWS else shlex.join(args)


def task_source_path(task, settings=None):
    override = str(task.get("commandOverride", "")).strip()
    if override:
        try:
            args = split_command(override)
            if "-b" in args and args.index("-b") + 1 < len(args):
                return args[args.index("-b") + 1]
            if "--background" in args and args.index("--background") + 1 < len(args):
                return args[args.index("--background") + 1]
            blend = next((part for part in args[1:] if part.lower().endswith(".blend")), None)
            if blend:
                return blend
        except ValueError:
            pass
    return str(task.get("filePath", ""))


def task_command(task, settings):
    override = str(task.get("commandOverride", "")).strip()
    if override:
        args = split_command(override)
        if task.get("_applyFrameRange"):
            for short, long_name, value in (
                ("-s", "--frame-start", task["frameStart"]),
                ("-e", "--frame-end", task["frameEnd"]),
            ):
                option = short if short in args else long_name if long_name in args else None
                if option and args.index(option) + 1 < len(args):
                    args[args.index(option) + 1] = str(value)
                else:
                    args.extend([short, str(value)])
        return args
    output = str(task_output_prefix(task, settings))
    output_format = task_format(task)
    blender_format = {"EXR": "OPEN_EXR", "PNG_SEQUENCE": "PNG", "MP4": "FFMPEG", "WEBM": "FFMPEG"}.get(output_format, output_format)
    args = [settings["blenderPath"], "-b", task["filePath"], "-o", output, "-F", blender_format]
    custom = settings.get("imageCustomArguments" if task["type"] == "image" else "animationCustomArguments", "").strip()
    if custom:
        args += [part[1:-1] if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'" else part for part in shlex.split(custom, posix=False)]
    if output_format in ("MP4", "WEBM"):
        container, codec = ("MPEG4", "H264") if output_format == "MP4" else ("WEBM", "WEBM")
        expression = f"import bpy; f=bpy.context.scene.render.ffmpeg; f.format='{container}'; f.codec='{codec}'; f.constant_rate_factor='MEDIUM'"
        args += ["--python-expr", expression]
    if task["type"] == "image":
        args += ["-f", str(task["frameStart"])]
    else:
        args += ["-s", str(task["frameStart"]), "-e", str(task["frameEnd"]), "-a"]
    return args


def output_file_for_frame(task, settings, frame):
    output_name = re.sub(
        r"#+",
        lambda match: str(frame).zfill(len(match.group(0))),
        task["outputName"],
    )
    output_path = task_output_prefix(task, settings).with_name(output_name)
    extension = {"JPEG": ".jpg", "PNG": ".png", "EXR": ".exr", "PNG_SEQUENCE": ".png"}.get(task_format(task), "")
    if extension and output_path.suffix.lower() not in (extension, ".jpeg" if extension == ".jpg" else extension):
        output_path = output_path.with_name(output_path.name + extension)
    return output_path


def rendered_output_exists(task, settings):
    if task.get("kind") != "render":
        return False
    if task_format(task) in ("MP4", "WEBM"):
        return task_output_prefix(task, settings).is_file()
    start = int(task["frameStart"])
    end = start if task["type"] == "image" else int(task["frameEnd"])
    return all(output_file_for_frame(task, settings, frame).is_file() for frame in range(start, end + 1))


def existing_animation_frames(task, settings):
    if task_format(task) != "PNG_SEQUENCE":
        return []
    return [
        frame for frame in range(int(task["frameStart"]), int(task["frameEnd"]) + 1)
        if output_file_for_frame(task, settings, frame).is_file()
    ]


def contiguous_ranges(frames):
    if not frames:
        return []
    ranges = []
    start = previous = frames[0]
    for frame in frames[1:]:
        if frame != previous + 1:
            ranges.append((start, previous))
            start = frame
        previous = frame
    ranges.append((start, previous))
    return ranges


def animation_preflight(data):
    conflicts = []
    for task in data["queue"]:
        if task.get("kind") != "render" or task.get("type") != "animation" or task_format(task) != "PNG_SEQUENCE":
            continue
        existing = existing_animation_frames(task, data["settings"])
        total = int(task["frameEnd"]) - int(task["frameStart"]) + 1
        if 0 < len(existing) < total:
            conflicts.append({"id": task["id"], "fileName": task["fileName"], "existing": len(existing), "total": total})
    return conflicts


def missing_render_tasks(data):
    return [
        task for task in data["queue"]
        if task.get("kind") == "render" and not Path(task_source_path(task, data.get("settings"))).is_file()
    ]


def executable_available(value):
    value = str(value or "").strip()
    return bool(value and (Path(value).is_file() or shutil.which(value)))


def writable_directory(path):
    candidate = Path(str(path or "")).expanduser()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate.is_dir() and os.access(candidate, os.W_OK)


def queue_preflight(data):
    errors = []
    warnings = []
    settings = data["settings"]
    renders = [task for task in data["queue"] if task.get("kind") == "render"]
    if not renders:
        errors.append("The queue does not contain a render task.")
    checked_outputs = set()
    for task in renders:
        name = str(task.get("fileName") or "Unnamed task")
        try:
            command = task_command(task, settings)
        except ValueError as error:
            errors.append(f"{name}: invalid command syntax ({error}).")
            continue
        if not command:
            errors.append(f"{name}: the Blender command is empty.")
            continue
        if not executable_available(command[0]):
            errors.append(f"{name}: Blender executable was not found: {command[0]}")
        source = task_source_path(task, settings)
        if not source or not Path(source).is_file():
            errors.append(f"{name}: source file is missing: {source or '<empty>'}")
        try:
            start = int(task.get("frameStart"))
            end = int(task.get("frameEnd", start))
            if end < start:
                errors.append(f"{name}: end frame is lower than start frame.")
        except (TypeError, ValueError):
            errors.append(f"{name}: frame range is invalid.")
        if not str(task.get("outputName", "")).strip():
            errors.append(f"{name}: output name is empty.")
        configured_root = settings.get("imageOutputPath" if task.get("type") == "image" else "animationOutputPath", "")
        has_override_output = any(option in command for option in ("-o", "--render-output"))
        if not configured_root and not has_override_output:
            errors.append(f"{name}: output directory is not configured.")
        else:
            output_directory = str(task_output_prefix(task, settings).parent)
        if (configured_root or has_override_output) and output_directory not in checked_outputs:
            checked_outputs.add(output_directory)
            if not writable_directory(output_directory):
                errors.append(f"Output directory is not writable: {output_directory}")
            else:
                try:
                    disk_path = Path(output_directory)
                    while not disk_path.exists() and disk_path != disk_path.parent:
                        disk_path = disk_path.parent
                    free = shutil.disk_usage(disk_path).free
                    if free < 1024 * 1024 * 1024:
                        warnings.append(f"Less than 1 GB is available for output: {output_directory}")
                except OSError:
                    warnings.append(f"Available disk space could not be checked: {output_directory}")
    return {"errors": errors, "warnings": warnings, "animations": animation_preflight(data)}


def open_output_folder(task, settings):
    folder = task_output_prefix(task, settings).parent
    folder.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        script = r'''
param([string]$target)
$target = $env:BLENDERCMD_GUI_OUTPUT_FOLDER
$shell = New-Object -ComObject Shell.Application
$shell.Open($target)
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class WindowFocus {
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, IntPtr processId);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint source, uint target, bool attach);
    [DllImport("user32.dll")] public static extern void keybd_event(byte key, byte scan, uint flags, UIntPtr extra);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr after, int x, int y, int cx, int cy, uint flags);
}
"@
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 100
    $window = $shell.Windows() | Where-Object {
        try { $_.Document.Folder.Self.Path -eq $target } catch { $false }
    } | Select-Object -First 1
    if ($window) {
        $handle = [IntPtr]$window.HWND
        $foreground = [WindowFocus]::GetForegroundWindow()
        $foregroundThread = [WindowFocus]::GetWindowThreadProcessId($foreground, [IntPtr]::Zero)
        $targetThread = [WindowFocus]::GetWindowThreadProcessId($handle, [IntPtr]::Zero)
        [WindowFocus]::AttachThreadInput($foregroundThread, $targetThread, $true) | Out-Null
        [WindowFocus]::ShowWindowAsync($handle, 9) | Out-Null
        [WindowFocus]::BringWindowToTop($handle) | Out-Null
        [WindowFocus]::SetWindowPos($handle, [IntPtr](-1), 0, 0, 0, 0, 0x0003) | Out-Null
        [WindowFocus]::SetWindowPos($handle, [IntPtr](-2), 0, 0, 0, 0, 0x0003) | Out-Null
        # A short Alt key pulse grants this user-initiated process permission to
        # cross Windows' foreground-lock boundary without leaving a key pressed.
        [WindowFocus]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)
        [WindowFocus]::SetForegroundWindow($handle) | Out-Null
        [WindowFocus]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
        [WindowFocus]::AttachThreadInput($foregroundThread, $targetThread, $false) | Out-Null
        break
    }
}
'''
        focus_environment = os.environ.copy()
        focus_environment["BLENDERCMD_GUI_OUTPUT_FOLDER"] = str(folder.resolve())
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
            creationflags=NO_WINDOW,
            env=focus_environment,
        )
    elif SYSTEM == "Darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])
    return str(folder)


def queue_with_states(data):
    current_id = public_state()["currentTaskId"]
    result = []
    for original in data["queue"]:
        task = dict(original)
        if task.get("kind") == "system":
            task["state"] = None
            task["missing"] = False
        else:
            task["missing"] = not Path(task_source_path(task, data.get("settings"))).is_file()
            if task["missing"]:
                task["state"] = "File missing"
                task["stateReason"] = "Source .blend file does not exist."
            elif task.get("id") == current_id:
                task["state"] = "Rendering"
            elif task.get("lastState") in ("Failed", "Cancelled"):
                task["state"] = task["lastState"]
                task["stateReason"] = str(task.get("lastError", ""))
            elif rendered_output_exists(task, data["settings"]):
                task["state"] = "Finished"
            else:
                task["state"] = "Waiting"
            try:
                task["command"] = format_command(task_command(task, data["settings"]))
            except ValueError:
                task["command"] = str(task.get("commandOverride", ""))
            generated = dict(task)
            generated.pop("commandOverride", None)
            task["generatedCommand"] = format_command(task_command(generated, data["settings"]))
        result.append(task)
    return result


def update_progress(line, task, started, completed_frames):
    frame_match = re.search(r"Fra:\s*(\d+)", line)
    tiles_match = re.search(r"Rendered\s+(\d+)/(\d+)\s+Tiles", line, re.IGNORECASE)
    samples_match = re.search(r"Sample\s+(\d+)/(\d+)", line, re.IGNORECASE)
    remaining_match = re.search(r"Remaining:\s*([\d:.]+)", line, re.IGNORECASE)

    remaining_seconds = None
    if remaining_match:
        try:
            parts = [float(part) for part in remaining_match.group(1).split(":")]
            if len(parts) == 2:
                remaining_seconds = parts[0] * 60 + parts[1]
            elif len(parts) == 3:
                remaining_seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
        except ValueError:
            pass

    tile_fraction = None
    if tiles_match:
        rendered, total_tiles = map(int, tiles_match.groups())
        if total_tiles:
            if samples_match:
                sample, total_samples = map(int, samples_match.groups())
                sample_fraction = min(1.0, max(0.0, sample / total_samples)) if total_samples else 0
                # Blender numbers the tile currently being sampled. Combine completed
                # tiles with the fraction of samples from the current tile.
                tile_fraction = (max(0, rendered - 1) + sample_fraction) / total_tiles
            else:
                tile_fraction = rendered / total_tiles
            tile_fraction = min(1.0, max(0.0, tile_fraction))

    if task["type"] == "image":
        progress = round((tile_fraction or 0) * 100)
        with state_lock:
            # Some Blender builds briefly report the completed sample count while
            # already switching the tile number. Never move the progress bar back.
            runtime["progress"] = max(runtime["progress"], progress)
            runtime["eta"] = round(remaining_seconds) if remaining_seconds is not None else None
        return completed_frames

    if frame_match:
        frame = int(frame_match.group(1))
        total_frames = task["frameEnd"] - task["frameStart"] + 1
        frames_before = min(total_frames, max(0, frame - task["frameStart"]))
        current_fraction = tile_fraction or 0
        overall = min(1.0, (frames_before + current_fraction) / total_frames)
        elapsed = max(0.01, time.monotonic() - started)
        eta_seconds = None
        if remaining_seconds is not None:
            completed_equivalent = max(0.01, frames_before + current_fraction)
            average_frame = elapsed / completed_equivalent
            future_frames = max(0, total_frames - frames_before - 1)
            eta_seconds = remaining_seconds + average_frame * future_frames
        elif overall > 0:
            eta_seconds = elapsed / overall * (1 - overall)
        with state_lock:
            runtime["progress"] = max(runtime["progress"], round(overall * 100))
            runtime["eta"] = round(eta_seconds) if eta_seconds is not None else None
    return completed_frames


def set_task_result(data, task_id, state=None, reason=""):
    for task in data["queue"]:
        if task.get("id") == task_id and task.get("kind") == "render":
            if state:
                task["lastState"] = state
                task["lastError"] = reason
            else:
                task.pop("lastState", None)
                task.pop("lastError", None)
            save_data(data)
            return


def execute_queue(existing_policy="overwrite"):
    global active_process
    with state_lock:
        if runtime["status"] == "Active":
            return
        runtime.update(status="Active", currentTaskId=None, progress=0, eta=None, log=[], cancelRequested=False)
    data = load_data()
    for queued_task in data["queue"]:
        if queued_task.get("kind") == "render":
            queued_task.pop("lastState", None)
            queued_task.pop("lastError", None)
    save_data(data)
    queue = list(data["queue"])
    settings = data["settings"]
    result_status = "Finished"
    append_log("Queue started.")
    try:
        for task in queue:
            with state_lock:
                if runtime["cancelRequested"]:
                    result_status = "Cancelled"
                    break
                runtime.update(currentTaskId=task["id"], progress=0, eta=None)
            if task["kind"] == "system":
                append_log(f"System action: {task['action']}")
                wait_for_notifications()
                run_system_action(task["action"])
                continue
            task_output_prefix(task, settings).parent.mkdir(parents=True, exist_ok=True)
            append_log(f"Rendering {task['fileName']}")
            started = time.monotonic()
            completed_frames = set()
            render_tasks = [task]
            if task["type"] == "animation" and task_format(task) == "PNG_SEQUENCE" and existing_policy == "skip":
                existing = set(existing_animation_frames(task, settings))
                missing = [frame for frame in range(int(task["frameStart"]), int(task["frameEnd"]) + 1) if frame not in existing]
                if not missing:
                    append_log(f"Skipping {task['fileName']}: all frames already exist.")
                    with state_lock:
                        runtime["progress"] = 100
                    set_task_result(data, task["id"], "Finished")
                    continue
                render_tasks = [
                    {**task, "frameStart": start, "frameEnd": end, "_applyFrameRange": True}
                    for start, end in contiguous_ranges(missing)
                ]
                total = int(task["frameEnd"]) - int(task["frameStart"]) + 1
                with state_lock:
                    runtime["progress"] = round(len(existing) / total * 100)
                append_log(f"Skipping {len(existing)} existing frame(s); rendering {len(missing)} missing frame(s).")
            task_failed = False
            for render_task in render_tasks:
                args = task_command(render_task, settings)
                with process_lock:
                    active_process = subprocess.Popen(
                        args,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        creationflags=NO_WINDOW,
                    )
                assert active_process.stdout is not None
                for line in active_process.stdout:
                    append_log(line)
                    completed_frames = update_progress(line, task, started, completed_frames)
                code = active_process.wait()
                with process_lock:
                    active_process = None
                if code != 0:
                    with state_lock:
                        cancelled = runtime["cancelRequested"]
                    if cancelled:
                        reason = "Cancelled by user."
                        result_status = "Cancelled"
                    else:
                        reason = f"Blender exited with code {code}."
                        result_status = "Failed"
                    append_log(f"{reason} Queue stopped.")
                    set_task_result(data, task["id"], result_status, reason)
                    task_failed = True
                    break
            if task_failed:
                break
            with state_lock:
                runtime["progress"] = 100
                runtime["eta"] = 0
            set_task_result(data, task["id"], "Finished")
            schedule_render_notification(task, data)
        append_log(f"Queue finished: {result_status}.")
    except Exception as error:
        append_log(f"Error: {error}")
        result_status = "Failed"
        current_id = public_state().get("currentTaskId")
        if current_id:
            set_task_result(data, current_id, "Failed", str(error))
    finally:
        with state_lock:
            runtime.update(status=result_status, currentTaskId=None, eta=None)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def json_response(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/data":
            data = load_data()
            data["runtime"] = public_state()
            data["queue"] = queue_with_states(data)
            return self.json_response(data)
        if path == "/api/system-defaults":
            return self.json_response(system_default_settings())
        if path.startswith("/api/dialog/"):
            try:
                return self.json_response({"path": choose_path(path.rsplit("/", 1)[-1])})
            except Exception as error:
                return self.json_response({"error": str(error)}, 500)
        requested = "index.html" if path == "/" else path.lstrip("/")
        file_path = (STATIC_DIR / requested).resolve()
        if STATIC_DIR.resolve() not in file_path.parents or not file_path.is_file():
            self.send_error(404)
            return
        mime = {".html": "text/html", ".css": "text/css", ".js": "application/javascript"}.get(file_path.suffix, "application/octet-stream")
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{mime}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/settings":
            data = load_data()
            settings = self.read_json()
            try:
                for key in ("imageOutputPath", "animationOutputPath"):
                    if str(settings.get(key, "")).strip():
                        Path(settings[key]).mkdir(parents=True, exist_ok=True)
            except OSError as error:
                return self.json_response({"error": f"Could not create output directory: {error}"}, 400)
            data["settings"] = settings
            save_data(data)
            return self.json_response({"ok": True})
        if path == "/api/queue":
            if public_state()["status"] == "Active":
                return self.json_response({"error": "The queue cannot be changed while rendering."}, 409)
            data = load_data()
            queue = self.read_json().get("queue", [])
            system_tasks = [task for task in queue if task.get("kind") == "system"]
            if len(system_tasks) > 1:
                return self.json_response({"error": "Only one shutdown or suspend task can be added."}, 400)
            clean = [
                {key: value for key, value in task.items() if key not in ("state", "missing", "stateReason", "command", "generatedCommand")}
                for task in queue
            ]
            data["queue"] = normalize_queue(clean)
            save_data(data)
            return self.json_response({"ok": True, "queue": queue_with_states(data)})
        if path == "/api/run":
            if public_state()["status"] == "Active":
                return self.json_response({"error": "Queue is already active."}, 409)
            check = queue_preflight(load_data())
            if check["errors"]:
                return self.json_response({"error": "Preflight failed.", "preflight": check}, 409)
            policy = self.read_json().get("existingFrames", "overwrite")
            if policy not in ("skip", "overwrite"):
                return self.json_response({"error": "Invalid existing frame policy."}, 400)
            threading.Thread(target=execute_queue, args=(policy,), daemon=True).start()
            return self.json_response({"ok": True})
        if path == "/api/preflight":
            return self.json_response(queue_preflight(load_data()))
        if path == "/api/open-output":
            data = load_data()
            task_id = self.read_json().get("id")
            task = next((item for item in data["queue"] if item.get("id") == task_id and item.get("kind") == "render"), None)
            if not task:
                return self.json_response({"error": "Render task not found."}, 404)
            try:
                return self.json_response({"ok": True, "path": open_output_folder(task, data["settings"])})
            except Exception as error:
                return self.json_response({"error": f"Could not open output folder: {error}"}, 500)
        if path == "/api/cancel":
            stop_active_render()
            return self.json_response({"ok": True})
        if path == "/api/exit":
            self.json_response({"ok": True})
            stop_active_render()
            if server_instance:
                threading.Thread(target=server_instance.shutdown, daemon=True).start()
            return
        self.send_error(404)


class AppServer(ThreadingHTTPServer):
    allow_reuse_address = False
    allow_reuse_port = False

    def handle_error(self, request, client_address):
        if app_logger.handlers:
            app_logger.exception("Unhandled HTTP error from %s", client_address)
        super().handle_error(request, client_address)


def show_startup_error(message):
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "BlenderCMD Render GUI", 0x10)
            return
        except Exception:
            pass
    print(f"Error: {message}", file=sys.stderr)


def main():
    global server_instance, tray_process
    config = load_data()
    configure_logging(config)
    host = str(config["server"].get("host", "localhost")).strip() or "localhost"
    try:
        port = int(config["server"].get("port", 5050))
    except (TypeError, ValueError):
        raise ValueError("server.port must be a number between 1 and 65535.")
    if not 1 <= port <= 65535:
        raise ValueError("server.port must be a number between 1 and 65535.")
    try:
        server = AppServer((host, port), Handler)
    except OSError as error:
        if error.errno in (48, 98, 10048) or getattr(error, "winerror", None) == 10048:
            show_startup_error(
                f"BlenderCMD Render GUI could not start because {host}:{port} is already in use.\n\n"
                "The application is probably already running. Check the system tray or choose a different port in configuration.yaml."
            )
            return 1
        show_startup_error(f"BlenderCMD Render GUI could not start on {host}:{port}.\n\n{error}")
        return 1
    server_instance = server
    browser_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    app_url = f"http://{browser_host}:{port}"
    print(f"Blender Render Queue: {app_url}")
    if not IS_WINDOWS:
        print("Keep this terminal open. Press Ctrl+C to stop the application.")
    tray_script = APP_DIR / "tray.ps1"
    if IS_WINDOWS and tray_script.exists():
        tray_process = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", str(tray_script), "-HostName", browser_host, "-Port", str(port), "-AppProcessId", str(os.getpid())],
            creationflags=NO_WINDOW,
        )
    threading.Timer(0.7, lambda: webbrowser.open(app_url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_active_render()
        server.server_close()
        server_instance = None
        if tray_process and tray_process.poll() is None:
            tray_process.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
