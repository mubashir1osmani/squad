"""Keras Functional models trained by PyTorch, not two separate networks."""

from __future__ import annotations

import os
from collections.abc import Mapping

os.environ.setdefault("KERAS_BACKEND", "torch")

import keras
import torch


def build_model(
    input_shapes: Mapping[str, tuple[int, ...]],
    output_names: tuple[str, ...],
    horizon_steps: int,
    hidden_units: int,
    learning_rate: float,
    seed: int,
) -> keras.Model:
    """Predict normalized residual sequences; zero output starts at a baseline."""
    if keras.backend.backend() != "torch":
        raise ValueError("Set KERAS_BACKEND=torch before importing Keras")
    keras.utils.set_random_seed(seed)
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    inputs = {
        name: keras.Input(shape=shape, name=name)
        for name, shape in input_shapes.items()
    }
    branches = []
    for name, value in inputs.items():
        flat = keras.layers.Flatten(name=f"{name}_flatten")(value)
        branches.append(
            keras.layers.Dense(hidden_units, activation="relu", name=f"{name}_encoder")(
                flat
            )
        )
    combined = (
        keras.layers.Concatenate(name="condition_on_route")(branches)
        if len(branches) > 1
        else branches[0]
    )
    if len(branches) > 1:
        combined = keras.layers.Dense(hidden_units, activation="relu", name="fusion")(
            combined
        )
    outputs = {}
    for name in output_names:
        residual = keras.layers.Dense(
            horizon_steps * 3,
            kernel_initializer="zeros",
            bias_initializer="zeros",
            name=f"{name}_residual",
        )(combined)
        outputs[name] = keras.layers.Reshape((horizon_steps, 3), name=name)(residual)
    model = keras.Model(
        inputs=inputs, outputs=outputs, name="flight_response_forecaster"
    )
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=learning_rate),
        loss={name: "mse" for name in output_names},
    )
    return model
