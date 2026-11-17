"""Deterministic software fixture. This is NOT PX4 SITL or real flight data."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .data import GYRO, MOTORS, MOTOR_ORDER, sha256


def generate(output, samples=1600, seed=20261111):
    output = Path(output)
    if output.exists():
        raise ValueError(f"Refusing to overwrite dataset directory: {output}")
    if samples < 100:
        raise ValueError("Need at least 100 fixture samples per run")
    output.mkdir(parents=True)
    manifest = {"schema_version": 1, "name": "software-fixture-v1",
                "warning": "Analytic test fixture only; NOT PX4, NOT real flight.",
                "generator": {"module": "nn.fixture", "seed": seed, "samples_per_flight": samples},
                "contract": {"body_frame": "FRD", "gyro_units": "rad/s",
                             "motor_order": MOTOR_ORDER, "actuator_kind": "normalized_output"},
                "flights": []}
    for index in range(12):
        rng = np.random.default_rng(seed + index)
        t = np.arange(samples) * .01
        frequency = rng.uniform(.2, 1.2, (4, 3))
        phase = rng.uniform(0, 2 * np.pi, (4, 3))
        commands = .48 + .10 * np.sin(t[:, None, None] * frequency + phase).sum(axis=2)
        commands = np.clip(commands, .1, .9)
        rates = np.zeros((samples, 3))
        applied = commands[0].copy()
        mixer = np.array([[1, -1, -1, 1], [1, 1, -1, -1], [-1, 1, -1, 1]], float)
        gain = np.array([5., 5.4, 2.8]) * rng.uniform(.97, 1.03, 3)
        for k in range(samples - 1):
            applied += .01 / .06 * (commands[k] - applied)
            torque = (mixer @ (applied ** 2)) * gain
            drag = .7 * rates[k] + .08 * rates[k] ** 3
            rates[k + 1] = rates[k] + .01 * (torque - drag)
        measured = rates + rng.normal(0, .0015, rates.shape)
        df = pd.DataFrame({"timestamp_us": np.arange(samples, dtype=np.int64) * 10_000,
                           "motor_timestamp_us": np.arange(samples, dtype=np.int64) * 10_000,
                           **{name: measured[:, i] for i, name in enumerate(GYRO)},
                           **{name: commands[:, i] for i, name in enumerate(MOTORS)},
                           "gyro_valid": 1, "motor_valid": 1})
        filename = f"fixture_{index:02d}.csv"
        df.to_csv(output / filename, index=False, float_format="%.9g")
        manifest["flights"].append({"id": f"fixture_{index:02d}", "path": filename,
                                    "group_id": f"fixture_run_{index:02d}",
                                    "split": "train" if index < 8 else "validation" if index < 10 else "test",
                                    "source_kind": "software_fixture", "sha256": sha256(output / filename)})
    (output / "dataset.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
