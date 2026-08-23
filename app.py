from __future__ import annotations

import json
import os
import platform
import re
import shlex
import subprocess
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
runtime = {
    "status": "Idle",
    "currentTaskId": None,
    "progress": 0,
    "eta": None,
    "log": [],
    "cancelRequested": False,
}


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
    data.setdefault("queue", [])
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
    data = {"server": {}, "settings": {}, "notifications": {}, "queue": []}
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
        if section in ("server", "settings", "notifications") and raw_line.startswith("  "):
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
    queue = data.get("queue", [])
    if not queue:
        lines.append("queue: []")
    else:
        lines.append("queue:")
        for task in queue:
            items = [(key, value) for key, value in task.items() if key != "state"]
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


def choose_path(kind):
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


def task_command(task, settings):
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


def queue_with_states(data):
    current_id = public_state()["currentTaskId"]
    result = []
    for original in data["queue"]:
        task = dict(original)
        if task.get("kind") == "system":
            task["state"] = None
        elif task.get("id") == current_id:
            task["state"] = "Rendering"
        elif rendered_output_exists(task, data["settings"]):
            task["state"] = "Finished"
        else:
            task["state"] = "Waiting"
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
        progress = round((tile_fraction or 0) * 100, 1)
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
            runtime["progress"] = max(runtime["progress"], round(overall * 100, 1))
            runtime["eta"] = round(eta_seconds) if eta_seconds is not None else None
    return completed_frames


def execute_queue(existing_policy="overwrite"):
    global active_process
    with state_lock:
        if runtime["status"] == "Active":
            return
        runtime.update(status="Active", currentTaskId=None, progress=0, eta=None, log=[], cancelRequested=False)
    data = load_data()
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
                    continue
                render_tasks = [{**task, "frameStart": start, "frameEnd": end} for start, end in contiguous_ranges(missing)]
                total = int(task["frameEnd"]) - int(task["frameStart"]) + 1
                with state_lock:
                    runtime["progress"] = round(len(existing) / total * 100, 1)
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
                    append_log(f"Blender finished with exit code {code}. Queue stopped.")
                    result_status = "Failed"
                    task_failed = True
                    break
            if task_failed:
                break
            with state_lock:
                runtime["progress"] = 100
                runtime["eta"] = 0
            schedule_render_notification(task, data)
        append_log(f"Queue finished: {result_status}.")
    except Exception as error:
        append_log(f"Error: {error}")
        result_status = "Failed"
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
            data["settings"] = self.read_json()
            save_data(data)
            return self.json_response({"ok": True})
        if path == "/api/queue":
            data = load_data()
            queue = self.read_json().get("queue", [])
            data["queue"] = [{key: value for key, value in task.items() if key != "state"} for task in queue]
            save_data(data)
            return self.json_response({"ok": True})
        if path == "/api/run":
            if public_state()["status"] == "Active":
                return self.json_response({"error": "Queue is already active."}, 409)
            policy = self.read_json().get("existingFrames", "overwrite")
            if policy not in ("skip", "overwrite"):
                return self.json_response({"error": "Invalid existing frame policy."}, 400)
            threading.Thread(target=execute_queue, args=(policy,), daemon=True).start()
            return self.json_response({"ok": True})
        if path == "/api/preflight":
            return self.json_response({"animations": animation_preflight(load_data())})
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


def main():
    global server_instance, tray_process
    config = load_data()
    host = str(config["server"].get("host", "localhost")).strip() or "localhost"
    try:
        port = int(config["server"].get("port", 5050))
    except (TypeError, ValueError):
        raise ValueError("server.port must be a number between 1 and 65535.")
    if not 1 <= port <= 65535:
        raise ValueError("server.port must be a number between 1 and 65535.")
    server = ThreadingHTTPServer((host, port), Handler)
    server_instance = server
    browser_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    app_url = f"http://{browser_host}:{port}"
    print(f"Blender Render Queue: {app_url}")
    if not IS_WINDOWS:
        print("Keep this terminal open. Press Ctrl+C to stop the application.")
    tray_script = APP_DIR / "tray.ps1"
    if IS_WINDOWS and tray_script.exists():
        tray_process = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", str(tray_script), "-HostName", browser_host, "-Port", str(port)],
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


if __name__ == "__main__":
    main()
