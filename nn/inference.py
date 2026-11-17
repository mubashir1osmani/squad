"""Observation-only Python inference. No vehicle commands or implicit hardware I/O."""
from collections import deque
import time

import numpy as np
import pandas as pd

from .data import GYRO, SIGNALS, read_log
from .model import predict_delta
from .pipeline import load_model


class Predictor:
    def __init__(self, model_dir):
        self.metadata, self.arrays, self.config = load_model(model_dir)
        self.history = deque(maxlen=self.config.history_steps)
        self.last_timestamp = None

    def reset(self):
        self.history.clear()
        self.last_timestamp = None

    def push(self, sample):
        """One canonical snapshot; availability is the caller's responsibility.

        Call check_timeout even when no samples arrive in a live integration.
        This method cannot detect silence without being called.
        """
        result = {"status": "invalid_sample", "prediction_valid": False,
                  "model_id": self.metadata["weights_sha256"][:16],
                  "horizon_us": self.config.horizon_us}
        try:
            t, mt = sample["timestamp_us"], sample["motor_timestamp_us"]
            gt = (sample["gyro_timestamp_us"] if self.metadata["contract"].get("requires_gyro_timestamp")
                  else sample.get("gyro_timestamp_us", t))
            stamps = (t, mt, gt)
            if any(isinstance(value, (bool, np.bool_))
                   or not isinstance(value, (int, np.integer))
                   or value < 0 or value > np.iinfo(np.int64).max for value in stamps):
                raise ValueError("Timestamps must be nonnegative int64 microseconds")
            t, mt = int(t), int(mt)
            values = np.asarray([sample[k] for k in SIGNALS], dtype=float)
            valid = (sample["gyro_valid"] == 1 and sample["motor_valid"] == 1
                     and np.isfinite(values).all() and ((values[3:] >= 0) & (values[3:] <= 1)).all()
                     and 0 <= t - mt <= self.config.max_motor_age_us
                     and 0 <= t - int(gt) <= self.config.max_gyro_age_us)
        except (KeyError, TypeError, ValueError, OverflowError):
            self.history.clear()
            return result
        result["timestamp_us"] = t
        if self.last_timestamp is not None and t <= self.last_timestamp:
            self.history.clear()
            result["status"] = "out_of_order"
            return result
        gap = (self.last_timestamp is not None and
               abs(t - self.last_timestamp - self.config.sample_period_us) > self.config.interval_tolerance_us)
        self.last_timestamp = t
        if not valid:
            self.history.clear()
            return result
        if gap:
            self.history.clear()
        self.history.append(values)
        if len(self.history) < self.config.history_steps:
            result["status"] = "timing_reset" if gap else "warmup"
            return result
        features = np.concatenate(self.history)[None, :]
        future = values[:3] + predict_delta(features, self.arrays)[0]
        if not np.isfinite(future).all():
            self.history.clear()
            return result
        # Range flag is a warning, not a calibrated confidence probability.
        outside = bool(np.any((features[0] < self.arrays["x_min"]) |
                              (features[0] > self.arrays["x_max"])))
        result.update({"status": "ok", "prediction_valid": True,
                       "outside_training_range": outside,
                       "prediction_timestamp_us": t + self.config.horizon_us,
                       **{f"predicted_{k}": float(future[i]) for i, k in enumerate(GYRO)}})
        return result

    def check_timeout(self, source_now_us):
        """Same clock as sample timestamps; invalidate history on silence/clock reset."""
        if not isinstance(source_now_us, (int, np.integer)) or source_now_us < 0:
            raise ValueError("Watchdog needs integer microseconds in the source clock")
        stale = (self.last_timestamp is None or source_now_us < self.last_timestamp or
                 source_now_us - self.last_timestamp >
                 self.config.sample_period_us + self.config.interval_tolerance_us)
        if stale:
            self.reset()
        return not stale


def replay(model_dir, input_path, output_path):
    from pathlib import Path
    if Path(output_path).exists():
        raise ValueError(f"Refusing to overwrite replay: {output_path}")
    predictor = Predictor(model_dir)
    samples = read_log(input_path).to_dict("records")
    results, latency = [], []
    for sample in samples:
        begin = time.perf_counter_ns()
        result = predictor.push(sample)
        elapsed_us = (time.perf_counter_ns() - begin) / 1e3
        if result["prediction_valid"]:
            latency.append(elapsed_us)
        results.append(result)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(results).to_csv(output_path, index=False)
    return {"rows": len(results), "valid_predictions": len(latency),
            "latency_scope": "host CPU Python push(), offline replay; not a real-time or FPGA measurement",
            "latency_us": {"p50": float(np.median(latency)),
                           "p95": float(np.quantile(latency, .95)),
                           "max": float(max(latency))} if latency else None}
