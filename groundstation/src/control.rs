//! Rate-limited MANUAL_CONTROL decisions.
//!
//! `tick` reads the stored arrival time and never writes it. A new operator
//! sample is the only path that refreshes freshness.

use crate::csv_cmd::{format_axis, CommandSample};
use crate::freshness::{evaluate, GateDecision, LiveInput, SuppressReason};
use crate::mapping::{map_command, ManualControlAxes, MapError};
use crate::rate_limit::{RateError, RateLimiter};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ControlEvent {
    pub t_mono_us: u64,
    pub kind: &'static str,
    pub detail: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TickOutcome {
    pub packet: Option<ManualControlAxes>,
    pub events: Vec<ControlEvent>,
}

#[derive(Debug)]
pub enum ControlError {
    Rate(RateError),
    Map(MapError),
}

impl std::fmt::Display for ControlError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ControlError::Rate(err) => write!(f, "{err}"),
            ControlError::Map(err) => write!(f, "{err}"),
        }
    }
}

impl std::error::Error for ControlError {}

#[derive(Debug)]
pub struct Controller {
    timeout_us: u64,
    target_system: u8,
    rate: RateLimiter,
    input: Option<LiveInput>,
    latched_suppress: Option<&'static str>,
}

impl Controller {
    pub fn new(timeout_us: u64, rate_hz: u32, target_system: u8) -> Result<Self, ControlError> {
        if target_system == 0 {
            return Err(ControlError::Map(MapError::Target));
        }
        Ok(Self {
            timeout_us,
            target_system,
            rate: RateLimiter::new(rate_hz).map_err(ControlError::Rate)?,
            input: None,
            latched_suppress: None,
        })
    }

    pub fn input_arrived_us(&self) -> Option<u64> {
        self.input.as_ref().map(|input| input.arrived_mono_us)
    }

    pub fn input(&self) -> Option<&LiveInput> {
        self.input.as_ref()
    }

    /// Accept one new sample. This is the only method that sets the arrival time.
    pub fn push_sample(
        &mut self,
        sample: CommandSample,
        now_mono_us: u64,
        connected: bool,
    ) -> ControlEvent {
        self.input = Some(LiveInput {
            sample,
            arrived_mono_us: now_mono_us,
            connected,
        });
        ControlEvent {
            t_mono_us: now_mono_us,
            kind: "input",
            detail: format!(
                "t_us={};enabled={};forward={};right={};up={};yaw_cw={};connected={}",
                sample.t_us,
                if sample.enabled { 1 } else { 0 },
                format_axis(sample.forward),
                format_axis(sample.right),
                format_axis(sample.up),
                format_axis(sample.yaw_cw),
                if connected { 1 } else { 0 }
            ),
        }
    }

    /// Mark the source disconnected without treating the output loop as new input.
    pub fn set_disconnected(&mut self) {
        if let Some(input) = self.input.as_mut() {
            input.connected = false;
        }
    }

    pub fn authority_active(&self, now_mono_us: u64) -> bool {
        matches!(
            evaluate(self.input.as_ref(), now_mono_us, self.timeout_us),
            GateDecision::Send
        )
    }

    pub fn tick(&mut self, now_mono_us: u64) -> Result<TickOutcome, ControlError> {
        let arrived_before = self.input_arrived_us();
        let decision = evaluate(self.input.as_ref(), now_mono_us, self.timeout_us);
        let mut events = Vec::new();
        let packet = match decision {
            GateDecision::Send => {
                self.latched_suppress = None;
                if self.rate.try_send(now_mono_us) {
                    let sample = self.input.as_ref().expect("send requires an input").sample;
                    let axes =
                        map_command(&sample, self.target_system).map_err(ControlError::Map)?;
                    events.push(ControlEvent {
                        t_mono_us: now_mono_us,
                        kind: "sent",
                        detail: format!(
                            "target={};x={};y={};z={};r={};buttons={}",
                            axes.target, axes.x, axes.y, axes.z, axes.r, axes.buttons
                        ),
                    });
                    Some(axes)
                } else {
                    None
                }
            }
            GateDecision::Suppress(reason) => {
                let class_name = reason.class_name();
                if self.latched_suppress != Some(class_name) {
                    self.latched_suppress = Some(class_name);
                    events.push(ControlEvent {
                        t_mono_us: now_mono_us,
                        kind: "suppressed",
                        detail: suppress_detail(reason),
                    });
                }
                None
            }
        };
        debug_assert_eq!(self.input_arrived_us(), arrived_before);
        Ok(TickOutcome { packet, events })
    }
}

fn suppress_detail(reason: SuppressReason) -> String {
    match reason {
        SuppressReason::Stale { age_us } => format!("reason=stale;age_us={age_us}"),
        other => format!("reason={}", other.class_name()),
    }
}

pub fn render_events(events: &[ControlEvent]) -> String {
    let mut out = String::from("t_mono_us,kind,detail\n");
    for event in events {
        debug_assert!(!event.detail.contains([',', '\n', '\r']));
        out.push_str(&event.t_mono_us.to_string());
        out.push(',');
        out.push_str(event.kind);
        out.push(',');
        out.push_str(&event.detail);
        out.push('\n');
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn enabled(forward: f64) -> CommandSample {
        CommandSample {
            t_us: 0,
            forward,
            right: -0.5,
            up: 0.0,
            yaw_cw: 0.5,
            enabled: true,
        }
    }

    fn disabled() -> CommandSample {
        CommandSample {
            t_us: 40_000,
            forward: 0.2,
            right: 0.0,
            up: 0.0,
            yaw_cw: 0.0,
            enabled: false,
        }
    }

    #[test]
    fn fresh_enabled_sample_maps_and_disabled_sample_suppresses() {
        let mut controller = Controller::new(250_000, 20, 1).unwrap();
        controller.push_sample(enabled(0.25), 0, true);
        let first = controller.tick(0).unwrap();
        let axes = first.packet.expect("fresh enabled input sends");
        assert_eq!(axes.x, 250);
        assert_eq!(axes.y, -500);
        assert_eq!(axes.z, 500);
        assert_eq!(axes.r, 500);
        assert_eq!(axes.buttons, 0);
        assert_eq!(axes.target, 1);

        controller.push_sample(disabled(), 1_000, true);
        let stopped = controller.tick(1_000).unwrap();
        assert!(stopped.packet.is_none());
        assert_eq!(stopped.events[0].kind, "suppressed");
        assert!(stopped.events[0].detail.contains("reason=disabled"));
    }

    #[test]
    fn output_ticks_do_not_refresh_stale_input() {
        let mut controller = Controller::new(250_000, 20, 1).unwrap();
        controller.push_sample(enabled(0.2), 1_000, true);
        let arrived = controller.input_arrived_us().unwrap();
        assert!(controller.tick(1_000).unwrap().packet.is_some());
        for now in [1_001_u64, 50_000, 249_000, 251_001, 500_000, 2_000_000] {
            let outcome = controller.tick(now).unwrap();
            assert_eq!(controller.input_arrived_us(), Some(arrived));
            if now > arrived + 250_000 {
                assert!(outcome.packet.is_none());
            }
        }
        let stale = controller.tick(2_000_000).unwrap();
        assert!(stale.packet.is_none());
        assert_eq!(controller.input_arrived_us(), Some(arrived));
        assert_eq!(
            stale
                .events
                .iter()
                .filter(|event| event.detail.contains("reason=stale"))
                .count(),
            0,
            "the stale transition is recorded once"
        );
        let first_stale = controller.tick(251_001).unwrap();
        // Already latched earlier in the loop, so this call adds no new event.
        assert!(first_stale.events.is_empty());
    }

    #[test]
    fn stale_transition_is_recorded_once_with_the_original_arrival() {
        let mut controller = Controller::new(250_000, 50, 3).unwrap();
        controller.push_sample(enabled(1.0), 0, true);
        assert!(controller.tick(0).unwrap().packet.is_some());
        let at_edge = controller.tick(250_000).unwrap();
        assert!(
            at_edge.packet.is_some(),
            "age equal to the timeout is still fresh"
        );
        let stale = controller.tick(250_001).unwrap();
        assert!(stale.packet.is_none());
        assert_eq!(stale.events.len(), 1);
        assert!(stale.events[0].detail.contains("reason=stale"));
        assert!(stale.events[0].detail.contains("age_us=250001"));
        let again = controller.tick(400_000).unwrap();
        assert!(again.events.is_empty());
        assert_eq!(controller.input_arrived_us(), Some(0));
    }

    #[test]
    fn rate_limit_counts_sends_without_moving_arrival() {
        let mut controller = Controller::new(1_000_000, 20, 1).unwrap();
        controller.push_sample(enabled(0.2), 0, true);
        let mut sent = 0;
        for now in [0, 49_999, 50_000, 99_999, 100_000] {
            if controller.tick(now).unwrap().packet.is_some() {
                sent += 1;
            }
            assert_eq!(controller.input_arrived_us(), Some(0));
        }
        assert_eq!(sent, 3);
    }

    #[test]
    fn disconnect_suppresses_without_refreshing_arrival() {
        let mut controller = Controller::new(250_000, 20, 1).unwrap();
        controller.push_sample(enabled(0.2), 5_000, true);
        controller.set_disconnected();
        let outcome = controller.tick(5_000).unwrap();
        assert!(outcome.packet.is_none());
        assert!(outcome.events[0].detail.contains("reason=disconnected"));
        assert_eq!(controller.input_arrived_us(), Some(5_000));
        let later = controller.tick(5_000 + 10_000).unwrap();
        assert!(later.packet.is_none());
        assert_eq!(controller.input_arrived_us(), Some(5_000));
    }

    #[test]
    fn no_input_suppresses_and_a_later_sample_can_send() {
        let mut controller = Controller::new(250_000, 20, 9).unwrap();
        let initial = controller.tick(0).unwrap();
        assert!(initial.packet.is_none());
        assert!(initial.events[0].detail.contains("reason=no_input"));
        controller.push_sample(enabled(-1.0), 10, true);
        let sent = controller.tick(10).unwrap();
        let axes = sent.packet.unwrap();
        assert_eq!(axes.target, 9);
        assert_eq!(axes.x, -1000);
        assert_eq!(axes.z, 500);
    }
}
