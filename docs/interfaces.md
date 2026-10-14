# Shared interfaces v1

These contracts are coordinator-owned. Use UTF-8, LF newlines, a header row, period decimal separators, and no thousands separators. CSV fixtures have unquoted numeric fields only. Reject nonfinite values. Timestamps are nonnegative integer microseconds relative to the start of one run, not wall-clock time. Require strictly increasing timestamps where a stream contains multiple samples. Schemas are intentionally independent of any selected FPGA or camera.

## Normalized manual commands

Filename convention: commands.csv

```csv
t_us,forward,right,up,yaw_cw,enabled
0,0,0,0,0,0
20000,0.2,0,0,0,1
40000,0,0,0,0,0
```

Axes are finite values in [-1,1]. Positive values mean forward, right, upward, and clockwise yaw as seen from above. Zero is centered input, not motor-off. enabled is 0 or 1 and represents operator enable/deadman input. A disabled sample has no authority to send motion commands; retain it in logs. A missing/stale/disconnected input must not hold the previous motion command indefinitely.

Groundstation maps enabled inputs to MAVLink MANUAL_CONTROL for an operator-configured PX4 SITL instance: x=forward*1000, y=right*1000, z=(up+1)*500, r=yaw_cw*1000, rounded and range-checked. Explain the requirement for compatible PX4 mode/configuration and verify this mapping against official documentation. Never infer physical thrust from this schema. No arming commands. Default operation writes/prints data only; UDP control requires an explicit flag and a loopback destination. Input timeout defaults to 250 ms measured with monotonic time; disabling/timeout suppresses further MANUAL_CONTROL messages and records the event. This suppression relies on separately configured autopilot loss handling; it is not itself a landing maneuver.

## Image sequences

Portable graymap P5 binary images, 8-bit pixels, maxval=255. Generate and accept sensible PGM whitespace/comments. Sequence manifest:

```csv
t_us,filename
0,frame_000.pgm
20000,frame_001.pgm
```

Filenames resolve relative to the manifest's directory; reject absolute or parent-traversal paths from external manifests. Camera coordinates: +x is image right, +y is image down. Equal dimensions are required for a pair. The vision CLI reads a manifest and emits flow.csv.

## Optical flow results and synthetic truth

```csv
t_us,dt_us,dx_px,dy_px,quality,valid
20000,20000,2,-1,255,1
```

t_us is the second frame's time, dt_us is its positive difference from the first. dx_px/dy_px describe the apparent displacement of image content from the previous image to the current one. These are not aircraft velocity, meters, radians, or world position. Quality is an integer [0,255], an algorithm-specific reliability indicator, not a calibrated probability. valid is 0 or 1. Invalid results must have zero displacement and quality=0. Synthetic truth uses the same schema; exact known translations get quality=255, valid=1. Empty/untextured frames produce invalid flow and must not be treated as confidently stationary. Synthetic tests use integer translation within a documented search window.

## Component CLI contracts

- Groundstation executable: quad-groundstation. Support `replay --input PATH --output PATH` for deterministic, offline command validation and processing without wall-time sleeps or network output. Additional live/controller/SITL subcommands may be added.
- Vision executable: quad-vision. Support `--manifest PATH --output PATH`.
- Simulation executable: quad-sim. Provide documented fixture generation and result-evaluation subcommands. The simulation owner chooses the exact arguments and documents them for integration.
- Each component keeps build outputs within its own directory and does not require another component to build.

If a shared change is necessary, send the proposed change to the coordinator. Do not silently diverge.
