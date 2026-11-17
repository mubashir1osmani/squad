"""Causal multi-horizon windows for rates and route-conditioned motion."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Literal, cast

import numpy as np
from numpy.typing import NDArray
import pandas as pd

from .data import (
    SIGNALS,
    WindowConfig,
    integer_column,
    load_dataset,
    read_log,
    row_validity,
)

FloatArray = NDArray[np.float32]
Arrays = dict[str, FloatArray]
POSITION = [f"position_{axis}_m" for axis in "xyz"]
VELOCITY = [f"velocity_{axis}_m_s" for axis in "xyz"]
QUATERNION = [f"attitude_{axis}" for axis in "wxyz"]
STATE = POSITION + VELOCITY + QUATERNION


@dataclass(frozen=True)
class ForecastConfig:
    mode: Literal["rates", "trajectory"] = "trajectory"
    sample_period_us: int = 10_000
    interval_tolerance_us: int = 0
    forecast_stride: int = 5
    horizon_steps: int = 10
    max_source_age_us: int = 15_000
    max_plan_age_us: int = 100_000
    hidden_units: int = 64
    epochs: int = 50
    patience: int = 8
    batch_size: int = 256
    learning_rate: float = 0.001
    seed: int = 42

    def __post_init__(self) -> None:
        if self.mode not in ("rates", "trajectory"):
            raise ValueError("Forecast mode must be rates or trajectory")
        for key, value in asdict(self).items():
            if key in ("mode", "learning_rate"):
                continue
            minimum = 0 if key in ("interval_tolerance_us", "seed") else 1
            if type(value) is not int or value < minimum:
                raise ValueError(f"Invalid forecast setting: {key}")
        if not np.isfinite(self.learning_rate) or self.learning_rate <= 0:
            raise ValueError("learning_rate must be finite and positive")
        if self.interval_tolerance_us >= self.sample_period_us:
            raise ValueError("Timing tolerance must be smaller than the sample period")

    @property
    def offsets_us(self) -> NDArray[np.int64]:
        return np.arange(1, self.horizon_steps + 1, dtype=np.int64) * (
            self.forecast_stride * self.sample_period_us
        )

    @property
    def window(self) -> WindowConfig:
        return WindowConfig(
            history_steps=8,
            sample_period_us=self.sample_period_us,
            interval_tolerance_us=self.interval_tolerance_us,
            horizon_steps=self.forecast_stride * self.horizon_steps,
            horizon_tolerance_us=self.interval_tolerance_us,
            max_motor_age_us=self.max_source_age_us,
            max_gyro_age_us=self.max_source_age_us,
        )

    @property
    def output_names(self) -> tuple[str, ...]:
        return ("rates",) if self.mode == "rates" else ("rates", "position", "velocity")


@dataclass(frozen=True)
class ForecastBatch:
    inputs: Arrays
    baseline: Arrays
    targets: Arrays
    timestamp_us: NDArray[np.int64]
    flight_id: NDArray[np.str_]


def read_config(path: Path) -> ForecastConfig:
    return ForecastConfig(**json.loads(path.read_text()))


def plan_columns(config: ForecastConfig) -> list[str]:
    return [
        f"plan_{step}_{axis}_m"
        for step in range(1, config.horizon_steps + 1)
        for axis in "xyz"
    ]


def trajectory_contract(config: ForecastConfig) -> dict[str, object]:
    return {
        "world_frame": "NED",
        "position_units": "m",
        "velocity_units": "m/s",
        "attitude": "body_FRD_to_world_NED_quaternion_wxyz",
        "plan_offsets_us": config.offsets_us.tolist(),
        "plan_semantics": "planned_positions_available_at_anchor_not_future_observations",
    }


def state_validity(frame: pd.DataFrame, config: ForecastConfig) -> NDArray[np.bool_]:
    missing = set(STATE + ["state_timestamp_us", "state_valid"]) - set(frame.columns)
    if missing:
        raise ValueError(f"Trajectory data missing state columns: {sorted(missing)}")
    stamps = integer_column(frame["state_timestamp_us"], "state_timestamp_us")
    age = frame.timestamp_us.to_numpy() - stamps
    state = frame[STATE].to_numpy(dtype=np.float64)
    if not frame.state_valid.isin([0, 1]).all():
        raise ValueError("state_valid must contain 0 or 1")
    return (
        (frame.state_valid.to_numpy() == 1)
        & np.isfinite(state).all(axis=1)
        & np.isclose(np.linalg.norm(state[:, 6:10], axis=1), 1.0, atol=0.01, rtol=0)
        & (age >= 0)
        & (age <= config.max_source_age_us)
    )


def make_inputs(
    frame: pd.DataFrame,
    anchors: NDArray[np.int64],
    config: ForecastConfig,
) -> tuple[Arrays, Arrays, NDArray[np.bool_]]:
    """Shared training/inference feature path; reads nothing after each anchor."""
    history_indices = anchors[:, None] + np.arange(-7, 1, dtype=np.int64)
    if np.any(history_indices < 0) or np.any(anchors >= len(frame)):
        raise ValueError("Eight history samples are required for each anchor")
    stamps = frame.timestamp_us.to_numpy()
    gyro_valid, motor_valid = row_validity(frame, config.window)
    history = frame[SIGNALS].to_numpy(dtype=np.float32)[history_indices]
    valid = (gyro_valid & motor_valid)[history_indices].all(axis=1)
    valid &= (
        np.abs(np.diff(stamps[history_indices], axis=1) - config.sample_period_us)
        <= config.interval_tolerance_us
    ).all(axis=1)
    inputs: Arrays = {"history": history.reshape(-1, 56)}
    rates = np.repeat(history[:, -1:, :3], config.horizon_steps, axis=1)
    baseline: Arrays = {"rates": rates}
    if config.mode == "rates":
        return inputs, baseline, valid
    valid &= state_validity(frame, config)[anchors]
    columns = plan_columns(config)
    missing = set(columns + ["plan_timestamp_us", "plan_valid"]) - set(frame.columns)
    if missing:
        raise ValueError(f"Trajectory data missing plan columns: {sorted(missing)}")
    issued = integer_column(frame.plan_timestamp_us, "plan_timestamp_us")[anchors]
    age = stamps[anchors] - issued
    if not frame.plan_valid.isin([0, 1]).all():
        raise ValueError("plan_valid must contain 0 or 1")
    plan = (
        frame[columns]
        .to_numpy(dtype=np.float32)[anchors]
        .reshape(-1, config.horizon_steps, 3)
    )
    valid &= (
        (frame.plan_valid.to_numpy()[anchors] == 1)
        & (age >= 0)
        & (age <= config.max_plan_age_us)
    )
    valid &= np.isfinite(plan).all(axis=(1, 2))
    state = frame[STATE].to_numpy(dtype=np.float32)[anchors]
    # Translation-invariant inputs: the current position is the local origin.
    inputs["state"] = state[:, 3:].copy()  # world velocity + body-to-world quaternion
    norm = np.linalg.norm(inputs["state"][:, 3:], axis=1, keepdims=True)
    inputs["state"][:, 3:] /= np.maximum(norm, 1e-8)
    inputs["state"][:, 3:] *= np.where(inputs["state"][:, 3:4] < 0, -1.0, 1.0)
    inputs["route"] = plan - state[:, None, :3]
    dt = config.offsets_us.astype(np.float32)[None, :, None] / 1_000_000
    baseline["position"] = state[:, None, :3] + state[:, None, 3:6] * dt
    baseline["velocity"] = np.repeat(state[:, None, 3:6], config.horizon_steps, axis=1)
    return inputs, baseline, valid


def load_forecasts(
    dataset: Path,
    config: ForecastConfig,
    splits: tuple[str, ...] = ("train", "validation"),
) -> tuple[dict[str, ForecastBatch], dict[str, object]]:
    """Reuse flight/hash/group checks and longest-horizon validity from the original loader."""
    manifest = json.loads(dataset.read_text())
    if config.mode == "trajectory" and manifest.get("contract", {}).get(
        "trajectory"
    ) != trajectory_contract(config):
        raise ValueError(
            "Trajectory training requires state + causal planned-route logs and the trajectory contract; "
            "rate-only CSVs cannot train position prediction (see nn/FORECASTING.md)"
        )
    canonical, audit = load_dataset(dataset, config.window, include_splits=splits)
    paths = {
        flight["id"]: dataset.parent / flight["path"] for flight in manifest["flights"]
    }
    result: dict[str, ForecastBatch] = {}
    for split in splits:
        source = canonical[split]
        batches: list[ForecastBatch] = []
        for flight_id in dict.fromkeys(source["flight_id"]):
            frame = read_log(paths[flight_id])
            times = frame.timestamp_us.to_numpy()
            anchors = np.searchsorted(
                times, source["timestamp_us"][source["flight_id"] == flight_id]
            )
            targets_at = (
                anchors[:, None]
                + np.arange(1, config.horizon_steps + 1) * config.forecast_stride
            )
            inputs, baseline, valid = make_inputs(frame, anchors, config)
            valid &= (
                np.abs(times[targets_at] - times[anchors, None] - config.offsets_us)
                <= config.interval_tolerance_us
            ).all(axis=1)
            target_columns = {"rates": SIGNALS[:3]}
            if config.mode == "trajectory":
                state_ok = state_validity(frame, config)
                invalid_prefix = np.concatenate(([0], np.cumsum(~state_ok)))
                valid &= (
                    invalid_prefix[targets_at[:, -1] + 1] == invalid_prefix[anchors]
                )
                target_columns.update(position=POSITION, velocity=VELOCITY)
            targets = {
                name: frame[columns].to_numpy(dtype=np.float32)[targets_at][valid]
                for name, columns in target_columns.items()
            }
            if not valid.any():
                raise ValueError(f"No usable forecast windows: {flight_id}")
            batches.append(
                ForecastBatch(
                    {key: value[valid] for key, value in inputs.items()},
                    {key: value[valid] for key, value in baseline.items()},
                    targets,
                    times[anchors][valid],
                    np.full(int(valid.sum()), str(flight_id)),
                )
            )

        def combine(field: Literal["inputs", "baseline", "targets"]) -> Arrays:
            return {
                key: np.concatenate([getattr(batch, field)[key] for batch in batches])
                for key in getattr(batches[0], field)
            }

        result[split] = ForecastBatch(
            combine("inputs"),
            combine("baseline"),
            combine("targets"),
            np.concatenate([batch.timestamp_us for batch in batches]),
            np.concatenate([batch.flight_id for batch in batches]),
        )
    return result, cast(dict[str, object], audit)
