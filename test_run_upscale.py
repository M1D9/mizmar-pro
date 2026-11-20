import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_upscale


def make_box(box_type, payload=b""):
    return (len(payload) + 8).to_bytes(4, "big") + box_type.encode("ascii") + payload


class VideoSettingsTests(unittest.TestCase):
    def test_upscale_accepts_original_fps_and_face_model(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.mp4"
            config_path = Path(directory) / "config.json"
            input_path.write_bytes(b"video")
            config_path.write_text(
                json.dumps(
                    {
                        "input_path": str(input_path),
                        "tool_type": "upscale",
                        "resolution": "2k",
                        "codec": "libx265",
                        "fps": "original",
                        "ai_model": "faces",
                        "denoise": True,
                    }
                ),
                encoding="utf-8",
            )

            settings = run_upscale.read_settings(config_path)

            self.assertIsNone(settings["fps"])
            self.assertEqual(settings["resolution"], "2k")
            self.assertEqual(settings["ai_model"], "faces")
            self.assertTrue(settings["denoise"])

    def test_original_dimensions_follow_source_orientation(self):
        self.assertEqual(run_upscale.get_target_dimensions("4k", 1920, 1080), (3840, 2160))
        self.assertEqual(run_upscale.get_target_dimensions("2k", 1080, 1920), (1440, 2560))
        self.assertEqual(run_upscale.get_target_dimensions("original", 1081, 1921), (1080, 1920))

    def test_motion_blur_accepts_ui_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.mp4"
            config_path = Path(directory) / "config.json"
            input_path.write_bytes(b"video")
            config_path.write_text(
                json.dumps(
                    {
                        "input_path": str(input_path),
                        "tool_type": "motion_blur",
                        "blur_amount": 5,
                        "blur_style": "sports",
                        "interpolation_engine": "rife",
                    }
                ),
                encoding="utf-8",
            )

            settings = run_upscale.read_settings(config_path)

            self.assertEqual(settings["blur_amount"], 5)
            self.assertEqual(settings["blur_style"], "sports")
            self.assertEqual(settings["interpolation_engine"], "rife")

    def test_upscale_filters_only_add_optional_denoise_and_fps(self):
        without_denoise = run_upscale.build_video_filters(
            "original", None, False, 1080, 1920
        )
        with_denoise = run_upscale.build_video_filters(
            "original", 60, True, 1080, 1920
        )

        self.assertNotIn("hqdn3d", without_denoise)
        self.assertNotIn("minterpolate", without_denoise)
        self.assertIn("hqdn3d", with_denoise)
        self.assertIn("minterpolate=fps=60", with_denoise)

    def test_motion_blur_amount_maps_to_expected_frame_count(self):
        filters = []
        run_upscale.apply_motion_blur(filters, 1, "cinematic")
        run_upscale.apply_motion_blur(filters, 5, "sports")

        self.assertIn("tmix=frames=2", filters[0])
        self.assertIn("tmix=frames=5", filters[1])


class Mp4ContainerTests(unittest.TestCase):
    def test_reads_regular_and_extended_top_level_boxes(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "sample.mp4"
            extended_box = (
                (1).to_bytes(4, "big")
                + b"mdat"
                + (16).to_bytes(8, "big")
            )
            input_path.write_bytes(make_box("ftyp") + extended_box)

            boxes = run_upscale.read_mp4_top_level_boxes(input_path)

            self.assertEqual([box[0] for box in boxes], ["ftyp", "mdat"])
            self.assertEqual(boxes[1][2], 16)

    def test_rejects_malformed_box_size(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "broken.mp4"
            input_path.write_bytes((100).to_bytes(4, "big") + b"mdat")

            with self.assertRaisesRegex(ValueError, "حجم صندوق MP4 غير صالح"):
                run_upscale.read_mp4_top_level_boxes(input_path)

    def test_hevc_remux_uses_stream_copy_and_hvc1_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "source.mp4"
            output_path = Path(directory) / "output.mp4"
            input_path.write_bytes(make_box("ftyp") + make_box("moov") + make_box("mdat"))
            command_holder = {}

            def fake_run(command, check):
                command_holder["command"] = command
                Path(command[-1]).write_bytes(b"remuxed")

            with (
                patch.object(run_upscale.shutil, "which", return_value="ffmpeg"),
                patch.object(run_upscale.subprocess, "run", side_effect=fake_run),
                patch.object(
                    run_upscale,
                    "probe_video",
                    return_value={"codec": "hevc", "codec_tag": "hvc1"},
                ),
            ):
                run_upscale.remux_hevc_mp4(input_path, output_path)

            command = command_holder["command"]
            self.assertIn("-c", command)
            self.assertEqual(command[command.index("-c") + 1], "copy")
            self.assertEqual(command[command.index("-tag:v") + 1], "hvc1")
            self.assertTrue(output_path.is_file())


if __name__ == "__main__":
    unittest.main()
