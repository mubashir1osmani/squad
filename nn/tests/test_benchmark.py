from __future__ import annotations

import unittest
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from nn.benchmark import rate_trend, split_flights
from nn.data import WindowConfig
from nn.model import fit_scaling, forward, metrics, normalized, train_mlp
from nn.report import build_development_report


class DevelopmentProtocolTests(unittest.TestCase):
    def test_score_once_and_separate_recordings_for_epoch_selection(self) -> None:
        flights = [f"{family}_20251017_run{run}" for family in ("square", "chirp", "random")
                   for run in range(1, 6 if family == "square" else 5)]
        scored: list[str] = []
        for fold in range(4):
            roles = split_flights(flights, fold)
            for role in ("train", "validation", "test"):
                self.assertEqual({flight.split("_")[0] for flight, value in roles.items() if value == role},
                                 {"square", "chirp", "random"})
            scored.extend(flight for flight, role in roles.items() if role == "test")
        self.assertEqual(sorted(scored), sorted(flights))
        with self.assertRaises(ValueError):
            split_flights(["unknown-flight"], 0)

    def test_rate_trend_matches_constant_acceleration(self) -> None:
        window = WindowConfig(history_steps=8, interval_tolerance_us=0)
        rates = np.arange(8)[:, None] * .01 * np.array([[1., -2., 3.]])
        signals = np.column_stack([rates, np.ones((8, 4)) * .5])
        data = {"x": signals.reshape(1, -1), "current": rates[-1:]}
        np.testing.assert_allclose(rate_trend(data, window), rates[-1:] + [[.05, -.1, .15]])
        with self.assertRaises(ValueError):
            rate_trend(data, WindowConfig(history_steps=8))

    def test_shared_scale_preserves_physical_axis_weight_and_persistence_origin(self) -> None:
        x = np.arange(40, dtype=float).reshape(10, 4)
        y = np.arange(10)[:, None] * np.array([[1., 10., 100.]])
        scaling = fit_scaling(x, y, "shared_zero_center")
        _, scaled = normalized(x, y, scaling)
        np.testing.assert_array_equal(scaling["y_mean"], np.zeros(3))
        self.assertEqual(np.unique(scaling["y_scale"]).size, 1)
        np.testing.assert_allclose(scaled[:, 2], scaled[:, 0] * 100)
        with self.assertRaises(ValueError):
            fit_scaling(x, y, "unknown")

    def test_zero_epoch_can_retain_persistence_without_forcing_bad_update(self) -> None:
        x = np.ones((20, 4))
        train_y, validation_y = np.ones((20, 3)), np.zeros((20, 3))
        config = {"seed": 42, "hidden_units": 4, "epochs": 3, "patience": 2,
                  "batch_size": 5, "learning_rate": .01, "l2": .001, "ridge_penalty": 10.}
        weights, history, epoch = train_mlp(x, train_y, x, validation_y, config,
                                           selection_metric="normalized_mae", zero_output_init=True)
        self.assertEqual(epoch, 0)
        self.assertEqual(history[0]["epoch"], 0)
        np.testing.assert_array_equal(forward(x, weights), np.zeros((20, 3)))

    def test_report_rejects_prediction_export_that_disagrees_with_metrics(self) -> None:
        flights = np.array([f"{family}_20251017_run1" for family in ("chirp", "random", "square")])
        truth = np.zeros((3, 3))
        predicted = np.ones((3, 3)) * .2
        values = metrics(truth, predicted, flights)
        table: dict[str, object] = {"flight_id": flights, "timestamp_us": [10000] * 3}
        for axis in "xyz":
            table[f"truth_{axis}_rad_s"] = [0.] * 3
            for mode in ("persistence", "mlp_original", "mlp_shared"):
                table[f"{mode}_{axis}_rad_s"] = [.2] * 3
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = {"status": "development_cross_validation_on_previously_exposed_data",
                       "windows_scored_once_each": 3,
                       "metrics": {mode: values for mode in ("persistence", "mlp_original", "mlp_shared")}}
            (root / "summary.json").write_text(json.dumps(summary))
            frame = pd.DataFrame(table)
            frame.to_csv(root / "predictions.csv", index=False)
            self.assertTrue(build_development_report(root, root / "valid")["csv_metrics_verified"])
            frame.loc[0, "mlp_shared_x_rad_s"] = 1.
            frame.to_csv(root / "predictions.csv", index=False)
            with self.assertRaisesRegex(ValueError, "differs"):
                build_development_report(root, root / "invalid")


if __name__ == "__main__":
    unittest.main()
