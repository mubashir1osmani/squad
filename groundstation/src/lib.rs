//! Ground station library: command CSV, stick normalization, and localhost MAVLink.
//!
//! The default build has no gamepad dependency and does not open a socket.
//! UDP output exists only on the explicit control path.

#![forbid(unsafe_code)]

pub mod control;
pub mod csv_cmd;
pub mod freshness;
pub mod gamepad;
#[cfg(feature = "gamepad")]
pub mod gamepad_io;
pub mod mapping;
pub mod normalize;
pub mod playback;
pub mod rate_limit;
pub mod replay;
pub mod session;
pub mod telemetry;
pub mod transport;

/// True when this binary was built with `--features gamepad`.
pub const GAMEPAD_FEATURE: bool = cfg!(feature = "gamepad");

/// Default manual-input timeout from the shared interface: 250 ms.
pub const DEFAULT_TIMEOUT_US: u64 = 250_000;

/// Default MANUAL_CONTROL send rate.
pub const DEFAULT_RATE_HZ: u32 = 20;

/// Inclusive maximum send rate. Keeps the localhost stream bounded.
pub const MAX_RATE_HZ: u32 = 50;

/// PX4 SITL offboard link: PX4 sends telemetry to this port.
/// See ROMFS `px4-rc.mavlink` (`udp_offboard_port_remote`).
pub const SITL_OFFBOARD_BIND: &str = "127.0.0.1:14540";

/// PX4 SITL offboard link: PX4 listens on this port.
/// See ROMFS `px4-rc.mavlink` (`udp_offboard_port_local`).
pub const SITL_OFFBOARD_DESTINATION: &str = "127.0.0.1:14580";

#[cfg(test)]
mod tests {
    #[test]
    fn published_defaults_match_the_contract() {
        assert_eq!(crate::DEFAULT_TIMEOUT_US, 250_000);
        const _: () =
            assert!(crate::DEFAULT_RATE_HZ >= 1 && crate::DEFAULT_RATE_HZ <= crate::MAX_RATE_HZ);
        assert!(crate::SITL_OFFBOARD_BIND.starts_with("127.0.0.1:"));
        assert!(crate::SITL_OFFBOARD_DESTINATION.starts_with("127.0.0.1:"));
        assert_eq!(crate::GAMEPAD_FEATURE, cfg!(feature = "gamepad"));
    }
}
