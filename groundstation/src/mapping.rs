//! Map a normalized command onto MAVLink `MANUAL_CONTROL` fields.
//!
//! Shared contract: `x = forward * 1000`, `y = right * 1000`,
//! `z = (up + 1) * 500`, `r = yaw_cw * 1000`, rounded half away from zero.
//!
//! That `z` encoding matches the stick values QGroundControl sends and the
//! values PX4 now emits in `src/modules/mavlink/streams/MANUAL_CONTROL.hpp`:
//! `z = (throttle + 1) * 500`, with throttle in [-1, 1]. PX4's
//! `ManualControlSetpoint.msg` documents that MAVLink `z` arrives in
//! [0, 1000] and is converted back to the internal throttle. MAVLink's
//! generic description still says `z` lies in [-1000, 1000]; this project
//! follows the shared interface and the PX4/QGC [0, 1000] convention.
//!
//! `z` is a stick code. It is not newtons, motor RPM, or a thrust estimate.
//! `INT16_MAX` (32767) is the MAVLink invalid-axis sentinel and is never
//! produced here. Button bits stay 0: on PX4 several of those bits are mode
//! switches, and this milestone does not change flight mode.

use std::fmt;

use crate::csv_cmd::CommandSample;

/// `MANUAL_CONTROL` msgid in the common dialect.
pub const MANUAL_CONTROL_MSGID: u32 = 69;

/// MAVLink invalid-axis sentinel. Valid mapped axes never use this value.
pub const INVALID_AXIS: i16 = i16::MAX;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ManualControlAxes {
    pub target: u8,
    pub x: i16,
    pub y: i16,
    pub z: i16,
    pub r: i16,
    pub buttons: u16,
}

#[derive(Debug, Clone, PartialEq)]
pub enum MapError {
    NonFinite { field: &'static str },
    OutOfRange { field: &'static str, value: f64 },
    RoundedOutOfRange { field: &'static str, value: i64 },
    Target,
}

impl fmt::Display for MapError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            MapError::NonFinite { field } => write!(f, "{field} is not finite"),
            MapError::OutOfRange { field, value } => {
                write!(f, "{field} {value} is outside [-1, 1]")
            }
            MapError::RoundedOutOfRange { field, value } => {
                write!(
                    f,
                    "{field} rounded to {value}, which is outside the MAVLink stick range"
                )
            }
            MapError::Target => write!(f, "target system must be 1..=255"),
        }
    }
}

impl std::error::Error for MapError {}

pub fn map_command(
    sample: &CommandSample,
    target_system: u8,
) -> Result<ManualControlAxes, MapError> {
    if target_system == 0 {
        return Err(MapError::Target);
    }
    let x = scale_thousand(sample.forward, "forward")?;
    let y = scale_thousand(sample.right, "right")?;
    let r = scale_thousand(sample.yaw_cw, "yaw_cw")?;
    let z = scale_collective(sample.up)?;
    Ok(ManualControlAxes {
        target: target_system,
        x,
        y,
        z,
        r,
        buttons: 0,
    })
}

fn scale_thousand(value: f64, field: &'static str) -> Result<i16, MapError> {
    check_axis(value, field)?;
    let rounded = round_half_away_from_zero(value * 1000.0)?;
    if !(-1000..=1000).contains(&rounded) || rounded == i64::from(INVALID_AXIS) {
        return Err(MapError::RoundedOutOfRange {
            field,
            value: rounded,
        });
    }
    Ok(rounded as i16)
}

fn scale_collective(up: f64) -> Result<i16, MapError> {
    check_axis(up, "up")?;
    let rounded = round_half_away_from_zero((up + 1.0) * 500.0)?;
    if !(0..=1000).contains(&rounded) || rounded == i64::from(INVALID_AXIS) {
        return Err(MapError::RoundedOutOfRange {
            field: "z",
            value: rounded,
        });
    }
    Ok(rounded as i16)
}

fn check_axis(value: f64, field: &'static str) -> Result<(), MapError> {
    if !value.is_finite() {
        return Err(MapError::NonFinite { field });
    }
    if !(-1.0..=1.0).contains(&value) {
        return Err(MapError::OutOfRange { field, value });
    }
    Ok(())
}

pub fn round_half_away_from_zero(value: f64) -> Result<i64, MapError> {
    if !value.is_finite() {
        return Err(MapError::NonFinite { field: "scaled" });
    }
    let rounded = if value >= 0.0 {
        (value + 0.5).floor()
    } else {
        (value - 0.5).ceil()
    };
    if !rounded.is_finite() || rounded.abs() > i64::MAX as f64 {
        return Err(MapError::RoundedOutOfRange {
            field: "scaled",
            value: 0,
        });
    }
    Ok(rounded as i64)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn assert_same_sign(axis: f64, mapped: i16) {
        if axis == 0.0 {
            assert_eq!(mapped, 0);
        } else if axis > 0.0 {
            assert!(mapped > 0);
        } else {
            assert!(mapped < 0);
        }
    }

    fn sample(forward: f64, right: f64, up: f64, yaw_cw: f64) -> CommandSample {
        CommandSample {
            t_us: 0,
            forward,
            right,
            up,
            yaw_cw,
            enabled: true,
        }
    }

    #[test]
    fn rounds_half_away_from_zero() {
        assert_eq!(round_half_away_from_zero(0.5).unwrap(), 1);
        assert_eq!(round_half_away_from_zero(-0.5).unwrap(), -1);
        assert_eq!(round_half_away_from_zero(1.5).unwrap(), 2);
        assert_eq!(round_half_away_from_zero(-1.5).unwrap(), -2);
        assert_eq!(round_half_away_from_zero(0.49).unwrap(), 0);
        assert_eq!(round_half_away_from_zero(-0.49).unwrap(), 0);
    }

    #[test]
    fn maps_signs_and_collective_stick() {
        let centered = map_command(&sample(0.0, 0.0, 0.0, 0.0), 1).unwrap();
        assert_eq!(
            centered,
            ManualControlAxes {
                target: 1,
                x: 0,
                y: 0,
                z: 500,
                r: 0,
                buttons: 0
            }
        );

        let full = map_command(&sample(1.0, 1.0, 1.0, 1.0), 42).unwrap();
        assert_eq!(full.x, 1000);
        assert_eq!(full.y, 1000);
        assert_eq!(full.z, 1000);
        assert_eq!(full.r, 1000);
        assert_eq!(full.target, 42);
        assert_eq!(full.buttons, 0);

        let low = map_command(&sample(-1.0, -1.0, -1.0, -1.0), 1).unwrap();
        assert_eq!(low.x, -1000);
        assert_eq!(low.y, -1000);
        assert_eq!(low.z, 0);
        assert_eq!(low.r, -1000);

        let partial = map_command(&sample(0.25, -0.5, 0.5, -0.25), 7).unwrap();
        assert_eq!(partial.x, 250);
        assert_eq!(partial.y, -500);
        assert_eq!(partial.z, 750);
        assert_eq!(partial.r, -250);
        assert_ne!(partial.x, INVALID_AXIS);
    }

    #[test]
    fn maps_the_spec_forward_example() {
        let mapped = map_command(&sample(0.2, 0.0, 0.0, 0.0), 1).unwrap();
        assert_eq!(mapped.x, 200);
        assert_eq!(mapped.y, 0);
        assert_eq!(mapped.z, 500);
        assert_eq!(mapped.r, 0);
    }

    #[test]
    fn grid_stays_inside_stick_codes_and_avoids_the_sentinel() {
        let values = [-1.0, -0.5, 0.0, 0.5, 1.0];
        for forward in values {
            for right in values {
                for up in values {
                    for yaw in values {
                        let mapped = map_command(&sample(forward, right, up, yaw), 1).unwrap();
                        assert!((-1000..=1000).contains(&mapped.x));
                        assert!((-1000..=1000).contains(&mapped.y));
                        assert!((0..=1000).contains(&mapped.z));
                        assert!((-1000..=1000).contains(&mapped.r));
                        assert_ne!(mapped.x, INVALID_AXIS);
                        assert_ne!(mapped.y, INVALID_AXIS);
                        assert_ne!(mapped.z, INVALID_AXIS);
                        assert_ne!(mapped.r, INVALID_AXIS);
                        assert_eq!(mapped.buttons, 0);
                        assert_same_sign(forward, mapped.x);
                        assert_same_sign(right, mapped.y);
                        assert_same_sign(yaw, mapped.r);
                    }
                }
            }
        }
    }

    #[test]
    fn rejects_nonfinite_out_of_range_and_broadcast_target() {
        let mut bad = sample(f64::NAN, 0.0, 0.0, 0.0);
        assert!(map_command(&bad, 1).is_err());
        bad.forward = 1.01;
        assert!(map_command(&bad, 1).is_err());
        bad.forward = 0.0;
        assert!(map_command(&bad, 0).is_err());
    }
}
