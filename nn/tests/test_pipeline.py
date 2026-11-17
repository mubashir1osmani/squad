import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from nn import fixture, importer, pipeline
from nn.data import GYRO, MOTORS, WindowConfig, load_dataset, make_windows, read_log
from nn.inference import Predictor
from nn.model import fit_scaling, loss_and_gradients, predict_delta


def simple_log(n=80):
    t = np.arange(n, dtype=np.int64) * 10_000
    return pd.DataFrame({"timestamp_us": t, "motor_timestamp_us": t,
                         **{k: np.arange(n) * (i + 1) * .01 for i, k in enumerate(GYRO)},
                         **{k: np.full(n, .4) for k in MOTORS},
                         "gyro_valid": 1, "motor_valid": 1})


class DataTests(unittest.TestCase):
    def test_window_is_causal_and_target_is_correct(self):
        df, config = simple_log(), WindowConfig()
        a = make_windows(df, config)
        self.assertEqual(a["x"].shape, (72, 28))
        np.testing.assert_allclose(a["y"][0], [.05, .1, .15])
        self.assertEqual(a["timestamp_us"][0], 30_000)
        self.assertEqual(a["target_timestamp_us"][0], 80_000)
        df.loc[4:, MOTORS] = .95  # Future motor changes must not enter the first input.
        np.testing.assert_array_equal(a["x"][0], make_windows(df, config)["x"][0])

    def test_invalid_and_future_motor_samples_never_enter_features(self):
        df, config = simple_log(), WindowConfig()
        df.loc[10, "motor_timestamp_us"] += 1
        df.loc[30, "motor_timestamp_us"] -= 20_000
        df.loc[50, "motor_0"] = np.nan
        a = make_windows(df, config)
        self.assertEqual(a["rejected"]["invalid_motor"], 12)
        self.assertTrue(np.isfinite(a["x"]).all())
        # A future motor invalidity does not invalidate an earlier feature window.
        self.assertIn(60_000, a["timestamp_us"])

    def test_gaps_and_cumulative_horizon_error_are_rejected(self):
        config = WindowConfig()
        df = simple_log()
        df.loc[35:, "timestamp_us"] += 100_000
        df.loc[35:, "motor_timestamp_us"] += 100_000
        a = make_windows(df, config)
        self.assertEqual(a["rejected"]["timing"], 8)
        # Every interval can pass while the accumulated target horizon is wrong.
        df = simple_log()
        df["timestamp_us"] = np.arange(len(df)) * 11_000
        df["motor_timestamp_us"] = df.timestamp_us
        self.assertEqual(len(make_windows(df, config)["x"]), 0)

    def test_integer_timestamp_validation_and_config(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "flight.csv"
            df = simple_log()
            df.loc[1, "timestamp_us"] = 0
            df.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "increase strictly"):
                read_log(path)
            df = simple_log()
            df.timestamp_us += 1_700_000_000_000_000
            df.motor_timestamp_us = df.timestamp_us
            df.to_csv(path, index=False)
            self.assertEqual(read_log(path).timestamp_us.iloc[1], 1_700_000_000_010_000)
        with self.assertRaises(ValueError):
            WindowConfig(history_steps=1.5)
        with self.assertRaises(ValueError):
            WindowConfig(interval_tolerance_us=10_000)

    def test_split_leakage_duplicate_content_and_checksum(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "fixture"
            fixture.generate(root, samples=100)
            path = root / "dataset.json"
            original = json.loads(path.read_text())
            data, _ = load_dataset(path, WindowConfig())
            self.assertEqual(len(data["train"]["x"]), 8 * 92)
            for kind in ("group", "duplicate", "checksum"):
                manifest = json.loads(json.dumps(original))
                if kind == "group":
                    manifest["flights"][8]["group_id"] = manifest["flights"][0]["group_id"]
                elif kind == "duplicate":
                    manifest["flights"][8].update({k: manifest["flights"][0][k] for k in ("path", "sha256")})
                else:
                    manifest["flights"][0]["sha256"] = "bad"
                path.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    load_dataset(path, WindowConfig())


class ModelTests(unittest.TestCase):
    def test_gradient_matches_finite_differences(self):
        rng = np.random.default_rng(71)
        x, y = rng.normal(size=(5, 4)), rng.normal(size=(5, 3))
        w = {"w1": rng.normal(size=(4, 3)), "b1": rng.normal(size=3),
             "w2": rng.normal(size=(3, 3)), "b2": rng.normal(size=3)}
        _, gradients = loss_and_gradients(x, y, w, .003)
        for key, array in w.items():
            for index in np.ndindex(array.shape):
                original = array[index]
                array[index] = original + 1e-6
                high, _ = loss_and_gradients(x, y, w, .003)
                array[index] = original - 1e-6
                low, _ = loss_and_gradients(x, y, w, .003)
                array[index] = original
                self.assertAlmostEqual(gradients[key][index], (high - low) / 2e-6, places=6)

    def test_scaling_uses_only_supplied_training_samples(self):
        x, y = np.array([[1., 4.], [3., 4.]]), np.array([[1., 2., 3.], [3., 4., 5.]])
        scaling = fit_scaling(x, y)
        np.testing.assert_array_equal(scaling["x_mean"], [2, 4])
        self.assertTrue((scaling["x_scale"] > 0).all())


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.dataset = cls.root / "fixture"
        fixture.generate(cls.dataset, samples=140)
        config = json.loads(pipeline.DEFAULT_CONFIG.read_text())
        config["training"].update({"epochs": 8, "patience": 4})
        cls.config_path = cls.root / "config.json"
        cls.config_path.write_text(json.dumps(config))
        cls.model = cls.root / "model"
        cls.metadata, _ = pipeline.train(cls.dataset / "dataset.json", cls.model, cls.config_path)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_model_save_reload_and_streaming_match_offline_windows(self):
        metadata, arrays, window = pipeline.load_model(self.model)
        self.assertEqual(metadata["parameter_count"], 771)
        self.assertFalse(metadata["test_evaluated_during_training"])
        df = read_log(self.dataset / "fixture_08.csv")
        windows = make_windows(df, window)
        expected = windows["current"] + predict_delta(windows["x"], arrays)
        predictor = Predictor(self.model)
        results = [predictor.push(s) for s in df.to_dict("records")]
        valid = [r for r in results if r["prediction_valid"]]
        actual = np.array([[r[f"predicted_{k}"] for k in GYRO] for r in valid[:len(expected)]])
        np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-7)
        self.assertEqual(valid[0]["prediction_timestamp_us"], 80_000)

    def test_stream_resets_on_gap_invalidity_out_of_order_and_silence(self):
        samples = simple_log().to_dict("records")
        p = Predictor(self.model)
        for s in samples[:4]:
            last = p.push(s)
        self.assertTrue(last["prediction_valid"])
        self.assertEqual(p.push(samples[3])["status"], "out_of_order")
        self.assertEqual(len(p.history), 0)
        self.assertEqual(p.push(samples[10])["status"], "timing_reset")
        invalid = dict(samples[11], motor_0=float("nan"))
        self.assertFalse(p.push(invalid)["prediction_valid"])
        for s in samples[12:16]:
            last = p.push(s)
        self.assertTrue(last["prediction_valid"])
        self.assertFalse(p.check_timeout(200_000))
        self.assertEqual(len(p.history), 0)
        self.assertEqual(p.push(samples[21])["status"], "warmup")

    def test_evaluation_requires_explicit_final_test_and_refuses_overwrite(self):
        report_path = self.root / "test.json"
        with self.assertRaisesRegex(ValueError, "acknowledge"):
            pipeline.evaluate(self.dataset / "dataset.json", self.model, "test", report_path)
        report = pipeline.evaluate(self.dataset / "dataset.json", self.model, "test", report_path, True)
        self.assertEqual(report["windows"], 2 * 132)
        with self.assertRaisesRegex(ValueError, "overwrite"):
            pipeline.evaluate(self.dataset / "dataset.json", self.model, "test", report_path, True)
        with self.assertRaisesRegex(ValueError, "overwrite"):
            pipeline.train(self.dataset / "dataset.json", self.model, self.config_path)

    def test_checksum_rejects_changed_weights(self):
        copied = self.root / "bad-model"
        copied.mkdir()
        (copied / "model.json").write_text((self.model / "model.json").read_text())
        (copied / "weights.npz").write_bytes(b"not the original model")
        with self.assertRaisesRegex(ValueError, "checksum"):
            pipeline.load_model(copied)

    def test_native_contract_requires_fresh_gyro_source_timestamp(self):
        predictor = Predictor(self.model)
        predictor.metadata["contract"]["requires_gyro_timestamp"] = True
        samples = simple_log().to_dict("records")
        self.assertFalse(predictor.push(samples[0])["prediction_valid"])
        for sample in samples[1:5]:
            sample["gyro_timestamp_us"] = sample["timestamp_us"]
            result = predictor.push(sample)
        self.assertTrue(result["prediction_valid"])
        stale = dict(samples[5], gyro_timestamp_us=samples[5]["timestamp_us"] - 20_000)
        self.assertFalse(predictor.push(stale)["prediction_valid"])
        future = dict(samples[6], gyro_timestamp_us=samples[6]["timestamp_us"] + 1)
        self.assertFalse(predictor.push(future)["prediction_valid"])


class ImportTests(unittest.TestCase):
    def test_import_assemble_and_prepare_complete_flight_splits(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            raw = root / "raw"
            fixture.generate(raw, samples=100)
            mapping = Path(__file__).parents[1] / "configs/import-example.json"
            runs = []
            for index, split in zip((0, 8, 10), ("train", "validation", "test")):
                source = raw / f"fixture_{index:02d}.csv"
                dest = root / f"run{index}"
                importer.import_csv(source, source, mapping, dest, f"flight{index}",
                                    f"group{index}", split, "px4_sitl")
                runs.append(dest / "run.json")
            # These are adapter test inputs, not evidence of actual PX4 collection.
            importer.assemble(runs, root / "assembled")
            data, audit = load_dataset(root / "assembled/dataset.json", WindowConfig())
            self.assertEqual([len(data[s]["x"]) for s in ("train", "validation", "test")], [92] * 3)
            self.assertEqual(len(audit["flights"]), 3)
            bad = json.loads(runs[-1].read_text())
            bad["dataset_entry"]["group_id"] = "group0"
            runs[-1].write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError, "group"):
                importer.assemble(runs, root / "leaky")

    def test_nanosecond_timestamps_retain_precision(self):
        values = ["1700000000000000123", "1700000000000010123"]
        np.testing.assert_array_equal(importer.timestamps(values, "ns"),
                                      [1700000000000000, 1700000000000010])
        with self.assertRaises(ValueError):
            importer.timestamps(["1", "2"], "ns")  # Rounded duplicate microseconds.

    def test_import_uses_past_actuators_and_explicit_frames(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            imu = pd.DataFrame({"t": [0, 10_000, 20_000, 30_000], "x": 180., "y": 90., "z": 0.})
            motors = pd.DataFrame({"t": [5_000, 25_000], **{k: [1000, 2000] for k in MOTORS}})
            imu.to_csv(root / "imu.csv", index=False)
            motors.to_csv(root / "motors.csv", index=False)
            mapping = json.loads((Path(__file__).parents[1] / "configs/import-example.json").read_text())
            mapping.update({"imu_timestamp_column": "t", "motor_timestamp_column": "t",
                            "gyro_columns": ["x", "y", "z"], "gyro_units": "deg/s",
                            "gyro_valid_column": None, "motor_valid_column": None,
                            "imu_to_frd": [[1, 0, 0], [0, -1, 0], [0, 0, -1]],
                            "actuator_kind": "normalized_pwm"})
            for channel in mapping["motor_channels"]:
                channel.update({"minimum": 1000, "maximum": 2000})
            mp = root / "mapping.json"
            mp.write_text(json.dumps(mapping))
            importer.import_csv(root / "imu.csv", root / "motors.csv", mp, root / "run",
                                "flight01", "flight01", "train", "real_flight")
            result = read_log(root / "run/flight.csv")
            np.testing.assert_array_equal(result.motor_valid, [0, 1, 1, 1])
            np.testing.assert_array_equal(result.motor_timestamp_us, [0, 5000, 5000, 25000])
            np.testing.assert_allclose(result.gyro_x_rad_s, np.pi)
            np.testing.assert_allclose(result.gyro_y_rad_s, -np.pi / 2)
            self.assertEqual(result.motor_0.iloc[2], 0.)  # Not the future 25 ms motor value.
            mapping["imu_to_frd"] = [[1, 0, 0], [0, -1, 0], [0, 0, 1]]
            mp.write_text(json.dumps(mapping))
            with self.assertRaisesRegex(ValueError, "rotation"):
                importer.import_csv(root / "imu.csv", root / "motors.csv", mp, root / "bad",
                                    "flight02", "flight02", "test", "real_flight")

if __name__ == "__main__":
    unittest.main()
