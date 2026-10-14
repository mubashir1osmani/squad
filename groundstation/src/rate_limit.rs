//! Bound how often MANUAL_CONTROL frames are emitted.

use std::fmt;

use crate::{DEFAULT_RATE_HZ, MAX_RATE_HZ};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RateLimiter {
    interval_us: u64,
    last_send_us: Option<u64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RateError;

impl fmt::Display for RateError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "send rate must be 1..={MAX_RATE_HZ} Hz")
    }
}

impl std::error::Error for RateError {}

impl RateLimiter {
    pub fn new(rate_hz: u32) -> Result<Self, RateError> {
        if !(1..=MAX_RATE_HZ).contains(&rate_hz) {
            return Err(RateError);
        }
        Ok(Self {
            interval_us: 1_000_000 / u64::from(rate_hz),
            last_send_us: None,
        })
    }

    pub fn interval_us(&self) -> u64 {
        self.interval_us
    }

    pub fn last_send_us(&self) -> Option<u64> {
        self.last_send_us
    }

    /// Record a send when `now_us` is at least one interval after the previous send.
    pub fn try_send(&mut self, now_us: u64) -> bool {
        match self.last_send_us {
            Some(last) if now_us.saturating_sub(last) < self.interval_us => false,
            _ => {
                self.last_send_us = Some(now_us);
                true
            }
        }
    }
}

impl Default for RateLimiter {
    fn default() -> Self {
        Self::new(DEFAULT_RATE_HZ).expect("default rate is inside the allowed range")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bounds_the_send_interval() {
        let mut limiter = RateLimiter::new(20).unwrap();
        assert_eq!(limiter.interval_us(), 50_000);
        assert!(limiter.try_send(0));
        assert!(!limiter.try_send(49_999));
        assert!(limiter.try_send(50_000));
        assert!(!limiter.try_send(50_001));
        assert!(limiter.try_send(100_000));
    }

    #[test]
    fn rejects_zero_and_too_fast() {
        assert!(RateLimiter::new(0).is_err());
        assert!(RateLimiter::new(51).is_err());
        assert!(RateLimiter::new(1).is_ok());
        assert!(RateLimiter::new(50).is_ok());
    }
}
