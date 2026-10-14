# quad-groundstation

Rust ground station for the FPGA-assisted quadcopter. It validates normalized manual commands offline and, only when asked, sends MAVLink `MANUAL_CONTROL` to a loopback address.

The default build does not link a gamepad library and does not open a socket. Arming and takeoff stay with the operator. This program never sends `COMMAND_LONG`, arm, or takeoff messages. `MANUAL_CONTROL.buttons` is always 0 so PX4 mode-switch bits stay clear.

## Build and test

From the repository root, with Rust 1.98 or newer:

```sh
cargo test --manifest-path groundstation/Cargo.toml
cargo clippy --manifest-path groundstation/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path groundstation/Cargo.toml --features gamepad
cargo clippy --manifest-path groundstation/Cargo.toml --all-targets --features gamepad -- -D warnings
```

The executable is `groundstation/target/debug/quad-groundstation`. These commands are host tests. They are not PX4 SITL, a physical controller check, HLS synthesis, or an FPGA measurement.

## Replay

`replay` reads `commands.csv`, checks the shared contract, and writes canonical CSV. It does not sleep and it does not open a socket.

```sh
cargo run --manifest-path groundstation/Cargo.toml --quiet -- replay \
  --input groundstation/examples/commands.csv \
  --output /tmp/commands.csv \
  --events /tmp/gaps.csv
```

Required header:

```csv
t_us,forward,right,up,yaw_cw,enabled
```

Rules enforced by the reader:

- UTF-8, LF newlines, no BOM, no quotes, no thousands separators
- `t_us` is a nonnegative integer with no leading zeros, strictly increasing across rows
- axes are plain decimals in [-1, 1]; `NaN`, infinities, and exponents are rejected
- `enabled` is `0` or `1`
- disabled rows stay in the output
- a gap larger than `--timeout-ms` (default 250) is reported in the optional events file and the row is kept

`--deadzone` defaults to 0, so validated axis values are preserved apart from canonical formatting. A value inside the deadzone becomes 0 and the remainder is rescaled onto [-1, 1].

## Stick mapping

Positive axes are forward, right, up, and clockwise yaw as seen from above. Zero is centered stick, not motor-off.

`MANUAL_CONTROL` fields, rounded half away from zero:

| Command | MAVLink field | Conversion |
| --- | --- | --- |
| `forward` | `x` | `forward * 1000` in [-1000, 1000] |
| `right` | `y` | `right * 1000` in [-1000, 1000] |
| `up` | `z` | `(up + 1) * 500` in [0, 1000] |
| `yaw_cw` | `r` | `yaw_cw * 1000` in [-1000, 1000] |

`z` is a stick code. It is not newtons, motor RPM, or a thrust estimate. `INT16_MAX` (32767) is the MAVLink invalid-axis sentinel and is never sent.

This `z` range matches the shared interface and the values PX4 emits in `src/modules/mavlink/streams/MANUAL_CONTROL.hpp`: `z = (throttle + 1) * 500` with internal throttle in [-1, 1]. PX4's `ManualControlSetpoint.msg` says QGroundControl sends throttle in [0, 1000] and the firmware converts that back to [-1, 1]. The generic MAVLink text still describes `z` as [-1000, 1000]; this milestone follows the project contract and the PX4/QGC convention. In Stabilized mode PX4 treats `z` as collective thrust demand. In altitude or position mode the same centered value is zero climb. The flight mode, which this program does not change, decides that interpretation.

References:

- [MAVLink MANUAL_CONTROL](https://mavlink.io/en/messages/common.html#MANUAL_CONTROL)
- [PX4 manual control](https://docs.px4.io/main/en/config/manual_control)
- [PX4 simulation and UDP ports](https://docs.px4.io/main/en/simulation/)

## Localhost control

UDP opens only after `--enable-udp`, and only when both `--bind` and `--destination` are numeric loopback addresses (`127.0.0.0/8` or `::1`). Hostnames are rejected. The default offline path is `replay`.

PX4 SITL's POSIX startup script `ROMFS/px4fmu_common/init.d-posix/px4-rc.mavlink` binds the offboard MAVLink instance on `127.0.0.1:14580` and sends telemetry to `14540`. A matching command, after the operator has started SITL, is:

```sh
cargo run --manifest-path groundstation/Cargo.toml --quiet -- control \
  --enable-udp \
  --bind 127.0.0.1:14540 \
  --destination 127.0.0.1:14580 \
  --input groundstation/examples/loopback.csv \
  --target-system 1 \
  --rate-hz 20 \
  --timeout-ms 250 \
  --command-log /tmp/commands.csv \
  --event-log /tmp/events.csv \
  --telemetry-log /tmp/telemetry.csv
```

Before those messages can move a vehicle, the operator configures PX4. `COM_RC_IN_MODE` must accept MAVLink manual control (1 is MAVLink only; 0 is RC only and 4 disables manual control). The operator also selects a mode that consumes stick input and arms the vehicle. This program does not set parameters, switch modes, arm, or take off.

The send rate is 1 to 50 Hz, default 20. The interval is integer microseconds `1_000_000 / rate_hz`. Input older than the timeout is not sent. Age equal to the timeout is still fresh. The arrival timestamp changes only when a new sample is accepted. Output ticks do not refresh it. For a CSV session, arrival is the sample's `t_us` on the session clock, so a late poll does not treat an old row as new.

Stopping the stream is not a landing command. PX4's own manual-control loss behavior has to be configured separately.

Our frames use source system 255 and component 190 (GCS). `--target-system` is `MANUAL_CONTROL.target`, default 1. Target 0 is rejected.

`examples/loopback.csv` is one fresh sample: forward 0.25, right -0.5, up 0, yaw 0.5, enabled 1. The mapped frame is `x=250`, `y=-500`, `z=500`, `r=500`, `buttons=0`.

### Logs

Command log columns are the shared command schema. Disabled samples are retained.

Event log:

```csv
t_mono_us,kind,detail
```

`detail` uses semicolons. `sent` records the mapped integers. `suppressed` uses `reason=no_input`, `reason=disconnected`, `reason=disabled`, or `reason=stale`.

Telemetry log, for messages this milestone understands:

```csv
t_mono_us,kind,msgid,sysid,compid,f1,f2,f3,f4,f5,f6,f7
```

- `heartbeat`: type, autopilot, base_mode, custom_mode, system_status, mavlink_version, unused 0
- `attitude`: time_boot_ms, roll, pitch, yaw, rollspeed, pitchspeed, yawspeed

Nonfinite attitude rows are dropped. Other messages increment an ignored count. No PX4 telemetry was received in the host tests because SITL was not running.

Replay gap reports use a different header, `t_us,kind,detail`, because those timestamps come from the file.

## Gamepad

Rebuild with the optional feature:

```sh
cargo build --manifest-path groundstation/Cargo.toml --features gamepad
cargo run --manifest-path groundstation/Cargo.toml --features gamepad --quiet -- gamepad \
  --enable-udp \
  --bind 127.0.0.1:14540 \
  --destination 127.0.0.1:14580 \
  --duration-ms 5000 \
  --command-log /tmp/commands.csv \
  --event-log /tmp/events.csv
```

The default build's `gamepad` subcommand exits with an error and does not open a socket. Omit `--duration-ms` to run until Ctrl-C.

Assumed Mode 2 mapping for a gilrs/SDL gamepad. These signs were not checked on a DualShock 4:

| Physical control | Command |
| --- | --- |
| Right stick Y, up positive | `forward` |
| Right stick X, right positive | `right` |
| Left stick Y, up positive | `up` |
| Left stick X, right positive | `yaw_cw` |
| L1 (`gilrs::Button::LeftTrigger`) | deadman |

The deadzone defaults to 0.08. Readings outside [-1, 1] are clamped, then the deadzone is rescaled. Neutral means a gilrs axis at 0; there is no calibration capture. L1 enables only on a rising edge that starts with every stick inside the deadzone. After a disconnect, L1 must be released before it can enable again. The pad is sampled on `--poll-ms` (default 20). That poll is separate from the send loop. If polls stop, the last sample goes stale and send stops. A disconnected pad does not keep the previous motion command.

L1 is local authority only. It is not copied into MAVLink button bits.

## What these checks are

| Check | What it shows |
| --- | --- |
| `cargo test` | CSV failures, axis mapping, freshness, rate limit, and a localhost UDP round trip |
| `cargo test --features gamepad` | The same tests, plus gilrs context startup. A connected pad is not required |
| `control` against `127.0.0.1` | This process can emit and decode `MANUAL_CONTROL` |
| PX4 SITL | Not run |
| Physical PS4 controller | Not run |
