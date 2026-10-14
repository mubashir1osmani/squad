//! Decide whether the latest operator sample may authorize MANUAL_CONTROL.
//!
//! Arrival time changes only when a new sample is accepted. Evaluating the
//! gate, including from the output loop, does not move that timestamp.

use crate::csv_cmd::CommandSample;

#[derive(Debug, Clone, PartialEq)]
pub struct LiveInput {
    pub sample: CommandSample,
    /// Monotonic microseconds when this sample was accepted.
    pub arrived_mono_us: u64,
    pub connected: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SuppressReason {
    NoInput,
    Disconnected,
    Disabled,
    Stale { age_us: u64 },
}

impl SuppressReason {
    pub fn class_name(self) -> &'static str {
        match self {
            SuppressReason::NoInput => "no_input",
            SuppressReason::Disconnected => "disconnected",
            SuppressReason::Disabled => "disabled",
            SuppressReason::Stale { .. } => "stale",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GateDecision {
    Send,
    Suppress(SuppressReason),
}

/// `timeout_us` is inclusive: age equal to the timeout is still fresh.
pub fn evaluate(input: Option<&LiveInput>, now_mono_us: u64, timeout_us: u64) -> GateDecision {
    let Some(input) = input else {
        return GateDecision::Suppress(SuppressReason::NoInput);
    };
    if !input.connected {
        return GateDecision::Suppress(SuppressReason::Disconnected);
    }
    let age = now_mono_us.saturating_sub(input.arrived_mono_us);
    if age > timeout_us {
        return GateDecision::Suppress(SuppressReason::Stale { age_us: age });
    }
    if !input.sample.enabled {
        return GateDecision::Suppress(SuppressReason::Disabled);
    }
    GateDecision::Send
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input(enabled: bool, arrived: u64, connected: bool) -> LiveInput {
        LiveInput {
            sample: CommandSample {
                t_us: arrived,
                forward: 0.2,
                right: 0.0,
                up: 0.0,
                yaw_cw: 0.0,
                enabled,
            },
            arrived_mono_us: arrived,
            connected,
        }
    }

    #[test]
    fn fresh_enabled_input_sends_and_boundaries_suppress() {
        let fresh = input(true, 1_000, true);
        assert_eq!(evaluate(Some(&fresh), 1_000, 250_000), GateDecision::Send);
        assert_eq!(evaluate(Some(&fresh), 251_000, 250_000), GateDecision::Send);
        assert!(matches!(
            evaluate(Some(&fresh), 251_001, 250_000),
            GateDecision::Suppress(SuppressReason::Stale { age_us: 250_001 })
        ));
        let disabled = input(false, 1_000, true);
        assert_eq!(
            evaluate(Some(&disabled), 1_000, 250_000),
            GateDecision::Suppress(SuppressReason::Disabled)
        );
        let gone = input(true, 1_000, false);
        assert_eq!(
            evaluate(Some(&gone), 1_000, 250_000),
            GateDecision::Suppress(SuppressReason::Disconnected)
        );
        assert_eq!(
            evaluate(None, 1_000, 250_000),
            GateDecision::Suppress(SuppressReason::NoInput)
        );
    }

    #[test]
    fn evaluate_does_not_require_a_mutable_arrival_time() {
        let sample = input(true, 10, true);
        let before = sample.arrived_mono_us;
        let _ = evaluate(Some(&sample), 1_000_000, 250_000);
        assert_eq!(sample.arrived_mono_us, before);
    }
}
