from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import h5py
import numpy as np
import pandas as pd

from nn import fixture
from nn.data import WindowConfig, load_dataset, make_windows, read_log, sha256
from nn.idsia_import import NativeRun, NativeSettings, causal_grid, read_numeric_topic, receipt_timestamps
from nn.report import display_indices


def settings() -> NativeSettings:
    config = json.loads((Path(__file__).parents[1] / "configs/idsia-v1.json").read_text())
    return config["native_import"]


class NativeImportTests(unittest.TestCase):
    def test_numeric_hdf_read_skips_object_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.h5"
            with h5py.File(path, "w") as store:
                group = store.create_group("cf/image_odom")
                group.create_dataset("block0_items", data=np.array([b"timestamp"]))
                group.create_dataset("block0_values", data=np.array([[1000], [2000]], dtype=np.int64))
                group.create_dataset("block1_items", data=np.array([b"unsafe_object"]))
                group.create_dataset("block1_values", data=np.array([b"not-a-pickle"], dtype=object),
                                     dtype=h5py.special_dtype(vlen=bytes))
            result = read_numeric_topic(path, "/cf/image_odom", ("timestamp",))
            np.testing.assert_array_equal(result["timestamp"], [1000, 2000])
            with self.assertRaisesRegex(ValueError, "Missing numeric"):
                read_numeric_topic(path, "/cf/image_odom", ("unsafe_object",))

    def test_native_timestamps_reject_float_duplicate_and_reversal(self) -> None:
        for stamps in (np.array([1., 2.]), np.array([1, 1]), np.array([2, 1]), np.array([-1, 2])):
            with self.assertRaises(ValueError):
                receipt_timestamps(stamps)

    def test_alignment_is_past_only_before_submicrosecond_rounding(self) -> None:
        origin = 1_700_000_000_000_000_000
        gyro_t = origin + np.array([0, 10_000_500, 20_000_000, 30_000_000], dtype=np.int64)
        motor_t = origin + np.array([0, 10_000_500, 20_000_000, 30_000_000], dtype=np.int64)
        status_t = origin + np.array([0, 30_000_000], dtype=np.int64)
        run = NativeRun(gyro_t, np.array([[1., 2., 3.], [9., 8., 7.], [4., 5., 6.], [1., 2., 3.]]),
                        motor_t, np.array([[100., 200., 300., 400.], [65535., 65535., 65535., 65535.],
                                           [200., 300., 400., 500.], [100., 200., 300., 400.]]),
                        status_t, np.array([16, 0], dtype=np.int64))
        frame, _ = causal_grid(run, settings(), WindowConfig())
        # The 10.0005ms packets are future at 10ms and must not be selected.
        self.assertEqual(frame.gyro_timestamp_us.iloc[1], 0)
        self.assertEqual(frame.motor_timestamp_us.iloc[1], 0)
        np.testing.assert_allclose(frame.loc[1, ["gyro_x_rad_s", "gyro_y_rad_s", "gyro_z_rad_s"]], [1, -2, -3])
        self.assertAlmostEqual(frame.motor_0.iloc[1], 100 / 65535)
        # Flight status is consumed causally; the 30ms not-flying flag invalidates that row.
        self.assertEqual(frame.gyro_valid.tolist(), [1, 1, 1, 0])
        altered = NativeRun(gyro_t, run.gyro_flu_rad_s * np.array([[1], [100], [1], [1]]),
                            motor_t, run.motor_commands, status_t, run.supervisor)
        changed, _ = causal_grid(altered, settings(), WindowConfig())
        pd.testing.assert_frame_equal(frame.iloc[:2], changed.iloc[:2])

    def test_dropouts_are_retained_and_stale_status_is_rejected(self) -> None:
        t = np.array([0, 10_000_000, 100_000_000, 120_000_000], dtype=np.int64)
        run = NativeRun(t, np.ones((4, 3)), t, np.ones((4, 4)) * 30000,
                        np.array([0], dtype=np.int64), np.array([16], dtype=np.int64))
        frame, audit = causal_grid(run, settings(), WindowConfig())
        self.assertEqual(len(frame), 13)
        self.assertEqual(frame.gyro_valid.iloc[4], 0)
        self.assertEqual(frame.motor_valid.iloc[4], 0)
        self.assertEqual(frame.gyro_valid.iloc[-1], 0)  # Flying status older than 100ms.
        self.assertTrue(audit["invalid_rows_retained"])

    def test_window_rejects_stale_and_future_gyro_even_with_valid_flags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "fixture"
            fixture.generate(root, samples=100)
            frame = read_log(root / "fixture_00.csv")
            frame["gyro_timestamp_us"] = frame.timestamp_us
            frame.loc[20, "gyro_timestamp_us"] -= 20_000
            frame.loc[60, "gyro_timestamp_us"] += 1
            windows = make_windows(frame, WindowConfig())
            self.assertEqual(windows["rejected"]["invalid_gyro"], 18)

    def test_development_loader_does_not_read_test_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "fixture"
            fixture.generate(root, samples=100)
            manifest_path = root / "dataset.json"
            manifest = json.loads(manifest_path.read_text())
            for flight in manifest["flights"]:
                if flight["split"] == "test":
                    path = root / flight["path"]
                    path.write_text("This deliberately is not a usable log.\n" + flight["id"])
                    flight["sha256"] = sha256(path)
            manifest_path.write_text(json.dumps(manifest))
            with patch("nn.data.read_log", wraps=read_log) as reader:
                data, audit = load_dataset(manifest_path, WindowConfig(), include_splits=("train", "validation"))
            self.assertEqual(set(data), {"train", "validation"})
            self.assertEqual(reader.call_count, 10)
            self.assertTrue(all(f["rows"] is None for f in audit["flights"] if f["split"] == "test"))
            with self.assertRaises(ValueError):
                load_dataset(manifest_path, WindowConfig())

    def test_plot_sampling_retains_gaps_peaks_and_largest_error(self) -> None:
        times = np.arange(1000, dtype=np.int64) * 10_000
        times[500:] += 100_000
        truth = np.zeros((1000, 3))
        prediction = truth.copy()
        truth[300, 0] = 10
        prediction[750, 1] = -12
        selected = display_indices(times, truth, prediction, 10_000, limit=100)
        self.assertLessEqual(len(selected), 100)
        self.assertTrue({0, 300, 499, 500, 750, 999}.issubset(set(selected.tolist())))
        self.assertTrue(np.all(np.diff(selected) > 0))


if __name__ == "__main__":
    unittest.main()
