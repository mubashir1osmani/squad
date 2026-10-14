# Ground station status

Completed on 2026-10-11. Ownership is `groundstation/**` only. No shared-interface change was required.

## Completed

- Cargo package `quad-groundstation` with library modules for CSV validation, deadzone, MAVLink field mapping, freshness, send-rate limiting, CSV scheduling, localhost transport, and telemetry decode.
- `quad-groundstation replay` writes canonical `commands.csv` with LF newlines. Invalid input leaves an existing output file untouched. The replay path does not sleep and does not open a socket.
- `quad-groundstation control` sends MAVLink v2 `MANUAL_CONTROL` only after `--enable-udp`, and only to a numeric loopback destination. Bind must be loopback too. Target system, source ids, rate, and timeout are configurable.
- Mapping follows the shared contract: `x = forward * 1000`, `y = right * 1000`, `z = (up + 1) * 500`, `r = yaw_cw * 1000`, half-away-from-zero rounding, buttons fixed at 0. No arm or takeoff message is constructed.
- Disabled, disconnected, missing, and stale input suppress further frames. Arrival time changes only when a sample is accepted. CSV arrival is the file timestamp, so a late poll does not refresh an old row. Output ticks do not refresh it.
- Optional `gamepad` feature uses gilrs. Mode 2 mapping, 0.08 deadzone, L1 deadman, neutral rising-edge latch, and disconnect handling are implemented and unit-tested without a device.
- Command, event, and telemetry logs. Telemetry records HEARTBEAT and ATTITUDE. Nonfinite attitude is dropped.

## Checks and outcomes

Host toolchain: rustc 1.98.1, cargo 1.98.1. MAVLink crate: `mavlink` 0.19.1, `dialect-common` only. Gamepad crate, feature build only: `gilrs` 0.11.2.

`cargo test --manifest-path groundstation/Cargo.toml`

- library: 43 passed
- CLI integration: 7 passed
- doc tests: 0
- result: pass

`cargo test --manifest-path groundstation/Cargo.toml --features gamepad`

- library: 44 passed, including `gilrs_initializes_without_requiring_a_connected_controller`
- CLI integration: 7 passed
- result: pass

`cargo clippy --all-targets -- -D warnings` passed for the default build and for `--features gamepad`.

Concrete loopback result from `transport::localhost_exchange_decodes_fields_and_disabled_input_sends_nothing` and `tests/cli.rs::control_loopback_decodes_manual_control_and_then_suppresses`:

- frame magic `0xFD`, payload length 11, msgid 69
- decoded fields for `examples/loopback.csv`: `x=250`, `y=-500`, `z=500`, `r=500`, `buttons=0`, `target=1`
- header source system 255, component 190, sequence 0 on the first frame
- a disabled sample produces no second datagram
- the CLI event log contains `sent,target=1;x=250;y=-500;z=500;r=500;buttons=0` and `reason=stale`
- CLI run with `--rate-hz 50 --timeout-ms 40` printed `sent=2 input_samples=1 telemetry_rows=0`
- `--destination 8.8.8.8:14540` is rejected and no socket is opened
- control without `--enable-udp` sends nothing
- a malformed or non-monotonic CSV sends nothing and does not replace an existing replay output
- replay of `examples/commands.csv` wrote the same three rows and produced no datagram on a listening socket

These are host and loopback results. PX4 SITL was not started. No physical controller sequence was recorded.

## Demo commands

```sh
cargo test --manifest-path groundstation/Cargo.toml
cargo test --manifest-path groundstation/Cargo.toml --features gamepad

cargo run --manifest-path groundstation/Cargo.toml --quiet -- replay \
  --input groundstation/examples/commands.csv \
  --output /tmp/commands.csv \
  --events /tmp/gaps.csv

cargo run --manifest-path groundstation/Cargo.toml --quiet -- control \
  --enable-udp \
  --bind 127.0.0.1:0 \
  --destination 127.0.0.1:14580 \
  --input groundstation/examples/loopback.csv \
  --timeout-ms 40 \
  --event-log /tmp/events.csv
```

The last command sends only if something is bound at `127.0.0.1:14580`. The automated test binds its own ephemeral port instead. For an operator-started PX4 SITL offboard link, bind `127.0.0.1:14540` and send to `127.0.0.1:14580`. See `groundstation/README.md`.

Gamepad, feature build only:

```sh
cargo run --manifest-path groundstation/Cargo.toml --features gamepad --quiet -- gamepad \
  --enable-udp \
  --destination 127.0.0.1:14580 \
  --bind 127.0.0.1:14540 \
  --duration-ms 5000
```

## Limitations

- No PX4 SITL process was launched, so mode handling, `COM_RC_IN_MODE`, and real telemetry were not observed. `telemetry_rows=0` in the loopback demo is expected.
- gilrs initialized on this Mac. Stick signs for a DualShock 4 were not measured. The README mapping is an assumption.
- Stopping `MANUAL_CONTROL` depends on the autopilot's own loss behavior. It is not a land command.
- The live CSV loop wakes about once per millisecond while a sample still has authority. That is separate from the 1..=50 Hz send cap.
- Button bits are intentionally unused. L1 is a local deadman.

## Follow-ups

- Operator-started PX4 SITL check of the offboard port, `COM_RC_IN_MODE`, and a manual arm.
- DualShock 4 pass to confirm gilrs axis signs and the L1 latch.
- Confirm the installed PX4 version still decodes `z` as [0, 1000] before any aircraft test.
