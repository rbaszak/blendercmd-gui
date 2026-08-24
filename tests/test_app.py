import copy
import tempfile
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


if __name__ == "__main__":
    unittest.main()
