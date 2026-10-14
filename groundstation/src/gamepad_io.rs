//! gilrs poller. Built only with `--features gamepad`.
//!
//! A successful poll of a connected pad is an input observation. Calling this
//! function is separate from `Controller::tick`, which does not read the device
//! and does not refresh arrival time.
//!
//! No DualShock 4 was required to compile this module. Do not treat a passing
//! build as a hardware mapping check.

use gilrs::{Axis, Button, Gilrs};

use crate::gamepad::RawGamepad;

pub fn poll_gilrs(gilrs: &mut Gilrs) -> RawGamepad {
    while gilrs.next_event().is_some() {}
    let Some((_, pad)) = gilrs.gamepads().find(|(_, pad)| pad.is_connected()) else {
        return RawGamepad::disconnected();
    };
    RawGamepad {
        connected: true,
        left_x: f64::from(pad.value(Axis::LeftStickX)),
        left_y: f64::from(pad.value(Axis::LeftStickY)),
        right_x: f64::from(pad.value(Axis::RightStickX)),
        right_y: f64::from(pad.value(Axis::RightStickY)),
        enable_held: pad.is_pressed(Button::LeftTrigger),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gilrs_initializes_without_requiring_a_connected_controller() {
        let mut gilrs = Gilrs::new().expect("gilrs context initializes on this host");
        let reading = poll_gilrs(&mut gilrs);
        if !reading.connected {
            assert!(!reading.enable_held);
            assert_eq!(reading.left_x, 0.0);
        }
        let _ = reading;
    }
}
