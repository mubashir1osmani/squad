//! quad-groundstation command line.
//!
//! `replay` validates commands and writes canonical CSV. It does not sleep and
//! it does not open a socket. `control` and `gamepad` open UDP only with
//! `--enable-udp` and only toward a numeric loopback address.

#![forbid(unsafe_code)]

use std::net::SocketAddr;
use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Parser, Subcommand};

use quad_groundstation::csv_cmd;
use quad_groundstation::replay::{replay_paths, ReplayOptions};
use quad_groundstation::session::{run_csv_session, SessionConfig};
use quad_groundstation::transport::{GCS_COMPONENT_ID, GCS_SYSTEM_ID};
use quad_groundstation::{DEFAULT_RATE_HZ, MAX_RATE_HZ};

#[derive(Debug, Parser)]
#[command(
    name = "quad-groundstation",
    version,
    about = "Validate manual commands offline, or send localhost MANUAL_CONTROL when explicitly enabled"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Validate a command CSV and write the canonical file. No network and no sleeps.
    Replay(ReplayArgs),
    /// Stream a command CSV as MANUAL_CONTROL to an operator-started PX4 SITL on localhost.
    Control(ControlArgs),
    /// Read an optional gamepad and stream MANUAL_CONTROL on localhost.
    Gamepad(GamepadArgs),
}

#[derive(Debug, clap::Args)]
struct ReplayArgs {
    #[arg(long)]
    input: PathBuf,
    #[arg(long)]
    output: PathBuf,
    /// Axial deadzone applied while canonicalizing. The default keeps validated values.
    #[arg(long, default_value_t = 0.0)]
    deadzone: f64,
    /// Gap threshold in milliseconds. Larger gaps are reported, not deleted.
    #[arg(long, default_value_t = 250)]
    timeout_ms: u64,
    /// Optional gap report. This is not a MAVLink log.
    #[arg(long)]
    events: Option<PathBuf>,
}

#[derive(Debug, clap::Args)]
struct ControlArgs {
    #[arg(long)]
    input: PathBuf,
    /// Required. Without this flag the command returns before opening a socket.
    #[arg(long, default_value_t = false)]
    enable_udp: bool,
    /// Numeric loopback destination, for example 127.0.0.1:14580.
    #[arg(long, value_parser = parse_loopback)]
    destination: SocketAddr,
    /// Numeric loopback bind address. Use 127.0.0.1:14540 to hear PX4 SITL offboard telemetry.
    #[arg(long, value_parser = parse_loopback, default_value = "127.0.0.1:0")]
    bind: SocketAddr,
    #[arg(long, default_value_t = 1)]
    target_system: u8,
    #[arg(long, default_value_t = GCS_SYSTEM_ID)]
    source_system: u8,
    #[arg(long, default_value_t = GCS_COMPONENT_ID)]
    source_component: u8,
    /// MANUAL_CONTROL send rate. Allowed range is 1..=50 Hz.
    #[arg(long, default_value_t = DEFAULT_RATE_HZ)]
    rate_hz: u32,
    #[arg(long, default_value_t = 250)]
    timeout_ms: u64,
    #[arg(long, default_value_t = 0.0)]
    deadzone: f64,
    #[arg(long)]
    command_log: Option<PathBuf>,
    #[arg(long)]
    event_log: Option<PathBuf>,
    #[arg(long)]
    telemetry_log: Option<PathBuf>,
}

#[derive(Debug, clap::Args)]
struct GamepadArgs {
    #[arg(long, default_value_t = false)]
    enable_udp: bool,
    #[arg(long, value_parser = parse_loopback)]
    destination: SocketAddr,
    #[arg(long, value_parser = parse_loopback, default_value = "127.0.0.1:0")]
    bind: SocketAddr,
    #[arg(long, default_value_t = 1)]
    target_system: u8,
    #[arg(long, default_value_t = GCS_SYSTEM_ID)]
    source_system: u8,
    #[arg(long, default_value_t = GCS_COMPONENT_ID)]
    source_component: u8,
    #[arg(long, default_value_t = DEFAULT_RATE_HZ)]
    rate_hz: u32,
    #[arg(long, default_value_t = 250)]
    timeout_ms: u64,
    #[arg(long, default_value_t = quad_groundstation::gamepad::DEFAULT_GAMEPAD_DEADZONE)]
    deadzone: f64,
    /// Stop after this many milliseconds. Omit it to run until Ctrl-C.
    #[arg(long)]
    duration_ms: Option<u64>,
    /// How often a connected pad is sampled, in milliseconds. Separate from --rate-hz.
    #[arg(long, default_value_t = 20)]
    poll_ms: u64,
    #[arg(long)]
    command_log: Option<PathBuf>,
    #[arg(long)]
    event_log: Option<PathBuf>,
    #[arg(long)]
    telemetry_log: Option<PathBuf>,
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            eprintln!("error: {err}");
            ExitCode::from(1)
        }
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let cli = Cli::parse();
    match cli.command {
        Command::Replay(args) => {
            let timeout_us = ms_to_us(args.timeout_ms)?;
            let product = replay_paths(
                &args.input,
                &args.output,
                args.events.as_deref(),
                &ReplayOptions {
                    deadzone: args.deadzone,
                    timeout_us,
                },
            )?;
            println!(
                "wrote {} samples; hold gaps: {}",
                product.commands_csv.lines().count().saturating_sub(1),
                product.gaps.len()
            );
            Ok(())
        }
        Command::Control(args) => {
            if !(1..=MAX_RATE_HZ).contains(&args.rate_hz) {
                return Err(format!("send rate must be 1..={MAX_RATE_HZ} Hz").into());
            }
            let log = csv_cmd::read_path(&args.input)?;
            let canonical = csv_cmd::canonicalize(&log, args.deadzone)?;
            let parsed = csv_cmd::parse_str(&canonical)?;
            let timeout_us = ms_to_us(args.timeout_ms)?;
            let report = run_csv_session(parsed.samples, &args.session(timeout_us))?;
            println!(
                "sent={} input_samples={} telemetry_rows={}",
                report.sent, report.input_samples, report.telemetry_rows
            );
            Ok(())
        }
        Command::Gamepad(args) => run_gamepad(args),
    }
}

fn run_gamepad(args: GamepadArgs) -> Result<(), Box<dyn std::error::Error>> {
    #[cfg(not(feature = "gamepad"))]
    {
        let _ = args;
        Err("this binary was built without gamepad support; rebuild with --features gamepad".into())
    }
    #[cfg(feature = "gamepad")]
    {
        if !(1..=MAX_RATE_HZ).contains(&args.rate_hz) {
            return Err(format!("send rate must be 1..={MAX_RATE_HZ} Hz").into());
        }
        let timeout_us = ms_to_us(args.timeout_ms)?;
        let poll_us = ms_to_us(args.poll_ms)?;
        let report = quad_groundstation::session::run_gamepad_session(
            &args.session(timeout_us),
            args.deadzone,
            args.duration_ms.map(|ms| ms.saturating_mul(1_000)),
            poll_us,
        )?;
        println!(
            "sent={} input_samples={} telemetry_rows={}",
            report.sent, report.input_samples, report.telemetry_rows
        );
        Ok(())
    }
}

impl ControlArgs {
    fn session(&self, timeout_us: u64) -> SessionConfig {
        SessionConfig {
            bind: self.bind,
            destination: self.destination,
            enable_udp: self.enable_udp,
            target_system: self.target_system,
            source_system: self.source_system,
            source_component: self.source_component,
            rate_hz: self.rate_hz,
            timeout_us,
            command_log: self.command_log.clone(),
            event_log: self.event_log.clone(),
            telemetry_log: self.telemetry_log.clone(),
        }
    }
}

#[cfg(feature = "gamepad")]
impl GamepadArgs {
    fn session(&self, timeout_us: u64) -> SessionConfig {
        SessionConfig {
            bind: self.bind,
            destination: self.destination,
            enable_udp: self.enable_udp,
            target_system: self.target_system,
            source_system: self.source_system,
            source_component: self.source_component,
            rate_hz: self.rate_hz,
            timeout_us,
            command_log: self.command_log.clone(),
            event_log: self.event_log.clone(),
            telemetry_log: self.telemetry_log.clone(),
        }
    }
}

fn parse_loopback(text: &str) -> Result<SocketAddr, String> {
    let addr: SocketAddr = text
        .parse()
        .map_err(|err| format!("use a numeric host:port address ({err})"))?;
    if addr.ip().is_loopback() {
        Ok(addr)
    } else {
        Err(format!("{addr} is not a loopback address"))
    }
}

fn ms_to_us(ms: u64) -> Result<u64, String> {
    ms.checked_mul(1_000)
        .ok_or_else(|| "timeout overflowed microseconds".to_string())
}
