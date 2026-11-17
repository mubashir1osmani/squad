"""Forecast causality, data contracts, training and saved-inference integration."""

from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from nn import fixture
from nn.data import MOTORS, sha256, read_log
from nn.forecast import ForecastPredictor, evaluate, fit_scaling, predict_arrays, train
from nn.forecast_data import (
    POSITION,
    QUATERNION,
    VELOCITY,
    ForecastConfig,
    load_forecasts,
    make_inputs,
    plan_columns,
    trajectory_contract,
)
from nn.new_model import keras

ROOT = Path(__file__).resolve().parents[1]


def add_trajectory_columns(frame: pd.DataFrame, config: ForecastConfig) -> pd.DataFrame:
    """Analytic test values only: not a flight simulator or training evidence."""
    frame = frame.copy()
    seconds = frame.timestamp_us.to_numpy() / 1_000_000
    for axis, name in enumerate(POSITION):
        frame[name] = (axis + 1) * seconds**2
    for axis, name in enumerate(VELOCITY):
        frame[name] = (axis + 1) * 2 * seconds
    frame[QUATERNION] = [1.0, 0.0, 0.0, 0.0]
    frame["state_timestamp_us"] = frame.timestamp_us
    frame["state_valid"] = 1
    frame["plan_timestamp_us"] = frame.timestamp_us
    frame["plan_valid"] = 1
    # A known constant-velocity desired plan; no future measured states are inputs.
    plan = frame[POSITION].to_numpy()[:, None, :] + (
        frame[VELOCITY].to_numpy()[:, None, :]
        * config.offsets_us[None, :, None]
        / 1_000_000
    )
    frame[plan_columns(config)] = plan.reshape(len(frame), -1)
    return frame


def make_dataset(root: Path, config: ForecastConfig) -> Path:
    fixture.generate(root, samples=100)
    path = root / "dataset.json"
    if config.mode == "trajectory":
        manifest = json.loads(path.read_text())
        manifest["contract"]["trajectory"] = trajectory_contract(config)
        for flight in manifest["flights"]:
            csv_path = root / flight["path"]
            add_trajectory_columns(read_log(csv_path), config).to_csv(
                csv_path, index=False
            )
            flight["sha256"] = sha256(csv_path)
        path.write_text(json.dumps(manifest))
    return path


class ForecastDataTests(unittest.TestCase):
    def test_causal_inputs_and_all_horizon_labels(self) -> None:
        config = ForecastConfig(horizon_steps=3)
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = make_dataset(Path(directory) / "data", config)
            data, audit = load_forecasts(path, config)
            batch = data["train"]
            self.assertEqual(batch.inputs["history"].shape[1:], (56,))
            self.assertEqual(batch.inputs["state"].shape[1:], (7,))
            self.assertEqual(batch.inputs["route"].shape[1:], (3, 3))
            self.assertTrue(
                all(
                    not f.get("loaded", True)
                    for f in audit["flights"]
                    if f["split"] == "test"
                )
            )
            frame = read_log(path.parent / "fixture_00.csv")
            anchors = np.array([7], dtype=np.int64)
            inputs, _, valid = make_inputs(frame, anchors, config)
            self.assertTrue(valid[0])
            np.testing.assert_allclose(
                batch.targets["position"][0], frame.loc[[12, 17, 22], POSITION]
            )
            changed = frame.copy()
            changed.loc[8:, MOTORS] = 0.99
            changed.loc[8:, POSITION + VELOCITY + plan_columns(config)] = 500.0
            changed_inputs, _, _ = make_inputs(changed, anchors, config)
            for name in inputs:
                np.testing.assert_array_equal(inputs[name], changed_inputs[name])

    def test_stale_future_plan_state_and_bad_timing_are_rejected(self) -> None:
        config = ForecastConfig(horizon_steps=2)
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = make_dataset(Path(directory) / "data", config)
            original = read_log(path.parent / "fixture_00.csv")
            anchor = np.array([20], dtype=np.int64)
            for field, value in (
                ("plan_timestamp_us", 200_001),
                ("plan_timestamp_us", 0),
                ("state_timestamp_us", 200_001),
                ("state_timestamp_us", 0),
                ("attitude_w", 0.0),
                ("position_x_m", np.nan),
                ("plan_1_x_m", np.nan),
                ("gyro_valid", 0),
                ("timestamp_us", 201_000),
            ):
                with self.subTest(field=field, value=value):
                    frame = original.copy()
                    frame.loc[20, field] = value
                    self.assertFalse(make_inputs(frame, anchor, config)[2][0])

    def test_translation_and_quaternion_sign_invariance(self) -> None:
        config = ForecastConfig(horizon_steps=2)
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = make_dataset(Path(directory) / "data", config)
            frame = read_log(path.parent / "fixture_00.csv")
            anchors = np.array([20], dtype=np.int64)
            original, baseline, _ = make_inputs(frame, anchors, config)
            shifted = frame.copy()
            shifted[POSITION + plan_columns(config)] += 10
            shifted[QUATERNION] *= -1
            changed, shifted_baseline, valid = make_inputs(shifted, anchors, config)
            self.assertTrue(valid[0])
            for name in original:
                np.testing.assert_allclose(original[name], changed[name], atol=2e-6)
            np.testing.assert_allclose(
                shifted_baseline["position"], baseline["position"] + 10
            )

    def test_missing_route_contract_and_invalid_future_state(self) -> None:
        config = ForecastConfig(mode="rates", horizon_steps=2)
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            path = make_dataset(root / "rates", config)
            with self.assertRaisesRegex(ValueError, "state.*planned-route"):
                load_forecasts(path, replace(config, mode="trajectory"))
            config = replace(config, mode="trajectory")
            path = make_dataset(root / "trajectory", config)
            manifest = json.loads(path.read_text())
            flight = manifest["flights"][0]
            csv_path = path.parent / flight["path"]
            frame = read_log(csv_path)
            frame.loc[10, "state_valid"] = 0
            frame.to_csv(csv_path, index=False)
            flight["sha256"] = sha256(csv_path)
            path.write_text(json.dumps(manifest))
            data, _ = load_forecasts(path, config)
            first = data["train"].flight_id == "fixture_00"
            self.assertNotIn(70_000, data["train"].timestamp_us[first])
            self.assertIn(110_000, data["train"].timestamp_us[first])


class ForecastIntegrationTests(unittest.TestCase):
    def test_train_reload_replay_and_evaluation_both_modes(self) -> None:
        self.assertEqual(keras.backend.backend(), "torch")
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            for mode in ("rates", "trajectory"):
                with self.subTest(mode=mode):
                    config = ForecastConfig(
                        mode=mode, horizon_steps=2, hidden_units=8, epochs=2, patience=1
                    )
                    dataset = make_dataset(root / f"data-{mode}", config)
                    config_path = root / f"{mode}.json"
                    config_path.write_text(json.dumps(asdict(config)))
                    output = root / f"model-{mode}"
                    result = train(dataset, output, config_path)
                    self.assertFalse(result["test_evaluated"])
                    predictor = ForecastPredictor(output)
                    data, _ = load_forecasts(dataset, config)
                    batch = data["validation"]
                    expected = predict_arrays(
                        predictor.model, batch.inputs, batch.baseline, predictor.scaling
                    )
                    frame = read_log(dataset.parent / "fixture_08.csv")
                    replay = predictor.predict_frame(frame)
                    selected = replay[
                        replay.timestamp_us.isin(
                            batch.timestamp_us[batch.flight_id == "fixture_08"]
                        )
                    ]
                    count = int((batch.flight_id == "fixture_08").sum())
                    for name, unit in (
                        ("rates", "rad_s"),
                        ("position", "m"),
                        ("velocity", "m_s"),
                    ):
                        if name not in expected:
                            continue
                        columns = [f"predicted_{name}_{axis}_{unit}" for axis in "xyz"]
                        np.testing.assert_allclose(
                            selected[columns],
                            expected[name][:count].reshape(-1, 3),
                            atol=1e-6,
                        )
                    # Last-eight-row inference must equal the corresponding offline anchor.
                    last = predictor.predict_frame(frame.iloc[13:21].copy())
                    np.testing.assert_allclose(
                        last.filter(like="predicted_").to_numpy(),
                        replay[replay.timestamp_us == frame.timestamp_us.iloc[20]]
                        .filter(like="predicted_")
                        .to_numpy(),
                        atol=1e-6,
                    )
                    np.testing.assert_allclose(
                        predictor.scaling["history_mean"],
                        fit_scaling(data["train"])["history_mean"],
                    )
                    with self.assertRaisesRegex(ValueError, "acknowledge-test"):
                        evaluate(dataset, output, "test", root / "test.json")
                    report = evaluate(
                        dataset, output, "validation", root / f"score-{mode}.json"
                    )
                    self.assertEqual(report["split"], "validation")
                    with self.assertRaisesRegex(ValueError, "overwrite"):
                        train(dataset, output, config_path)
                    if mode == "trajectory":
                        frame.loc[20, "plan_timestamp_us"] = (
                            int(frame.timestamp_us.iloc[20]) + 1
                        )
                        invalid = predictor.predict_frame(frame.iloc[13:21])
                        self.assertFalse(invalid.prediction_valid.any())
                    metadata_path = output / "model.json"
                    metadata = json.loads(metadata_path.read_text())
                    metadata["scaling_sha256"] = "incorrect"
                    metadata_path.write_text(json.dumps(metadata))
                    with self.assertRaisesRegex(ValueError, "checksum"):
                        ForecastPredictor(output)


if __name__ == "__main__":
    unittest.main()
