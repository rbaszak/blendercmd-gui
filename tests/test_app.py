import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class QueueTests(unittest.TestCase):
    def test_normalize_queue_keeps_one_power_action_last(self):
        queue = [
            {"id": "power-1", "kind": "system", "action": "shutdown"},
            {"id": "render", "kind": "render"},
            {"id": "power-2", "kind": "system", "action": "suspend"},
        ]
        self.assertEqual(["render", "power-1"], [item["id"] for item in app.normalize_queue(queue)])

    def test_logging_configuration_round_trip(self):
        parsed = app.parse_yaml_config("logging:\n  enabled: true\nqueue: []\n")
        self.assertTrue(parsed["logging"]["enabled"])

    def test_custom_command_source_path(self):
        task = {"commandOverride": 'blender -b "C:\\Scenes\\hero.blend" -f 1'}
        self.assertEqual(r"C:\Scenes\hero.blend", app.task_source_path(task, {}))

    def test_custom_command_range_can_be_rewritten_for_skipped_frames(self):
        task = {
            "commandOverride": 'blender -b scene.blend -o frames_#### -s 1 -e 100 -a',
            "frameStart": 21,
            "frameEnd": 40,
            "_applyFrameRange": True,
        }
        command = app.task_command(task, {})
        self.assertEqual("21", command[command.index("-s") + 1])
        self.assertEqual("40", command[command.index("-e") + 1])

    def test_custom_output_path_is_used(self):
        task = {
            "commandOverride": 'blender -b scene.blend -o "C:\\Renders\\hero_####" -f 1',
            "type": "image",
        }
        self.assertEqual(r"C:\Renders\hero_####", str(app.task_output_prefix(task, {})))

    def test_preflight_accepts_valid_render(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blend = root / "scene.blend"
            executable = root / "blender"
            blend.touch()
            executable.touch()
            data = app.default_data()
            data["settings"].update({
                "blenderPath": str(executable),
                "imageOutputPath": str(root / "images"),
                "animationOutputPath": str(root / "animations"),
            })
            data["queue"] = [{
                "id": "render", "kind": "render", "filePath": str(blend),
                "fileName": blend.name, "type": "image", "outputFormat": "PNG",
                "frameStart": 0, "frameEnd": 0, "outputName": "scene_####",
            }]
            result = app.queue_preflight(data)
            self.assertEqual([], result["errors"])

    def test_failed_state_has_priority_over_existing_output(self):
        data = app.default_data()
        data["queue"] = [{
            "id": "render", "kind": "render", "filePath": __file__,
            "fileName": "scene.blend", "type": "image", "outputFormat": "PNG",
            "frameStart": 0, "frameEnd": 0, "outputName": "scene_####",
            "lastState": "Failed", "lastError": "Blender exited with code 1.",
        }]
        with patch.object(app, "rendered_output_exists", return_value=True):
            task = app.queue_with_states(copy.deepcopy(data))[0]
        self.assertEqual("Failed", task["state"])
        self.assertIn("code 1", task["stateReason"])

    def test_unchanged_existing_output_is_not_a_successful_render(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "scene_0000.png"
            output.write_bytes(b"old render")
            task = {
                "kind": "render", "type": "image", "outputFormat": "PNG",
                "frameStart": 0, "frameEnd": 0, "outputName": "scene_####",
            }
            settings = {"imageOutputPath": str(root)}
            before = app.snapshot_outputs(task, settings)
            self.assertIn("did not create or update", app.output_validation_error(task, settings, before))

    def test_new_output_is_a_successful_render(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = {
                "kind": "render", "type": "image", "outputFormat": "PNG",
                "frameStart": 0, "frameEnd": 0, "outputName": "scene_####",
            }
            settings = {"imageOutputPath": str(root)}
            before = app.snapshot_outputs(task, settings)
            (root / "scene_0000.png").write_bytes(b"new render")
            self.assertIsNone(app.output_validation_error(task, settings, before))

    def test_blender_render_error_is_extracted_from_report(self):
        line = "00:04.547  reports | ERROR Render output disabled in Output properties"
        self.assertEqual("Render output disabled in Output properties", app.blender_render_error(line))

    def test_notification_settings_are_normalized(self):
        result = app.normalize_notifications({
            "enabled": True,
            "server": "https://ntfy.example.com/",
            "topic": " blender ",
        })
        self.assertEqual("https://ntfy.example.com", result["server"])
        self.assertEqual("blender", result["topic"])

    def test_enabled_notifications_require_a_topic(self):
        with self.assertRaises(ValueError):
            app.normalize_notifications({"enabled": True, "server": "https://ntfy.sh", "topic": ""})

    def test_advanced_render_values_are_added_before_render_trigger(self):
        task = {
            "kind": "render", "type": "image", "outputFormat": "PNG",
            "filePath": "scene.blend", "fileName": "scene.blend",
            "frameStart": 0, "frameEnd": 0, "outputName": "scene_####",
            "resolutionPercentage": 50, "samples": 128,
        }
        settings = {"blenderPath": "blender", "imageOutputPath": "renders", "imageCustomArguments": ""}
        command = app.task_command(task, settings)
        expression_index = command.index("--python-expr")
        self.assertLess(expression_index, command.index("-f"))
        expression = command[expression_index + 1]
        self.assertIn("resolution_percentage=50", expression)
        self.assertIn("samples',128", expression)

    def test_empty_advanced_values_keep_blend_defaults(self):
        task = {
            "kind": "render", "type": "image", "outputFormat": "PNG",
            "filePath": "scene.blend", "fileName": "scene.blend",
            "frameStart": 0, "frameEnd": 0, "outputName": "scene_####",
        }
        settings = {"blenderPath": "blender", "imageOutputPath": "renders", "imageCustomArguments": ""}
        self.assertNotIn("--python-expr", app.task_command(task, settings))

    def test_image_progress_uses_samples_without_tiles(self):
        app.runtime["progress"] = 0
        app.runtime["eta"] = None
        app.update_progress(
            "Fra: 0 | Remaining: 00:45.00 | Sample 64/128",
            {"type": "image", "frameStart": 0, "frameEnd": 0},
            time.monotonic() - 10,
            set(),
        )
        self.assertEqual(50, app.runtime["progress"])
        self.assertEqual(45, app.runtime["eta"])

    def test_animation_progress_combines_frame_and_sample_progress(self):
        app.runtime["progress"] = 0
        app.runtime["eta"] = None
        app.update_progress(
            "Fra: 2 | Remaining: 00:10.00 | Rendering 50 / 100 Samples",
            {"type": "animation", "frameStart": 1, "frameEnd": 4},
            time.monotonic() - 20,
            set(),
        )
        self.assertEqual(38, app.runtime["progress"])

    def test_unnumbered_still_output_is_finalized_after_render(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = {
                "kind": "render", "type": "image", "outputFormat": "PNG",
                "frameStart": 0, "frameEnd": 0, "outputName": "scene",
            }
            settings = {"imageOutputPath": str(root)}
            temporary = root / "scene0000.png"
            before = app.output_signature(temporary)
            temporary.write_bytes(b"render")
            app.finalize_still_output(task, settings, before)
            self.assertFalse(temporary.exists())
            self.assertEqual(b"render", (root / "scene.png").read_bytes())

    def test_numbered_still_output_is_not_renamed(self):
        task = {
            "kind": "render", "type": "image", "outputFormat": "PNG",
            "frameStart": 0, "frameEnd": 0, "outputName": "scene_####",
        }
        self.assertIsNone(app.temporary_still_output(task, {"imageOutputPath": "renders"}))

    def test_power_action_can_be_replaced_or_removed(self):
        data = {"queue": [{"id": "render", "kind": "render"}, {"id": "old", "kind": "system", "action": "shutdown"}]}
        app.set_power_action(data, "suspend")
        self.assertEqual(["render", "system-suspend"], [task["id"] for task in data["queue"]])
        app.set_power_action(data, None)
        self.assertEqual(["render"], [task["id"] for task in data["queue"]])

    def test_task_result_does_not_restore_removed_power_action(self):
        latest = {"queue": [{"id": "render", "kind": "render"}]}
        with patch.object(app, "load_data", return_value=latest), patch.object(app, "save_data") as save:
            app.set_task_result("render", "Finished")
        saved = save.call_args.args[0]
        self.assertEqual("Finished", saved["queue"][0]["lastState"])
        self.assertFalse(any(task.get("kind") == "system" for task in saved["queue"]))


if __name__ == "__main__":
    unittest.main()
