//! Axial deadzone and stick normalization.
//!
//! CSV values are already required to lie in [-1, 1] and are rejected when they
//! do not. Gamepad axes may slightly overshoot, so stick normalization clamps
//! that overshoot and then applies the same deadzone.

use std::fmt;

#[derive(Debug, Clone, PartialEq)]
pub enum NormalizeError {
    NonFinite,
    DeadzoneRange { deadzone: f64 },
    OutOfRange { value: f64 },
}

impl fmt::Display for NormalizeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            NormalizeError::NonFinite => write!(f, "stick value or deadzone is not finite"),
            NormalizeError::DeadzoneRange { deadzone } => {
                write!(f, "deadzone {deadzone} must be in [0, 1)")
            }
            NormalizeError::OutOfRange { value } => {
                write!(f, "normalized axis {value} is outside [-1, 1]")
            }
        }
    }
}

impl std::error::Error for NormalizeError {}

/// Collapse `[-deadzone, deadzone]` to 0 and rescale the rest onto [-1, 1].
pub fn apply_deadzone(value: f64, deadzone: f64) -> Result<f64, NormalizeError> {
    if !value.is_finite() || !deadzone.is_finite() {
        return Err(NormalizeError::NonFinite);
    }
    if !(0.0..1.0).contains(&deadzone) {
        return Err(NormalizeError::DeadzoneRange { deadzone });
    }
    if !(-1.0..=1.0).contains(&value) {
        return Err(NormalizeError::OutOfRange { value });
    }
    Ok(scale_deadzone(value, deadzone))
}

/// Clamp a raw stick reading into [-1, 1], then apply the axial deadzone.
pub fn normalize_stick(raw: f64, deadzone: f64) -> Result<f64, NormalizeError> {
    if !raw.is_finite() || !deadzone.is_finite() {
        return Err(NormalizeError::NonFinite);
    }
    if !(0.0..1.0).contains(&deadzone) {
        return Err(NormalizeError::DeadzoneRange { deadzone });
    }
    let clamped = raw.clamp(-1.0, 1.0);
    Ok(scale_deadzone(clamped, deadzone))
}

fn scale_deadzone(value: f64, deadzone: f64) -> f64 {
    let magnitude = value.abs();
    if magnitude <= deadzone {
        return 0.0;
    }
    let mut scaled = value.signum() * (magnitude - deadzone) / (1.0 - deadzone);
    if scaled > 1.0 && scaled < 1.0 + 1e-9 {
        scaled = 1.0;
    } else if scaled < -1.0 && scaled > -1.0 - 1e-9 {
        scaled = -1.0;
    }
    if scaled.abs() < 1e-12 {
        0.0
    } else {
        scaled
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn zero_deadzone_preserves_sign_and_magnitude() {
        assert_eq!(apply_deadzone(0.0, 0.0).unwrap(), 0.0);
        assert_eq!(apply_deadzone(-0.0, 0.0).unwrap(), 0.0);
        assert_eq!(apply_deadzone(0.25, 0.0).unwrap(), 0.25);
        assert_eq!(apply_deadzone(-1.0, 0.0).unwrap(), -1.0);
    }

    #[test]
    fn deadzone_collapses_and_rescales() {
        assert_eq!(apply_deadzone(0.05, 0.08).unwrap(), 0.0);
        assert_eq!(apply_deadzone(-0.08, 0.08).unwrap(), 0.0);
        assert_eq!(apply_deadzone(1.0, 0.5).unwrap(), 1.0);
        assert_eq!(apply_deadzone(-1.0, 0.5).unwrap(), -1.0);
        let scaled = apply_deadzone(0.5, 0.25).unwrap();
        assert!((scaled - (0.25 / 0.75)).abs() < 1e-12);
        assert!(apply_deadzone(-0.5, 0.25).unwrap() < 0.0);
    }

    #[test]
    fn stick_overshoot_is_clamped_before_the_deadzone() {
        assert_eq!(normalize_stick(1.2, 0.0).unwrap(), 1.0);
        assert_eq!(normalize_stick(-1.4, 0.0).unwrap(), -1.0);
        assert_eq!(normalize_stick(0.05, 0.08).unwrap(), 0.0);
    }

    #[test]
    fn rejects_bad_deadzone_and_nonfinite_input() {
        assert!(apply_deadzone(0.0, 1.0).is_err());
        assert!(apply_deadzone(0.0, -0.1).is_err());
        assert!(apply_deadzone(f64::NAN, 0.0).is_err());
        assert!(normalize_stick(f64::INFINITY, 0.0).is_err());
        assert!(apply_deadzone(1.1, 0.0).is_err());
    }
}
