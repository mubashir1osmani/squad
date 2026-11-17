"""Source provenance, causal forecast JSONL and reproducible trained demo checks."""

from __future__ import annotations

from dataclasses import asdict
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from nn import fixture
from nn.data import GYRO, read_log, sha256
from nn.forecast import (
    ForecastPredictor,
    fit_scaling,
    predict_arrays,
    train,
    write_json,
)
from nn.forecast_data import (
    POSITION,
    VELOCITY,
    load_forecasts,
    make_inputs,
    plan_columns,
)
from nn.groundstation_demo import ForecastConfig, flight, generate_dataset, run
from nn.groundstation_export import export, packets, validate_model, write_packets
from nn.new_model import build_model

ROOT = Path(__file__).resolve().parents[1]


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(dir=ROOT)
        cls.root = Path(cls.temp.name)
        cls.predictors: dict[str, ForecastPredictor] = {}
        for mode in ("rates", "trajectory"):
            config = ForecastConfig(
                mode=mode, horizon_steps=2, hidden_units=8, epochs=2, patience=1
            )
            data = cls.root / f"data-{mode}"
            if mode == "rates":
                fixture.generate(data, samples=100)
            else:
                generate_dataset(data, config, 100)
            write_json(cls.root / f"config-{mode}.json", asdict(config))
            train(
                data / "dataset.json",
                cls.root / f"model-{mode}",
                cls.root / f"config-{mode}.json",
            )
            cls.predictors[mode] = ForecastPredictor(cls.root / f"model-{mode}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def frame(self, mode: str):
        filename = "fixture_00.csv" if mode == "rates" else "trajectory_00.csv"
        return read_log(self.root / f"data-{mode}" / filename)

    def values(self, mode: str, frame=None, **kwargs):
        return list(
            packets(
                self.predictors[mode],
                self.frame(mode) if frame is None else frame,
                "software_fixture",
                "test-model",
                **kwargs,
            )
        )

    def test_both_modes_exact_schema_and_finite_json(self) -> None:
        for mode in ("rates", "trajectory"):
            with self.subTest(mode=mode):
                values = self.values(mode)
                self.assertEqual(len(values), 100)
                self.assertTrue(
                    all(not value["prediction_valid"] for value in values[:7])
                )
                self.assertTrue(all(value["prediction_valid"] for value in values[7:]))
                stream = io.StringIO()
                self.assertEqual(
                    write_packets(iter(values), stream),
                    {"packets": 100, "valid_packets": 93},
                )
                decoded = [
                    json.loads(
                        line, parse_constant=lambda _: self.fail("nonfinite JSON")
                    )
                    for line in stream.getvalue().splitlines()
                ]
                packet = decoded[20]
                self.assertEqual(
                    set(packet),
                    {
                        "schema_version",
                        "model_id",
                        "model_mode",
                        "source_kind",
                        "position_frame",
                        "rates_frame",
                        "timestamp_us",
                        "prediction_valid",
                        "outside_training_range",
                        "reason",
                        "observed_position_m",
                        "planned_path",
                        "predictions",
                    },
                )
                self.assertEqual(
                    [point["target_timestamp_us"] for point in packet["predictions"]],
                    [250000, 300000],
                )
                for point in packet["predictions"]:
                    self.assertEqual(
                        set(point),
                        {
                            "target_timestamp_us",
                            "rates_rad_s",
                            "position_m",
                            "velocity_m_s",
                        },
                    )
                    self.assertEqual(len(point["rates_rad_s"]), 3)
                    if mode == "rates":
                        self.assertIsNone(point["position_m"])
                        self.assertIsNone(point["velocity_m_s"])
                if mode == "rates":
                    self.assertIsNone(packet["observed_position_m"])
                    self.assertEqual(packet["planned_path"], [])
                else:
                    frame = self.frame(mode)
                    np.testing.assert_allclose(
                        packet["observed_position_m"], frame.iloc[20][POSITION]
                    )
                    np.testing.assert_allclose(
                        [p["position_m"] for p in packet["planned_path"]],
                        frame.iloc[20][plan_columns(self.predictors[mode].config)]
                        .to_numpy()
                        .reshape(2, 3),
                    )
                for packet in decoded[:7]:
                    self.assertIsNone(packet["observed_position_m"])
                    self.assertEqual(packet["predictions"], [])
                    self.assertEqual(packet["planned_path"], [])
                    self.assertTrue(packet["reason"])

    def test_future_observations_and_future_plan_rows_cannot_change_anchor(
        self,
    ) -> None:
        mode = "trajectory"
        frame = self.frame(mode)
        expected = self.values(mode, frame)[20]
        frame.loc[
            21:, GYRO + POSITION + VELOCITY + plan_columns(self.predictors[mode].config)
        ] += 500
        self.assertEqual(self.values(mode, frame)[20], expected)
        # Its intended future route is not simply future measured positions.
        self.assertFalse(
            np.allclose(
                expected["planned_path"][0]["position_m"],
                self.frame(mode).iloc[25][POSITION].to_numpy(float),
            )
        )

    def test_invalid_stale_future_nonfinite_inputs_clear_paths(self) -> None:
        for mode in ("rates", "trajectory"):
            fields = [
                ("motor_timestamp_us", 0),
                ("motor_timestamp_us", 200001),
                ("gyro_valid", 0),
                ("motor_valid", 0),
                ("motor_0", 2.0),
                ("gyro_x_rad_s", np.nan),
                ("motor_1", np.inf),
                ("timestamp_us", 201000),
            ]
            if mode == "trajectory":
                fields += [
                    ("state_timestamp_us", 0),
                    ("state_timestamp_us", 200001),
                    ("plan_timestamp_us", 0),
                    ("plan_timestamp_us", 200001),
                    ("state_valid", 0),
                    ("plan_valid", 0),
                    ("attitude_w", 0),
                    ("position_x_m", np.nan),
                    ("plan_1_x_m", np.inf),
                ]
            for field, value in fields:
                with self.subTest(mode=mode, field=field, value=value):
                    frame = self.frame(mode)
                    frame.loc[20, field] = value
                    packet = self.values(mode, frame)[20]
                    self.assertFalse(packet["prediction_valid"])
                    self.assertEqual(packet["predictions"], [])
                    self.assertEqual(packet["planned_path"], [])
                    self.assertIsNone(packet["observed_position_m"])
                    json.dumps(packet, allow_nan=False)
            self.assertFalse(
                self.values(mode, source_now_us=300000)[20]["prediction_valid"]
            )
            self.assertTrue(
                self.values(mode, source_now_us=200000)[20]["prediction_valid"]
            )
            self.assertFalse(
                self.values(mode, source_now_us=199999)[20]["prediction_valid"]
            )

    def test_chronology_missing_timestamps_and_integer_precision(self) -> None:
        frame = self.frame("trajectory")
        for value in (frame.timestamp_us.iloc[19], -1, 200000.5):
            broken = frame.copy()
            broken["timestamp_us"] = broken.timestamp_us.astype(object)
            broken.loc[20, "timestamp_us"] = value
            with self.assertRaises(ValueError):
                self.values("trajectory", broken)
        with self.assertRaisesRegex(ValueError, "Missing source timestamp"):
            self.values("trajectory", frame.drop(columns="gyro_timestamp_us"))
        shift = 2**53 + 100
        for field in (
            "timestamp_us",
            "motor_timestamp_us",
            "gyro_timestamp_us",
            "state_timestamp_us",
            "plan_timestamp_us",
        ):
            frame[field] += shift
        packet = self.values("trajectory", frame)[20]
        self.assertEqual(packet["timestamp_us"], 200000 + shift)
        self.assertEqual(
            packet["predictions"][0]["target_timestamp_us"], 250000 + shift
        )
        short = self.values("trajectory", frame.iloc[:3])
        self.assertEqual(len(short), 3)
        self.assertFalse(any(p["prediction_valid"] for p in short))
        for field in (
            "timestamp_us",
            "motor_timestamp_us",
            "gyro_timestamp_us",
            "state_timestamp_us",
            "plan_timestamp_us",
        ):
            frame[field] = np.iinfo(np.int64).max - 1_000_000 + np.arange(100) * 10000
        with self.assertRaisesRegex(ValueError, "outside int64"):
            self.values("trajectory", frame)

    def test_invalid_model_identifiers_and_unknown_source_kind_are_rejected(
        self,
    ) -> None:
        for model_id in ("", "   ", "x" * 129):
            with self.assertRaisesRegex(ValueError, "Invalid model ID"):
                list(
                    packets(
                        self.predictors["rates"],
                        self.frame("rates"),
                        "software_fixture",
                        model_id,
                    )
                )
        with self.assertRaisesRegex(ValueError, "Invalid model ID"):
            export(
                self.root / "model-rates",
                self.root / "data-rates/fixture_00.csv",
                "-",
                "software_fixture",
                model_id="",
            )
        with self.assertRaisesRegex(ValueError, "source_kind"):
            list(
                packets(
                    self.predictors["rates"],
                    self.frame("rates"),
                    "mislabelled",
                    "model",
                )
            )

    def test_metadata_checksums_contract_and_model_integrity(self) -> None:
        data = self.root / "data-trajectory"
        source = data / "trajectory_00.csv"
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            output = str(Path(directory) / "export.jsonl")
            result = export(
                self.root / "model-trajectory", source, output, "software_fixture"
            )
            self.assertEqual(result["valid_packets"], 93)
            with self.assertRaisesRegex(ValueError, "overwrite"):
                export(
                    self.root / "model-trajectory", source, output, "software_fixture"
                )
            with self.assertRaisesRegex(ValueError, "contradicts"):
                export(
                    self.root / "model-trajectory",
                    source,
                    str(Path(directory) / "wrong.jsonl"),
                    "real_flight",
                )
            metadata = json.loads((data / "dataset.json").read_text())
            metadata["contract"]["trajectory"]["world_frame"] = "ENU"
            metadata_path = Path(directory) / "source.json"
            # Standalone source metadata declares the exact input hash.
            standalone = {
                "schema_version": 1,
                "source_kind": "software_fixture",
                "input_sha256": sha256(source),
                "contract": metadata["contract"],
            }
            write_json(metadata_path, standalone)
            with self.assertRaisesRegex(ValueError, "NED"):
                export(
                    self.root / "model-trajectory",
                    source,
                    "-",
                    "software_fixture",
                    metadata_path,
                )
            standalone["input_sha256"] = "bad"
            write_json(metadata_path, standalone)
            with self.assertRaisesRegex(ValueError, "checksum"):
                export(
                    self.root / "model-trajectory",
                    source,
                    "-",
                    "software_fixture",
                    metadata_path,
                )
        predictor = self.predictors["rates"]
        original = predictor.metadata["offsets_us"]
        predictor.metadata["offsets_us"] = [1]
        try:
            with self.assertRaisesRegex(ValueError, "metadata"):
                validate_model(predictor)
        finally:
            predictor.metadata["offsets_us"] = original

    def test_stdout_is_jsonl_and_source_clock_pacing(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "nn",
                "groundstation-export",
                "--model",
                str(self.root / "model-rates"),
                "--input",
                str(self.root / "data-rates/fixture_00.csv"),
                "--output",
                "-",
                "--source-kind",
                "software_fixture",
            ],
            capture_output=True,
            text=True,
            cwd=ROOT.parent,
            check=True,
        )
        self.assertEqual(
            len([json.loads(line) for line in result.stdout.splitlines()]), 100
        )
        self.assertEqual(json.loads(result.stderr)["packets"], 100)
        values = self.values("rates")[:3]
        with (
            patch("nn.groundstation_export.time.monotonic", return_value=100.0),
            patch("nn.groundstation_export.time.sleep") as sleep,
        ):
            write_packets(iter(values), io.StringIO(), pace=True, speed=2)
            self.assertEqual(
                [call.args[0] for call in sleep.call_args_list], [0.005, 0.01]
            )
        for speed in (0, -1, np.nan, np.inf):
            with self.assertRaisesRegex(ValueError, "positive"):
                write_packets(iter(values), io.StringIO(), speed=speed)

    def test_checksum_consistent_wrong_network_shape_cannot_broadcast(self) -> None:
        predictor = self.predictors["trajectory"]
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            model_dir = Path(directory) / "malformed"
            shutil.copytree(self.root / "model-trajectory", model_dir)
            wrong = build_model(
                {"history": (56,), "state": (7,), "route": (2, 3)},
                predictor.config.output_names,
                1,
                8,
                0.001,
                42,
            )
            wrong.save(model_dir / "model.keras")
            metadata = json.loads((model_dir / "model.json").read_text())
            metadata["model_sha256"] = sha256(model_dir / "model.keras")
            write_json(model_dir / "model.json", metadata)
            malformed = ForecastPredictor(model_dir)
            with self.assertRaisesRegex(ValueError, "Actual Keras output shapes"):
                validate_model(malformed)
            frame = self.frame("trajectory")
            inputs, baseline, _ = make_inputs(frame, np.array([20]), predictor.config)
            with self.assertRaisesRegex(ValueError, "output shape mismatch"):
                predict_arrays(malformed.model, inputs, baseline, malformed.scaling)

    @unittest.skipUnless(
        (ROOT / "models/keras-rates-v1").is_dir()
        and (ROOT / "data/idsia-native/causal-v1/chirp_20251017_run1.csv").is_file(),
        "Optional IDSIA regression requires local saved weights and flight data",
    )
    def test_saved_idsia_rates_model_emits_no_position(self) -> None:
        predictor = ForecastPredictor(ROOT / "models/keras-rates-v1")
        frame = read_log(ROOT / "data/idsia-native/causal-v1/chirp_20251017_run1.csv")
        anchors = np.arange(7, len(frame), dtype=np.int64)
        valid = make_inputs(frame, anchors, predictor.config)[2]
        anchor = int(anchors[np.flatnonzero(valid)[0]])
        frame = frame.iloc[anchor - 7 : anchor + 1]
        values = list(packets(predictor, frame, "real_flight", "idsia-keras-rates-v1"))
        self.assertTrue(any(value["prediction_valid"] for value in values))
        self.assertTrue(
            all(
                value["observed_position_m"] is None and value["planned_path"] == []
                for value in values
            )
        )
        self.assertTrue(
            all(
                point["position_m"] is None and point["velocity_m_s"] is None
                for value in values
                for point in value["predictions"]
            )
        )


class DemoTests(unittest.TestCase):
    def test_trained_saved_demo_train_only_scaling_and_unexposed_test(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            output = Path(directory) / "demo"
            result = run(output, samples=100, epochs=2)
            self.assertGreater(result["best_epoch"], 0)
            self.assertFalse(result["test_evaluated"])
            predictor = ForecastPredictor(output / "model")
            self.assertTrue((output / "model/model.keras").is_file())
            self.assertTrue(
                any(
                    np.any(weight != 0)
                    for layer in predictor.model.layers
                    if layer.name.endswith("_residual")
                    for weight in layer.get_weights()
                )
            )
            self.assertTrue(
                all(
                    not f.get("loaded", True)
                    for f in predictor.metadata["dataset_audit"]["flights"]
                    if f["split"] == "test"
                )
            )
            data, _ = load_forecasts(output / "data/dataset.json", predictor.config)
            for name, value in fit_scaling(data["train"]).items():
                np.testing.assert_array_equal(value, predictor.scaling[name])
            values = [
                json.loads(line)
                for line in (output / "predictions.jsonl").read_text().splitlines()
            ]
            self.assertTrue(
                all(value["source_kind"] == "software_fixture" for value in values)
            )
            observed = read_log(output / "input.csv")
            np.testing.assert_allclose(
                observed[POSITION], flight(predictor.config, 100, 99)[POSITION]
            )
            self.assertEqual(result["predictions"]["packets"], 100)
            self.assertEqual(result["predictions"]["valid_packets"], 93)
            valid = values[20]
            # Show actual learned corrections, not a renamed constant-velocity baseline.
            baseline = (
                observed.iloc[20][POSITION].to_numpy(float)
                + observed.iloc[20][VELOCITY].to_numpy(float) * 0.05
            )
            self.assertFalse(
                np.allclose(valid["predictions"][0]["position_m"], baseline)
            )


if __name__ == "__main__":
    unittest.main()
