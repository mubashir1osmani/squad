# PX4 SITL setup for this project

Checked against the PX4 Guide (`main`) on 2026-10-11. PX4 changes these pages. Read them again before installing anything.

Status of this workspace: no PX4 tree, no Gazebo install, no Docker image pull, and no SITL process was run. Nothing below is a measured flight, a measured hover, or a measured MAVLink session.

`quad-sim` writes CSV and PGM fixtures and scores optical-flow files. It has no aerodynamic model and no kinematic vehicle model. A `commands.csv` file is a stick log. It does not move a simulated aircraft.

## Three PX4-related runtimes

Use the names PX4 uses in its own guide.

| Runtime | What it is | Official starting point |
| --- | --- | --- |
| Synthetic fixtures | Files from `quad-sim`. Host C++ only | `./simulation/build/quad-sim generate` |
| SIH | PX4's in-process headless simulator. Physics stays inside PX4. No Gazebo | [SIH](https://docs.px4.io/main/en/sim_sih/) and the prebuilt `px4` package |
| Gazebo Harmonic | External 3D simulator. PX4 talks to it through `gz_bridge` | [Gazebo Simulation](https://docs.px4.io/main/en/sim_gazebo_gz/) |

Gazebo Classic is community-supported and is not the default for new work. The current guide tells new projects to use Gazebo (formerly Ignition). Ubuntu 22.04 and newer ship that Gazebo line. Gazebo Harmonic cannot be installed on Ubuntu 20.04.

SIH and Gazebo are both real PX4 SITL stacks. A fixture test does not exercise either one.

Official overview: [Simulation](https://docs.px4.io/main/en/simulation/).

## Do not install this from the fixture milestone

The sizes below are PX4's own approximations from [Pre-built SITL Packages](https://docs.px4.io/main/en/simulation/px4_sitl_prebuilt_packages):

| Image or package | Role | Approximate size |
| --- | --- | --- |
| `px4io/px4-sitl` | SIH container | 100 MB |
| `px4io/px4-sitl-gazebo` | Gazebo Harmonic container | 2 GB |
| `px4` `.deb` | SIH on Ubuntu 22.04 or 24.04 | 10 MB |
| `px4-gazebo` `.deb` | Gazebo Harmonic on Ubuntu 22.04 or 24.04 | 30 MB plus the OSRF Gazebo runtime |

`Tools/setup/ubuntu.sh` and `Tools/setup/macos.sh` install system packages, Python dependencies, and, with `--sim-tools`, a simulator toolchain. They were not run here. A future operator runs them on a machine they choose, outside this repository's test suite.

## Ubuntu source build

Supported host releases, from [Ubuntu Development Environment](https://docs.px4.io/main/en/dev_setup/dev_env_linux_ubuntu):

- Ubuntu 24.04 is the current CI and release target.
- Ubuntu 22.04 is also build-tested.
- Ubuntu 26.04 is not supported yet.
- The setup script is intended for a clean Ubuntu LTS install.

Gazebo Harmonic is part of the normal Ubuntu 22.04 development setup. Ubuntu 24.04 can use Gazebo Harmonic, Ionic, or Jetty, and PX4 uses the newest installed release.

```sh
git clone https://github.com/PX4/PX4-Autopilot.git --recursive
bash ./PX4-Autopilot/Tools/setup/ubuntu.sh
```

`--no-nuttx` and `--no-sim-tools` omit the corresponding pieces. The script expects a restart when it finishes.

The quadrotor target for this airframe, from [Gazebo Simulation](https://docs.px4.io/main/en/sim_gazebo_gz/) and [Building PX4 Software](https://docs.px4.io/main/en/dev_setup/building_px4):

```sh
cd PX4-Autopilot
make px4_sitl gz_x500
```

`gz_x500` is the x500 quadrotor. `PX4_SYS_AUTOSTART` for that model is 4001. The same page lists depth, vision-odometry, lidar, VTOL, plane, and rover targets. Headless Gazebo is `HEADLESS=1 make px4_sitl gz_x500`. A world is selected with `PX4_GZ_WORLD` or a suffix such as `gz_x500_windy`. The default world must be requested as `gz_x500`, not `gz_x500_default`.

The build page shows the operator typing this in the PX4 shell after the simulator is up, and only after a ground-control connection when the default configuration requires one:

```text
pxh> commander takeoff
```

That command is an operator action in the PX4 shell. `quad-sim` and the ground-station milestone do not send arm or takeoff. Landing in that same official example is `commander land`. Stop the process with Ctrl-C or `shutdown`.

If `ninja: error: unknown target 'gz_x500'` appears, PX4's Gazebo page says to run `make distclean` and retry.

## macOS source build

This workspace is macOS. [Setting up a Developer Environment](https://docs.px4.io/main/en/dev_setup/dev_env) lists Gazebo SITL as supported on macOS. ROS 2 with Gazebo is listed for Ubuntu and Windows, and is absent from the macOS column. Gazebo Classic SITL is listed for Windows in that same table.

[macOS Development Environment](https://docs.px4.io/main/en/dev_setup/dev_env_mac) is the procedure to follow:

- It supports NuttX hardware builds and Gazebo Harmonic.
- It runs on Intel and Apple Silicon. PX4 CI exercises the Apple Silicon runners only. Intel is not covered by CI.
- Install the Xcode command-line tools and Homebrew.
- Raise the open-file limit. The macOS default of 256 is too low for the PX4 build (`Too many open files`). The official snippet appends `ulimit -S -n 2048` to `~/.zshrc`. This repository's setup did not change the shell startup files.
- Clone PX4 with submodules, then run `./Tools/setup/macos.sh --sim-tools`.
- The script creates `.venv` in the PX4 tree. Activate it with `source .venv/bin/activate` before building. Python dependencies stay in that virtualenv.
- `--sim-tools` installs Gazebo from a locked conda-forge environment through pixi, into `Tools/setup/macos/.pixi`, not from Homebrew. The [simulator comparison table](https://docs.px4.io/main/en/simulation/) says Gazebo on Apple Silicon "Works (via Homebrew)". The macOS toolchain page is the install procedure, and it disagrees with that wording. Follow the toolchain page.
- The script installs XQuartz. macOS may require a log out and back in the first time.
- Homebrew 6 refuses untrusted taps until `brew trust`. The script prompts for a password when simulation tools are requested.
- Omit `--sim-tools` for a NuttX-only build. `make px4_sitl` can configure without Gazebo. The `gz_*` targets need the simulator packages.
- Smoke check from the same page: `arm-none-eabi-gcc --version`, `cmake --version`, `ninja --version`, and, with the venv active, `gz sim --versions`. Then `make px4_sitl gz_x500`.

That smoke command was not run in this workspace.

## Prebuilt containers

[PX4 Simulation QuickStart](https://docs.px4.io/main/en/simulation/px4_simulation_quickstart) and the prebuilt-package page are for running PX4 without compiling it.

On Docker Desktop for macOS, the prebuilt page says the containers send MAVLink to the host through `host.docker.internal`. QGroundControl on the host connects without a Docker `-p` publish. The quickstart also shows:

```sh
docker run --rm -it -p 14550:14550/udp px4io/px4-sitl:latest
```

That image is SIH, the headless in-process simulator, not Gazebo. Gazebo headless is:

```sh
docker run --rm -it -e HEADLESS=1 px4io/px4-sitl-gazebo:latest
```

The x500 model inside the Gazebo package is `PX4_SIM_MODEL=gz_x500`. A visible Gazebo window is documented for Linux and Windows/WSLg. On macOS the same page says to run Gazebo headless or use a Linux desktop VM.

These containers were not pulled here.

## Ports, manual control, and loss of link

From [Simulation](https://docs.px4.io/main/en/simulation/) and the prebuilt-package page, a default single instance uses:

| Use | Port |
| --- | --- |
| Ground station listens | UDP 14550 |
| Offboard / API link | UDP 14540, with PX4's local port 14580 for instance 0 |
| Simulator TCP, external MAVLink simulators | TCP 4560 |

Gazebo does not use that simulator MAVLink API. SIH does not use it either. Both still expose the ground-station MAVLink ports. MAVLink listens on localhost unless `MAV_{i}_BROADCAST` is set. This project's ground station is required to stay on an explicit loopback destination. `quad-sim` does not open a socket.

Joystick and gamepad input becomes MAVLink `MANUAL_CONTROL`. [Manual Control](https://docs.px4.io/main/en/config/manual_control) documents `COM_RC_IN_MODE`:

| Value | Source selection |
| --- | --- |
| 0 | RC only |
| 1 | MAVLink only |
| 2 | RC or MAVLink, with fallback |
| 3 | RC or MAVLink, keep the first valid source until reboot |
| 4 | Manual control disabled |
| 5–8 | Priority orders among RC and MAVLink instances |

With one manual source and no RC receiver, the same page says PX4 locks onto the first valid source until reboot. The operator sets the parameter. `quad-sim` does not write PX4 parameters.

The ground-station mapping required by `docs/interfaces.md` is `x = forward * 1000`, `y = right * 1000`, `z = (up + 1) * 500`, `r = yaw_cw * 1000`, then rounded and range-checked. Confirm that mapping against the MAVLink `MANUAL_CONTROL` definition and the PX4 version that is actually running. The z axis in PX4's manual-control setpoint is a throttle-style channel. Pixel flow and stick position are separate streams.

When every allowed manual source is absent for longer than the RC-loss timeout, PX4 triggers its manual-control-loss failsafe. The behavior of that failsafe is the autopilot's configuration. Suppressing further `MANUAL_CONTROL` messages, which the ground station does on disable or on a stale input, is not itself a land command. The fixture `command_stale_gap` is a 680 ms gap in a command log. It is the software stand-in for that timeout. It is not a SITL trace.

## What a later operator can record

A real SITL check, run later and outside this milestone, would record:

- host OS and PX4 git revision
- SIH or Gazebo, and the exact `make` or `docker` command
- whether the Gazebo window was visible
- the `COM_RC_IN_MODE` value
- whether arming was performed by the operator
- a log that shows `MANUAL_CONTROL` arriving from the ground station on loopback

Until that log exists, the supported statement is: fixture tests passed on the host, and PX4 SITL is not yet run.
