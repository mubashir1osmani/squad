//! Live sessions. CSV scheduling and the output tick stay separate: a sample's
//! arrival time is its file timestamp, and `tick` never rewrites it.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use crate::control::{render_events, ControlError, ControlEvent, Controller, TickOutcome};
use crate::csv_cmd::{self, CommandLog, CommandSample, CsvError};
use crate::mapping::MapError;
use crate::normalize::NormalizeError;
use crate::playback::Playback;
use crate::telemetry::{self, TelemetryRow};
use crate::transport::{self, LinkConfig, LoopbackLink, TransportError};

static STOP: AtomicBool = AtomicBool::new(false);

fn install_stop_handler() {
    let _ = ctrlc::set_handler(|| {
        STOP.store(true, Ordering::SeqCst);
    });
}

#[derive(Debug)]
pub enum SessionError {
    Transport(TransportError),
    Control(ControlError),
    Csv(CsvError),
    Normalize(NormalizeError),
    Message(String),
}

impl std::fmt::Display for SessionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SessionError::Transport(err) => write!(f, "{err}"),
            SessionError::Control(err) => write!(f, "{err}"),
            SessionError::Csv(err) => write!(f, "{err}"),
            SessionError::Normalize(err) => write!(f, "{err}"),
            SessionError::Message(err) => write!(f, "{err}"),
        }
    }
}

impl std::error::Error for SessionError {}

impl From<TransportError> for SessionError {
    fn from(value: TransportError) -> Self {
        SessionError::Transport(value)
    }
}

impl From<ControlError> for SessionError {
    fn from(value: ControlError) -> Self {
        SessionError::Control(value)
    }
}

impl From<CsvError> for SessionError {
    fn from(value: CsvError) -> Self {
        SessionError::Csv(value)
    }
}

impl From<NormalizeError> for SessionError {
    fn from(value: NormalizeError) -> Self {
        SessionError::Normalize(value)
    }
}

#[derive(Debug, Clone)]
pub struct SessionConfig {
    pub bind: SocketAddr,
    pub destination: SocketAddr,
    pub enable_udp: bool,
    pub target_system: u8,
    pub source_system: u8,
    pub source_component: u8,
    pub rate_hz: u32,
    pub timeout_us: u64,
    pub command_log: Option<PathBuf>,
    pub event_log: Option<PathBuf>,
    pub telemetry_log: Option<PathBuf>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SessionReport {
    pub sent: u64,
    pub input_samples: u64,
    pub telemetry_rows: u64,
    pub telemetry_ignored: u64,
}

#[derive(Debug)]
pub struct CsvDriver {
    playback: Playback,
    controller: Controller,
}

impl CsvDriver {
    pub fn new(
        samples: Vec<CommandSample>,
        timeout_us: u64,
        rate_hz: u32,
        target_system: u8,
    ) -> Result<Self, ControlError> {
        Ok(Self {
            playback: Playback::new(samples),
            controller: Controller::new(timeout_us, rate_hz, target_system)?,
        })
    }

    pub fn done(&self) -> bool {
        self.playback.done()
    }

    pub fn arrival(&self) -> Option<u64> {
        self.controller.input_arrived_us()
    }

    pub fn authority_active(&self, elapsed_us: u64) -> bool {
        self.controller.authority_active(elapsed_us)
    }

    /// Accept newly due samples, then run one output tick.
    ///
    /// Arrival is the sample's `t_us` on the session clock. A late poll does
    /// not stamp an old sample with the current time.
    pub fn step(&mut self, elapsed_us: u64) -> Result<DriverStep, ControlError> {
        let mut accepted = Vec::new();
        let mut events = Vec::new();
        for sample in self.playback.poll(elapsed_us) {
            let arrival = sample.t_us;
            accepted.push(sample);
            events.push(self.controller.push_sample(sample, arrival, true));
        }
        let mut outcome = self.controller.tick(elapsed_us)?;
        events.append(&mut outcome.events);
        outcome.events = events;
        Ok(DriverStep { outcome, accepted })
    }
}

#[derive(Debug)]
pub struct DriverStep {
    pub outcome: TickOutcome,
    pub accepted: Vec<CommandSample>,
}

pub fn run_csv_session(
    samples: Vec<CommandSample>,
    config: &SessionConfig,
) -> Result<SessionReport, SessionError> {
    if !config.enable_udp {
        return Err(TransportError::UdpDisabled.into());
    }
    if config.target_system == 0 {
        return Err(ControlError::Map(MapError::Target).into());
    }
    install_stop_handler();
    STOP.store(false, Ordering::SeqCst);

    let mut driver = CsvDriver::new(
        samples,
        config.timeout_us,
        config.rate_hz,
        config.target_system,
    )?;
    let mut link = LoopbackLink::open(LinkConfig {
        bind: config.bind,
        destination: config.destination,
        enable_udp: true,
        source_system: config.source_system,
        source_component: config.source_component,
    })?;

    let mut accepted = Vec::new();
    let mut events = Vec::new();
    let mut telemetry_rows = Vec::new();
    let mut ignored = 0u64;
    let mut sent = 0u64;
    let start = Instant::now();

    loop {
        if STOP.load(Ordering::SeqCst) {
            events.push(ControlEvent {
                t_mono_us: elapsed_us(start),
                kind: "stopped",
                detail: "reason=operator".into(),
            });
            break;
        }
        let elapsed = elapsed_us(start);
        let stepped = driver.step(elapsed)?;
        accepted.extend(stepped.accepted);
        if transport::dispatch(&mut link, stepped.outcome.packet)? {
            sent += 1;
        }
        events.extend(stepped.outcome.events);
        ignored += ingest_telemetry(&link, elapsed, &mut telemetry_rows)?;
        if driver.done() && !driver.authority_active(elapsed) {
            break;
        }
        sleep_slice(1_000);
    }

    if ignored > 0 {
        events.push(ControlEvent {
            t_mono_us: elapsed_us(start),
            kind: "telemetry_ignored",
            detail: format!("count={ignored}"),
        });
    }
    write_logs(config, &accepted, &events, &telemetry_rows)?;
    Ok(SessionReport {
        sent,
        input_samples: accepted.len() as u64,
        telemetry_rows: telemetry_rows.len() as u64,
        telemetry_ignored: ignored,
    })
}

fn ingest_telemetry(
    link: &LoopbackLink,
    elapsed: u64,
    rows: &mut Vec<TelemetryRow>,
) -> Result<u64, SessionError> {
    let mut ignored = 0u64;
    for (header, message) in link.poll_frames()? {
        match telemetry::classify(&header, &message, elapsed) {
            Some(row) => rows.push(row),
            None => ignored += 1,
        }
    }
    Ok(ignored)
}

fn write_logs(
    config: &SessionConfig,
    accepted: &[CommandSample],
    events: &[ControlEvent],
    telemetry_rows: &[TelemetryRow],
) -> Result<(), SessionError> {
    if let Some(path) = &config.command_log {
        let csv = csv_cmd::canonicalize(
            &CommandLog {
                samples: accepted.to_vec(),
            },
            0.0,
        )?;
        csv_cmd::write_atomic(path, &csv)?;
    }
    if let Some(path) = &config.event_log {
        csv_cmd::write_atomic(path, &render_events(events))?;
    }
    if let Some(path) = &config.telemetry_log {
        csv_cmd::write_atomic(path, &telemetry::render(telemetry_rows))?;
    }
    Ok(())
}

fn elapsed_us(start: Instant) -> u64 {
    start.elapsed().as_micros() as u64
}

fn sleep_slice(micros: u64) {
    if micros > 0 {
        thread::sleep(Duration::from_micros(micros));
    }
}

#[cfg(feature = "gamepad")]
mod gamepad_session {
    use super::*;
    use crate::gamepad::{GamepadMap, GamepadSafety, RawGamepad};
    use crate::gamepad_io::poll_gilrs;

    pub fn run(
        config: &SessionConfig,
        deadzone: f64,
        duration_us: Option<u64>,
        input_interval_us: u64,
    ) -> Result<SessionReport, SessionError> {
        if !config.enable_udp {
            return Err(TransportError::UdpDisabled.into());
        }
        if !(1_000..=50_000).contains(&input_interval_us) {
            return Err(SessionError::Message(
                "gamepad poll interval must be 1..=50 ms".into(),
            ));
        }
        let mut gilrs = gilrs::Gilrs::new()
            .map_err(|err| SessionError::Message(format!("gamepad context failed: {err}")))?;
        install_stop_handler();
        STOP.store(false, Ordering::SeqCst);

        let mut controller =
            Controller::new(config.timeout_us, config.rate_hz, config.target_system)?;
        let mut link = LoopbackLink::open(LinkConfig {
            bind: config.bind,
            destination: config.destination,
            enable_udp: true,
            source_system: config.source_system,
            source_component: config.source_component,
        })?;
        let map = GamepadMap { deadzone };
        let mut safety = GamepadSafety::new();
        let mut accepted = Vec::new();
        let mut events = Vec::new();
        let mut telemetry_rows = Vec::new();
        let mut ignored = 0u64;
        let mut sent = 0u64;
        let mut next_poll = 0u64;
        let mut saw_disconnect = false;
        let start = Instant::now();

        loop {
            if STOP.load(Ordering::SeqCst) {
                break;
            }
            let elapsed = elapsed_us(start);
            if duration_us.is_some_and(|limit| elapsed >= limit) {
                break;
            }
            if elapsed >= next_poll {
                next_poll = elapsed.saturating_add(input_interval_us);
                let reading = safety.update(poll_gilrs(&mut gilrs), deadzone);
                if reading.connected {
                    saw_disconnect = false;
                    let sample = map.to_sample(reading, elapsed)?;
                    events.push(controller.push_sample(sample, elapsed, true));
                    accepted.push(sample);
                } else if !saw_disconnect {
                    saw_disconnect = true;
                    let sample = map.to_sample(RawGamepad::disconnected(), elapsed)?;
                    events.push(controller.push_sample(sample, elapsed, false));
                    accepted.push(sample);
                }
            }
            let outcome = controller.tick(elapsed)?;
            if transport::dispatch(&mut link, outcome.packet)? {
                sent += 1;
            }
            events.extend(outcome.events);
            ignored += ingest_telemetry(&link, elapsed, &mut telemetry_rows)?;
            sleep_slice(1_000);
        }

        if ignored > 0 {
            events.push(ControlEvent {
                t_mono_us: elapsed_us(start),
                kind: "telemetry_ignored",
                detail: format!("count={ignored}"),
            });
        }
        write_logs(config, &accepted, &events, &telemetry_rows)?;
        Ok(SessionReport {
            sent,
            input_samples: accepted.len() as u64,
            telemetry_rows: telemetry_rows.len() as u64,
            telemetry_ignored: ignored,
        })
    }
}

#[cfg(feature = "gamepad")]
pub use gamepad_session::run as run_gamepad_session;

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(t_us: u64, forward: f64, enabled: bool) -> CommandSample {
        CommandSample {
            t_us,
            forward,
            right: 0.0,
            up: 0.0,
            yaw_cw: 0.0,
            enabled,
        }
    }

    #[test]
    fn late_poll_does_not_refresh_an_old_sample() {
        let mut driver = CsvDriver::new(vec![sample(0, 1.0, true)], 250_000, 20, 1).unwrap();
        let stepped = driver.step(300_000).unwrap();
        assert!(stepped.outcome.packet.is_none());
        assert_eq!(stepped.accepted.len(), 1);
        assert_eq!(driver.arrival(), Some(0));
        assert!(stepped
            .outcome
            .events
            .iter()
            .any(|event| event.detail.contains("reason=stale")));
        let again = driver.step(500_000).unwrap();
        assert!(again.outcome.packet.is_none());
        assert!(again.accepted.is_empty());
        assert_eq!(driver.arrival(), Some(0));
    }

    #[test]
    fn a_gap_drops_authority_until_the_next_sample() {
        let mut driver = CsvDriver::new(
            vec![sample(0, 1.0, true), sample(1_000_000, -1.0, true)],
            250_000,
            50,
            1,
        )
        .unwrap();
        let first = driver.step(0).unwrap();
        assert_eq!(first.outcome.packet.unwrap().x, 1000);
        let held = driver.step(250_000).unwrap();
        assert!(
            held.outcome.packet.is_some(),
            "age equal to the timeout still sends"
        );
        assert_eq!(driver.arrival(), Some(0));
        let stale = driver.step(250_001).unwrap();
        assert!(stale.outcome.packet.is_none());
        assert_eq!(driver.arrival(), Some(0));
        let still = driver.step(999_999).unwrap();
        assert!(still.outcome.packet.is_none());
        assert!(still.accepted.is_empty());
        assert_eq!(driver.arrival(), Some(0));
        let resumed = driver.step(1_000_000).unwrap();
        assert_eq!(resumed.outcome.packet.unwrap().x, -1000);
        assert_eq!(driver.arrival(), Some(1_000_000));
    }

    #[test]
    fn one_late_poll_keeps_every_due_sample_and_uses_the_latest() {
        let mut driver = CsvDriver::new(
            vec![sample(0, 1.0, true), sample(10, -1.0, true)],
            250_000,
            20,
            1,
        )
        .unwrap();
        let stepped = driver.step(10).unwrap();
        assert_eq!(stepped.accepted.len(), 2);
        assert_eq!(stepped.outcome.packet.unwrap().x, -1000);
        assert_eq!(driver.arrival(), Some(10));
    }

    #[test]
    fn disabled_sample_is_kept_and_has_no_packet() {
        let mut driver = CsvDriver::new(
            vec![sample(0, 0.2, true), sample(20_000, 0.2, false)],
            250_000,
            20,
            4,
        )
        .unwrap();
        assert!(driver.step(0).unwrap().outcome.packet.is_some());
        let stopped = driver.step(20_000).unwrap();
        assert!(stopped.outcome.packet.is_none());
        assert!(stopped
            .outcome
            .events
            .iter()
            .any(|event| event.detail.contains("reason=disabled")));
        assert_eq!(driver.arrival(), Some(20_000));
        assert!(driver.done());
    }
}
