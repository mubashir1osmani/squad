//! Mode 2 stick mapping and the enable latch.
//!
//! This module is pure and is built without gilrs. The optional `gamepad`
//! feature only adds the device poller in `gamepad_io`.
//!
//! Assumed DualShock 4 / SDL signs, not measured on hardware in this milestone:
//! - Right stick X: right positive → `right`
//! - Right stick Y: up positive → `forward`
//! - Left stick X: right positive → `yaw_cw` (clockwise from above)
//! - Left stick Y: up positive → `up`
//! - L1 (`gilrs::Button::LeftTrigger`) is the deadman
//!
//! Neutral means a gilrs axis at 0. The default deadzone is 0.08. There is no
//! separate calibration capture. Enable arms only on a rising L1 edge that
//! begins with every stick inside the deadzone. After a disconnect, L1 must be
//! released before it can enable again. Motion buttons are not copied into
//! MAVLink; L1 is local authority only.

use crate::csv_cmd::CommandSample;
use crate::normalize::{self, NormalizeError};

pub const DEFAULT_GAMEPAD_DEADZONE: f64 = 0.08;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct RawGamepad {
    pub connected: bool,
    pub left_x: f64,
    pub left_y: f64,
    pub right_x: f64,
    pub right_y: f64,
    pub enable_held: bool,
}

impl RawGamepad {
    pub fn disconnected() -> Self {
        Self {
            connected: false,
            left_x: 0.0,
            left_y: 0.0,
            right_x: 0.0,
            right_y: 0.0,
            enable_held: false,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct GamepadMap {
    pub deadzone: f64,
}

impl Default for GamepadMap {
    fn default() -> Self {
        Self {
            deadzone: DEFAULT_GAMEPAD_DEADZONE,
        }
    }
}

impl GamepadMap {
    pub fn to_sample(&self, raw: RawGamepad, t_us: u64) -> Result<CommandSample, NormalizeError> {
        Ok(CommandSample {
            t_us,
            forward: normalize::normalize_stick(raw.right_y, self.deadzone)?,
            right: normalize::normalize_stick(raw.right_x, self.deadzone)?,
            up: normalize::normalize_stick(raw.left_y, self.deadzone)?,
            yaw_cw: normalize::normalize_stick(raw.left_x, self.deadzone)?,
            enabled: raw.connected && raw.enable_held,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GamepadSafety {
    enable_latched: bool,
    prev_l1: bool,
    await_release: bool,
}

impl Default for GamepadSafety {
    fn default() -> Self {
        Self::new()
    }
}

impl GamepadSafety {
    pub fn new() -> Self {
        Self {
            enable_latched: false,
            prev_l1: false,
            await_release: false,
        }
    }

    pub fn update(&mut self, raw: RawGamepad, deadzone: f64) -> RawGamepad {
        if !raw.connected {
            self.enable_latched = false;
            self.prev_l1 = false;
            self.await_release = true;
            let mut cleared = RawGamepad::disconnected();
            cleared.enable_held = false;
            return cleared;
        }
        if self.await_release {
            if raw.enable_held {
                let mut held = raw;
                held.enable_held = false;
                return held;
            }
            self.await_release = false;
            self.prev_l1 = false;
        }

        let rising = raw.enable_held && !self.prev_l1;
        if !raw.enable_held {
            self.enable_latched = false;
        } else if rising {
            self.enable_latched = sticks_neutral(&raw, deadzone);
        }
        let mut out = raw;
        out.enable_held = self.enable_latched && raw.enable_held;
        self.prev_l1 = raw.enable_held;
        out
    }
}

fn sticks_neutral(raw: &RawGamepad, deadzone: f64) -> bool {
    [raw.left_x, raw.left_y, raw.right_x, raw.right_y]
        .into_iter()
        .all(|axis| axis.is_finite() && axis.abs() <= deadzone)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn centered(enable_held: bool) -> RawGamepad {
        RawGamepad {
            connected: true,
            left_x: 0.0,
            left_y: 0.0,
            right_x: 0.0,
            right_y: 0.0,
            enable_held,
        }
    }

    #[test]
    fn mode2_axes_and_deadzone() {
        let map = GamepadMap { deadzone: 0.08 };
        let raw = RawGamepad {
            connected: true,
            left_x: 0.5,
            left_y: -1.0,
            right_x: 0.04,
            right_y: 1.2,
            enable_held: true,
        };
        let sample = map.to_sample(raw, 20).unwrap();
        assert_eq!(sample.t_us, 20);
        assert_eq!(
            sample.forward, 1.0,
            "right stick up is forward, overshoot clamped"
        );
        assert_eq!(sample.right, 0.0, "inside the deadzone");
        assert_eq!(sample.up, -1.0);
        assert!(sample.yaw_cw > 0.0);
        assert!(sample.enabled);
        let idle = map.to_sample(RawGamepad::disconnected(), 0).unwrap();
        assert!(!idle.enabled);
        assert_eq!(idle.forward, 0.0);
    }

    #[test]
    fn enable_requires_a_neutral_rising_edge_and_disconnect_clears_it() {
        let mut safety = GamepadSafety::new();
        let dz = 0.08;

        let mut deflected = centered(true);
        deflected.right_y = 1.0;
        let blocked = safety.update(deflected, dz);
        assert!(!blocked.enable_held);

        let still_held = safety.update(centered(true), dz);
        assert!(
            !still_held.enable_held,
            "recentering while L1 stays down does not enable"
        );

        let released = safety.update(centered(false), dz);
        assert!(!released.enable_held);
        let armed = safety.update(centered(true), dz);
        assert!(armed.enable_held);

        let mut moved = centered(true);
        moved.left_x = 0.6;
        let held_through_motion = safety.update(moved, dz);
        assert!(held_through_motion.enable_held);

        let cleared = safety.update(RawGamepad::disconnected(), dz);
        assert!(!cleared.connected);
        assert!(!cleared.enable_held);

        let reconnected_still_holding = safety.update(centered(true), dz);
        assert!(reconnected_still_holding.connected);
        assert!(
            !reconnected_still_holding.enable_held,
            "reconnect requires L1 to be released first"
        );
        let _ = safety.update(centered(false), dz);
        let rearmed = safety.update(centered(true), dz);
        assert!(rearmed.enable_held);
    }

    #[test]
    fn a_stopped_poll_goes_stale_and_ticks_do_not_refresh_it() {
        use crate::control::Controller;

        let mut safety = GamepadSafety::new();
        let map = GamepadMap { deadzone: 0.0 };
        let mut controller = Controller::new(250_000, 20, 1).unwrap();
        let raw = safety.update(centered(true), 0.0);
        let sample = map.to_sample(raw, 0).unwrap();
        assert!(sample.enabled);
        controller.push_sample(sample, 0, true);
        assert!(controller.tick(0).unwrap().packet.is_some());
        assert!(controller.tick(50_000).unwrap().packet.is_some());
        assert_eq!(controller.input_arrived_us(), Some(0));
        let stale = controller.tick(250_001).unwrap();
        assert!(stale.packet.is_none());
        assert!(stale.events[0].detail.contains("reason=stale"));
        assert_eq!(controller.input_arrived_us(), Some(0));
    }
}
