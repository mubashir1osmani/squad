"""Train, save, evaluate and replay Keras/PyTorch multi-horizon forecasts."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import platform
import time
from typing import cast

import numpy as np
import pandas as pd

from .data import integer_column, read_log, sha256
from .forecast_data import (
    Arrays,
    ForecastBatch,
    ForecastConfig,
    load_forecasts,
    make_inputs,
    read_config,
)
from .new_model import build_model, keras, torch


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def fit_scaling(batch: ForecastBatch) -> Arrays:
    scaling: Arrays = {}
    for name, values in batch.inputs.items():
        scaling[f"{name}_mean"] = values.mean(axis=0)
        scaling[f"{name}_scale"] = np.maximum(values.std(axis=0), 1e-3)
        scaling[f"{name}_min"] = values.min(axis=0)
        scaling[f"{name}_max"] = values.max(axis=0)
    for name, values in batch.targets.items():
        residual = values - batch.baseline[name]
        # One physical-unit scale per output head, equal for axes and horizons.
        # This preserves the zero-residual persistence/constant-velocity baseline.
        scaling[f"{name}_target_scale"] = np.asarray(
            max(float(np.sqrt(np.mean(residual**2))), 1e-3), dtype=np.float32
        )
    return scaling


def normalize(inputs: Arrays, scaling: Arrays) -> Arrays:
    return {
        name: (value - scaling[f"{name}_mean"]) / scaling[f"{name}_scale"]
        for name, value in inputs.items()
    }


def residual_targets(batch: ForecastBatch, scaling: Arrays) -> Arrays:
    return {
        name: (value - batch.baseline[name]) / scaling[f"{name}_target_scale"]
        for name, value in batch.targets.items()
    }


def predict_arrays(
    model: keras.Model, inputs: Arrays, baseline: Arrays, scaling: Arrays
) -> Arrays:
    residuals = cast(
        Arrays, model.predict(normalize(inputs, scaling), batch_size=1024, verbose=0)
    )
    if not isinstance(residuals, dict) or set(residuals) != set(baseline):
        raise ValueError("Forecast output heads disagree with the physical-unit baseline")
    for name, expected in baseline.items():
        # Exact shape checking prevents a single-step network being silently
        # broadcast across a multi-horizon baseline by NumPy arithmetic.
        if (expected.ndim != 3 or expected.shape[-1] != 3
                or np.asarray(residuals[name]).shape != expected.shape):
            raise ValueError(f"Forecast output shape mismatch: {name}")
    result = {
        name: baseline[name] + residuals[name] * scaling[f"{name}_target_scale"]
        for name in baseline
    }
    if any(not np.isfinite(value).all() for value in result.values()):
        raise ValueError("Nonfinite forecast; do not use this prediction")
    return result


def metrics(targets: Arrays, predictions: Arrays) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, target in targets.items():
        error = predictions[name].astype(np.float64) - target
        absolute = np.abs(error)
        values: dict[str, object] = {
            "units": {"rates": "rad/s", "position": "m", "velocity": "m/s"}[name],
            "mae": float(absolute.mean()),
            "mae_per_horizon_axis": absolute.mean(axis=0).tolist(),
            "rmse_per_horizon_axis": np.sqrt((error**2).mean(axis=0)).tolist(),
            "p95_absolute_per_horizon_axis": np.quantile(
                absolute, 0.95, axis=0
            ).tolist(),
        }
        if name == "position":
            distance = np.linalg.norm(error, axis=2)
            values.update(
                average_displacement_error_m=float(distance.mean()),
                final_displacement_error_m=float(distance[:, -1].mean()),
            )
        result[name] = values
    return result


def score(
    batch: ForecastBatch, predictions: Arrays, config: ForecastConfig
) -> dict[str, object]:
    report: dict[str, object] = {
        "windows": len(batch.timestamp_us),
        "neural_network": metrics(batch.targets, predictions),
        "persistence_constant_velocity": metrics(batch.targets, batch.baseline),
    }
    if "route" in batch.inputs:
        # Compare with perfect tracking of the intended route, not just no motion.
        origin = batch.baseline["position"][:, :1] - (
            batch.baseline["velocity"][:, :1] * config.offsets_us[0] / 1_000_000
        )
        report["planned_route_position"] = metrics(
            {"position": batch.targets["position"]},
            {"position": batch.inputs["route"] + origin},
        )
    report["per_flight"] = {
        str(flight): metrics(
            {
                key: value[batch.flight_id == flight]
                for key, value in batch.targets.items()
            },
            {
                key: value[batch.flight_id == flight]
                for key, value in predictions.items()
            },
        )
        for flight in np.unique(batch.flight_id)
    }
    return report


def train(dataset: Path, output: Path, config_path: Path) -> dict[str, object]:
    if output.exists():
        raise ValueError(f"Refusing to overwrite experiment: {output}")
    config = read_config(config_path)
    data, audit = load_forecasts(dataset, config)
    training, validation = data["train"], data["validation"]
    scaling = fit_scaling(training)
    model = build_model(
        {name: value.shape[1:] for name, value in training.inputs.items()},
        config.output_names,
        config.horizon_steps,
        config.hidden_units,
        config.learning_rate,
        config.seed,
    )
    x, xv = normalize(training.inputs, scaling), normalize(validation.inputs, scaling)
    y, yv = residual_targets(training, scaling), residual_targets(validation, scaling)
    initial_weights = model.get_weights()
    initial_loss = float(
        model.evaluate(
            xv, yv, batch_size=config.batch_size, verbose=0, return_dict=True
        )["loss"]
    )
    started = time.perf_counter()
    history = model.fit(
        x,
        y,
        validation_data=(xv, yv),
        epochs=config.epochs,
        batch_size=config.batch_size,
        shuffle=True,
        verbose=2,
        callbacks=[
            keras.callbacks.TerminateOnNaN(),
            keras.callbacks.EarlyStopping(
                monitor="val_loss",
                patience=config.patience,
                restore_best_weights=True,
            ),
        ],
    )
    losses = np.asarray(history.history["val_loss"], dtype=float)
    if not np.isfinite(losses).all():
        raise ValueError("Training produced nonfinite validation loss")
    best_epoch = int(np.argmin(losses)) + 1
    if initial_loss <= float(losses.min()):
        model.set_weights(initial_weights)
        best_epoch = 0
    report = score(
        validation,
        predict_arrays(model, validation.inputs, validation.baseline, scaling),
        config,
    )
    output.mkdir(parents=True)
    model.save(output / "model.keras")
    np.savez(output / "scaling.npz", **scaling)
    metadata: dict[str, object] = {
        "schema_version": 1,
        "kind": "keras_torch_forecast",
        "config": asdict(config),
        "feature_names": config.window.feature_names,
        "input_shapes": {
            name: list(value.shape[1:]) for name, value in training.inputs.items()
        },
        "output_names": list(config.output_names),
        "offsets_us": config.offsets_us.tolist(),
        "rate_axis_order": ["roll_x", "pitch_y", "yaw_z"],
        "parameter_count": model.count_params(),
        "best_epoch": best_epoch,
        "epochs_run": len(losses),
        "initial_validation_loss": initial_loss,
        "training_seconds": time.perf_counter() - started,
        "dataset_audit": audit,
        "training_windows": len(training.timestamp_us),
        "validation_windows": len(validation.timestamp_us),
        "model_sha256": sha256(output / "model.keras"),
        "scaling_sha256": sha256(output / "scaling.npz"),
        "code_sha256": {
            name: sha256(Path(__file__).parent / name)
            for name in (
                "forecast.py",
                "forecast_data.py",
                "new_model.py",
                "data.py",
            )
        },
        "software": {
            "python": platform.python_version(),
            "keras": keras.__version__,
            "torch": torch.__version__,
            "numpy": np.__version__,
            "backend": keras.backend.backend(),
        },
        "test_evaluated_during_training": False,
        "status": "experimental_observation_only; dataset may already be exposed development data",
    }
    write_json(output / "model.json", metadata)
    write_json(output / "validation.json", report)
    pd.DataFrame(history.history).to_csv(
        output / "training_history.csv", index_label="epoch_zero_based"
    )
    return {
        "output": str(output),
        "parameters": model.count_params(),
        "best_epoch": best_epoch,
        "validation": report,
        "test_evaluated": False,
    }


class ForecastPredictor:
    """Observation-only batch or last-eight-snapshots inference, in physical units."""

    def __init__(self, directory: Path) -> None:
        self.metadata = json.loads((directory / "model.json").read_text())
        if (
            self.metadata.get("schema_version") != 1
            or self.metadata.get("kind") != "keras_torch_forecast"
        ):
            raise ValueError("Unsupported forecast artifact")
        for name, key in (
            ("model.keras", "model_sha256"),
            ("scaling.npz", "scaling_sha256"),
        ):
            if sha256(directory / name) != self.metadata[key]:
                raise ValueError(f"Forecast checksum mismatch: {name}")
        self.config = ForecastConfig(**self.metadata["config"])
        if keras.backend.backend() != "torch":
            raise ValueError("Set KERAS_BACKEND=torch before importing Keras")
        self.model = keras.models.load_model(
            directory / "model.keras", compile=False, safe_mode=True
        )
        with np.load(directory / "scaling.npz", allow_pickle=False) as saved:
            self.scaling: Arrays = {name: saved[name] for name in saved.files}
        for name, value in self.scaling.items():
            if not np.isfinite(value).all() or (
                name.endswith("scale") and np.any(value <= 0)
            ):
                raise ValueError(f"Invalid normalization: {name}")

    def predict_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Return one row per anchor/horizon; invalid anchors are explicitly marked."""
        required = ["timestamp_us", "motor_timestamp_us"]
        if self.metadata["dataset_audit"]["contract"].get("requires_gyro_timestamp"):
            required.append("gyro_timestamp_us")
        frame = frame.copy()
        for name in required:
            if name not in frame:
                raise ValueError(f"Missing required source timestamp: {name}")
            frame[name] = integer_column(frame[name], name)
        if "gyro_timestamp_us" in frame:
            frame["gyro_timestamp_us"] = integer_column(
                frame.gyro_timestamp_us, "gyro_timestamp_us"
            )
        if np.any(np.diff(frame.timestamp_us.to_numpy()) <= 0):
            raise ValueError("Prediction timestamps must increase strictly")
        if len(frame) < 8:
            raise ValueError("Prediction needs eight history snapshots")
        anchors = np.arange(7, len(frame), dtype=np.int64)
        inputs, baseline, valid = make_inputs(frame, anchors, self.config)
        predictions: Arrays = {
            name: np.full(value.shape, np.nan, dtype=np.float32)
            for name, value in baseline.items()
        }
        outside = np.zeros(len(anchors), dtype=bool)
        if valid.any():
            chosen = {key: value[valid] for key, value in inputs.items()}
            result = predict_arrays(
                self.model,
                chosen,
                {k: v[valid] for k, v in baseline.items()},
                self.scaling,
            )
            for name, value in result.items():
                predictions[name][valid] = value
            for name, value in chosen.items():
                out = (value < self.scaling[f"{name}_min"]) | (
                    value > self.scaling[f"{name}_max"]
                )
                outside[valid] |= out.reshape(len(value), -1).any(axis=1)
        steps = self.config.horizon_steps
        times = frame.timestamp_us.to_numpy()[anchors]
        columns: dict[str, object] = {
            "timestamp_us": np.repeat(times, steps),
            "prediction_timestamp_us": (
                times[:, None] + self.config.offsets_us
            ).reshape(-1),
            "prediction_valid": np.repeat(valid, steps),
            "outside_training_range": np.repeat(outside, steps),
        }
        for name, value in predictions.items():
            unit = {"rates": "rad_s", "position": "m", "velocity": "m_s"}[name]
            for axis, axis_name in enumerate("xyz"):
                columns[f"predicted_{name}_{axis_name}_{unit}"] = value[
                    :, :, axis
                ].reshape(-1)
        return pd.DataFrame(columns)


def replay(model: Path, input_path: Path, output: Path) -> dict[str, object]:
    if output.exists():
        raise ValueError(f"Refusing to overwrite replay: {output}")
    predictor = ForecastPredictor(model)
    result = predictor.predict_frame(read_log(input_path))
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False)
    return {
        "output": str(output),
        "forecast_rows": len(result),
        "valid_rows": int(result.prediction_valid.sum()),
        "mode": predictor.config.mode,
    }


def evaluate(
    dataset: Path,
    model: Path,
    split: str,
    output: Path,
    acknowledge_test: bool = False,
) -> dict[str, object]:
    if split == "test" and not acknowledge_test:
        raise ValueError(
            "Final test requires --acknowledge-test after freezing the model"
        )
    if output.exists():
        raise ValueError(f"Refusing to overwrite evaluation: {output}")
    predictor = ForecastPredictor(model)
    data, audit = load_forecasts(dataset, predictor.config, (split,))
    original = predictor.metadata["dataset_audit"]
    if audit["contract"] != original["contract"]:
        raise ValueError("Dataset frame/actuator/plan contract differs from model")
    if audit["manifest_sha256"] != original["manifest_sha256"]:
        groups = {flight["group_id"] for flight in original["flights"]}
        hashes = {flight["sha256"] for flight in original["flights"]}
        for flight in cast(list[dict[str, object]], audit["flights"]):
            if flight["split"] == split and (
                flight["group_id"] in groups or flight["sha256"] in hashes
            ):
                raise ValueError("Evaluation overlaps a model-development recording")
    batch = data[split]
    predictions = predict_arrays(
        predictor.model, batch.inputs, batch.baseline, predictor.scaling
    )
    report = {
        "split": split,
        "offsets_us": predictor.config.offsets_us.tolist(),
        "dataset_audit": audit,
        "metrics": score(batch, predictions, predictor.config),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    return report
