"""Reproducible synthetic route-conditioned training and telemetry demonstration.

This deliberately simple kinematic fixture is neither PX4 nor an aircraft model.
The analytic desired route is defined before rollout and available at every anchor.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .data import GYRO, MOTORS, MOTOR_ORDER, sha256
from .forecast import train, write_json
from .forecast_data import (
    POSITION,
    VELOCITY,
    QUATERNION,
    ForecastConfig,
    plan_columns,
    trajectory_contract,
)
from .groundstation_export import export

WARNING = "Synthetic software fixture only; not PX4, real flight, a dynamics simulator or flight-accuracy evidence."


def desired(seconds: np.ndarray, index: int) -> np.ndarray:
    """A mission schedule known before rollout, independent of measured future motion."""
    phase = index * 0.41
    radius = 3.0 + (index % 3) * 0.25
    angle = 0.6 * seconds + phase
    return np.stack(
        (
            radius * np.sin(angle),
            radius * np.cos(angle),
            -2.0 + 0.3 * np.sin(0.37 * seconds + phase),
        ),
        axis=-1,
    )


def flight(config: ForecastConfig, samples: int, index: int) -> pd.DataFrame:
    stamps = np.arange(samples, dtype=np.int64) * config.sample_period_us
    dt = config.sample_period_us / 1_000_000
    seconds = stamps / 1_000_000
    route = desired(seconds[:, None] + config.offsets_us[None, :] / 1_000_000, index)
    position, velocity = np.zeros((samples, 3)), np.zeros((samples, 3))
    position[0] = desired(np.asarray(0.0), index) + [0.4, -0.3, 0.1]
    acceleration = np.zeros((samples, 3))
    for sample in range(samples - 1):
        # This servo follows the already-known schedule with lag; no future
        # measured position/velocity enters the controller or plan columns.
        setpoint = desired(np.asarray(seconds[sample] + 0.25), index)
        acceleration[sample] = np.clip(
            4.0 * (setpoint - position[sample]) - 2.4 * velocity[sample], -4, 4
        )
        velocity[sample + 1] = velocity[sample] + acceleration[sample] * dt
        position[sample + 1] = position[sample] + velocity[sample + 1] * dt
    acceleration[-1] = acceleration[-2]
    rates = acceleration * [0.03, -0.03, 0.01]
    motors = np.clip(
        0.5
        + acceleration
        @ np.asarray(
            [
                [0.02, -0.02, -0.02, 0.02],
                [0.02, 0.02, -0.02, -0.02],
                [0.01, -0.01, 0.01, -0.01],
            ]
        ),
        0.1,
        0.9,
    )
    frame = pd.DataFrame(
        {
            "timestamp_us": stamps,
            "motor_timestamp_us": stamps,
            "gyro_timestamp_us": stamps,
            "gyro_valid": 1,
            "motor_valid": 1,
            "state_timestamp_us": stamps,
            "state_valid": 1,
            "plan_timestamp_us": stamps,
            "plan_valid": 1,
        }
    )
    frame[GYRO], frame[MOTORS] = rates, motors
    frame[POSITION], frame[VELOCITY] = position, velocity
    frame[QUATERNION] = [1.0, 0.0, 0.0, 0.0]
    frame[plan_columns(config)] = route.reshape(samples, -1)
    return frame


def generate_dataset(
    output: Path, config: ForecastConfig, samples: int
) -> dict[str, Any]:
    if output.exists():
        raise ValueError(f"Refusing to overwrite fixture dataset: {output}")
    if samples < 100:
        raise ValueError("Need at least 100 samples per fixture flight")
    output.mkdir(parents=True)
    contract = {
        "body_frame": "FRD",
        "gyro_units": "rad/s",
        "motor_order": MOTOR_ORDER,
        "actuator_kind": "normalized_output",
        "requires_gyro_timestamp": True,
        "timebase": "synthetic shared monotonic source clock, integer microseconds",
        "trajectory": trajectory_contract(config),
    }
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "name": "trajectory-software-fixture-v1",
        "warning": WARNING,
        "contract": contract,
        "provenance": {
            "generator": "nn.groundstation_demo",
            "samples_per_flight": samples,
            "plan_available": "Entire analytic mission schedule known before rollout; plan never copies measured future positions",
            "split": "Four whole training flights; one validation flight; one untouched test flight",
        },
        "flights": [],
    }
    for index in range(6):
        name = f"trajectory_{index:02d}"
        path = output / f"{name}.csv"
        flight(config, samples, index).to_csv(path, index=False, float_format="%.10g")
        manifest["flights"].append(
            {
                "id": name,
                "group_id": name,
                "path": path.name,
                "split": "train"
                if index < 4
                else "validation"
                if index == 4
                else "test",
                "source_kind": "software_fixture",
                "sha256": sha256(path),
            }
        )
    write_json(output / "dataset.json", manifest)
    return manifest


def run(output: Path, samples: int = 600, epochs: int = 12) -> dict[str, Any]:
    if output.exists():
        raise ValueError(f"Refusing to overwrite demo directory: {output}")
    config = ForecastConfig(
        mode="trajectory",
        horizon_steps=10,
        hidden_units=24,
        epochs=epochs,
        patience=4,
        batch_size=256,
        seed=42,
    )
    output.mkdir(parents=True)
    manifest = generate_dataset(output / "data", config, samples)
    write_json(output / "config.json", asdict(config))
    trained = train(
        output / "data/dataset.json", output / "model", output / "config.json"
    )
    if trained["best_epoch"] == 0:
        raise ValueError(
            "Demo training retained the initial baseline; inspect validation before claiming a trained forecast"
        )
    # An independent new fixture route is shown; no reserved test is evaluated.
    input_path = output / "input.csv"
    flight(config, samples, 99).to_csv(input_path, index=False, float_format="%.10g")
    write_json(
        output / "input.metadata.json",
        {
            "schema_version": 1,
            "source_kind": "software_fixture",
            "input_sha256": sha256(input_path),
            "contract": manifest["contract"],
            "warning": WARNING,
            "provenance": "Independent synthetic route 99, generated from a predeclared mission schedule",
        },
    )
    exported = export(
        output / "model",
        input_path,
        str(output / "predictions.jsonl"),
        "software_fixture",
        output / "input.metadata.json",
        "software-fixture-trajectory-v1",
    )
    result = {
        "warning": WARNING,
        "output": str(output),
        "source_kind": "software_fixture",
        "best_epoch": trained["best_epoch"],
        "test_evaluated": False,
        "input": str(input_path),
        "metadata": str(output / "input.metadata.json"),
        "model": str(output / "model"),
        "predictions": exported,
    }
    write_json(output / "demo.json", result)
    return result
