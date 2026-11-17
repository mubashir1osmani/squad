# Neural prediction telemetry v1

The Python `nn` component emits UTF-8 JSON Lines. The Rust ground station
validates and records those packets and displays them without sending aircraft
commands. This interface is independent of the optional manual-control link.

## Packet

```json
{
  "schema_version": 1,
  "model_id": "example-trajectory-model",
  "model_mode": "trajectory",
  "source_kind": "software_fixture",
  "position_frame": "NED",
  "rates_frame": "FRD",
  "timestamp_us": 100000,
  "prediction_valid": true,
  "outside_training_range": false,
  "reason": "ok",
  "observed_position_m": [1.0, 2.0, -5.0],
  "planned_path": [
    {"target_timestamp_us": 150000, "position_m": [1.1, 2.0, -5.0]}
  ],
  "predictions": [
    {
      "target_timestamp_us": 150000,
      "rates_rad_s": [0.01, 0.02, 0.0],
      "position_m": [1.08, 2.01, -5.0],
      "velocity_m_s": [1.6, 0.2, 0.0]
    }
  ]
}
```

- `model_mode` is `rates` or `trajectory`. `source_kind` is `real_flight`,
  `px4_sitl`, `software_fixture`, or `unknown`. The source label must stay visible.
- IDs are nonempty strings of at most 128 characters. Every numeric value is
  finite. Timestamps are nonnegative integer microseconds in the input log's
  source clock, never host receipt time. Packet anchors strictly increase.
- Position is north/east/down in metres. Rates are body forward/right/down in
  radians per second. Top-down plots use east horizontally and north vertically;
  altitude is negative down. Never turn angular-rate predictions into a position
  path without an actual position-output model.
- A valid packet has 1..128 prediction points. Their target timestamps strictly
  increase and are after the anchor. A valid `trajectory` packet includes
  observed position, predicted position and velocity, and one planned position
  at each matching target time. These planned positions are inputs known at the
  anchor, not future measured positions relabelled as a plan.
- Valid `rates` packets have no observed position, an empty `planned_path`, and
  null `position_m`/`velocity_m_s` on every prediction. Their display explicitly
  says that this model does not predict a flight path.
- Invalid packets have a nonempty explanatory `reason`, null observed position,
  and empty predictions/planned path. Preserve invalid packets in the capture;
  clear the current predicted path instead of keeping an old path looking live.
- `outside_training_range` is a warning flag, not a confidence probability.
- A stream keeps model ID/mode, source kind and coordinate frames fixed. Reject
  malformed or contradictory packets and oversized input lines (maximum 1 MiB).

## Meaning of a displayed route

The planned route is an input from mission planning. The neural trajectory is a
forecast of likely future motion conditional on that route and current state.
It does not select an obstacle-free route or authorize autonomous flight.

The existing saved IDSIA model predicts rates only. The trajectory architecture
needs position, velocity, attitude and a causally available future plan. Its
software demo must be labelled synthetic; it does not establish real-flight
trajectory accuracy, PX4 integration, HLS synthesis, or FPGA execution.

## Capture and display

The ground station supports file/stdin ingestion and saves validated packets.
The dashboard shows the intended route and predicted motion separately, with
observed anchor positions, rate values, timestamps, validity and source labels.
Offline replay is labelled replay. Any live HTTP listener is explicitly enabled
and loopback-only; stale or disconnected live sources lose their current path.
No prediction telemetry is connected to arming, takeoff or motor commands.
