//! Localhost-only MAVLink v2 frames.
//!
//! Sockets open only when `enable_udp` is set, and both the bind address and
//! the destination must already be numeric loopback addresses. This module
//! sends `MANUAL_CONTROL` and can decode supported telemetry. It does not send
//! arm, disarm, or takeoff commands.

use std::io::{Cursor, ErrorKind};
use std::net::{SocketAddr, UdpSocket};

use mavlink::dialects::common::{MavMessage, MANUAL_CONTROL_DATA};
use mavlink::{write_v2_msg, MavHeader, MavlinkReader, MavlinkVersion};

use crate::mapping::ManualControlAxes;

pub const GCS_SYSTEM_ID: u8 = 255;
pub const GCS_COMPONENT_ID: u8 = 190;
pub const MANUAL_CONTROL_PAYLOAD_LEN: u8 = 11;

#[derive(Debug)]
pub enum TransportError {
    UdpDisabled,
    NotLoopback {
        role: &'static str,
        addr: SocketAddr,
    },
    Io(std::io::Error),
    Mavlink(String),
}

impl std::fmt::Display for TransportError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TransportError::UdpDisabled => {
                write!(
                    f,
                    "UDP is disabled; pass --enable-udp to open a localhost socket"
                )
            }
            TransportError::NotLoopback { role, addr } => {
                write!(f, "{role} address {addr} is not a numeric loopback address")
            }
            TransportError::Io(err) => write!(f, "UDP error: {err}"),
            TransportError::Mavlink(err) => write!(f, "MAVLink error: {err}"),
        }
    }
}

impl std::error::Error for TransportError {}

#[derive(Debug, Clone, Copy)]
pub struct LinkConfig {
    pub bind: SocketAddr,
    pub destination: SocketAddr,
    pub enable_udp: bool,
    pub source_system: u8,
    pub source_component: u8,
}

#[derive(Debug)]
pub struct LoopbackLink {
    socket: UdpSocket,
    destination: SocketAddr,
    sequence: u8,
    source_system: u8,
    source_component: u8,
}

impl LoopbackLink {
    pub fn open(config: LinkConfig) -> Result<Self, TransportError> {
        if !config.enable_udp {
            return Err(TransportError::UdpDisabled);
        }
        require_loopback("bind", config.bind)?;
        require_loopback("destination", config.destination)?;
        let socket = UdpSocket::bind(config.bind).map_err(TransportError::Io)?;
        socket.set_nonblocking(true).map_err(TransportError::Io)?;
        Ok(Self {
            socket,
            destination: config.destination,
            sequence: 0,
            source_system: config.source_system,
            source_component: config.source_component,
        })
    }

    pub fn local_addr(&self) -> Result<SocketAddr, TransportError> {
        self.socket.local_addr().map_err(TransportError::Io)
    }

    pub fn send_manual(&mut self, axes: ManualControlAxes) -> Result<Vec<u8>, TransportError> {
        let bytes = encode_manual_control(
            axes,
            self.source_system,
            self.source_component,
            self.sequence,
        )?;
        self.sequence = self.sequence.wrapping_add(1);
        self.socket
            .send_to(&bytes, self.destination)
            .map_err(TransportError::Io)?;
        Ok(bytes)
    }

    pub fn poll_frames(&self) -> Result<Vec<(MavHeader, MavMessage)>, TransportError> {
        let mut frames = Vec::new();
        let mut buffer = [0u8; 1024];
        loop {
            match self.socket.recv_from(&mut buffer) {
                Ok((size, source)) => {
                    if !source.ip().is_loopback() {
                        continue;
                    }
                    frames.extend(decode_frames(&buffer[..size]));
                }
                Err(err)
                    if err.kind() == ErrorKind::WouldBlock || err.kind() == ErrorKind::TimedOut =>
                {
                    break;
                }
                Err(err) => return Err(TransportError::Io(err)),
            }
        }
        Ok(frames)
    }
}

pub fn require_loopback(role: &'static str, addr: SocketAddr) -> Result<(), TransportError> {
    if addr.ip().is_loopback() {
        Ok(())
    } else {
        Err(TransportError::NotLoopback { role, addr })
    }
}

pub fn encode_manual_control(
    axes: ManualControlAxes,
    source_system: u8,
    source_component: u8,
    sequence: u8,
) -> Result<Vec<u8>, TransportError> {
    let message = MavMessage::MANUAL_CONTROL(MANUAL_CONTROL_DATA {
        target: axes.target,
        x: axes.x,
        y: axes.y,
        z: axes.z,
        r: axes.r,
        buttons: axes.buttons,
    });
    let header = MavHeader {
        system_id: source_system,
        component_id: source_component,
        sequence,
    };
    let mut buffer = Vec::with_capacity(32);
    write_v2_msg(&mut buffer, header, &message)
        .map_err(|err| TransportError::Mavlink(err.to_string()))?;
    Ok(buffer)
}

pub fn decode_frames(bytes: &[u8]) -> Vec<(MavHeader, MavMessage)> {
    let mut reader = MavlinkReader::new(Cursor::new(bytes));
    let mut frames = Vec::new();
    while let Ok(frame) = reader.read_message::<MavMessage>(MavlinkVersion::V2) {
        frames.push(frame);
    }
    frames
}

/// Send the tick's packet when it has one. A suppressed tick does not touch the socket.
pub fn dispatch(
    link: &mut LoopbackLink,
    axes: Option<ManualControlAxes>,
) -> Result<bool, TransportError> {
    if let Some(axes) = axes {
        link.send_manual(axes)?;
        Ok(true)
    } else {
        Ok(false)
    }
}

/// Payload field offsets for the base `MANUAL_CONTROL` message.
/// MAVLink sorts fields by descending size, so the wire order is x, y, z, r,
/// buttons, target. MAVLink 2 drops trailing zero bytes; a non-zero target
/// keeps the 11-byte payload intact.
pub fn manual_control_wire_fields(frame: &[u8]) -> Option<ManualControlAxes> {
    if frame.first().copied() != Some(mavlink::MAV_STX_V2) || frame.len() < 21 {
        return None;
    }
    if frame[1] != MANUAL_CONTROL_PAYLOAD_LEN {
        return None;
    }
    let msgid = u32::from(frame[7]) | (u32::from(frame[8]) << 8) | (u32::from(frame[9]) << 16);
    if msgid != crate::mapping::MANUAL_CONTROL_MSGID {
        return None;
    }
    Some(ManualControlAxes {
        x: i16::from_le_bytes([frame[10], frame[11]]),
        y: i16::from_le_bytes([frame[12], frame[13]]),
        z: i16::from_le_bytes([frame[14], frame[15]]),
        r: i16::from_le_bytes([frame[16], frame[17]]),
        buttons: u16::from_le_bytes([frame[18], frame[19]]),
        target: frame[20],
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::control::Controller;
    use crate::csv_cmd::CommandSample;
    use crate::mapping::MANUAL_CONTROL_MSGID;
    use mavlink::Message;
    use std::net::UdpSocket;
    use std::time::Duration;

    fn axes() -> ManualControlAxes {
        ManualControlAxes {
            target: 1,
            x: 200,
            y: -500,
            z: 500,
            r: 1000,
            buttons: 0,
        }
    }

    fn config(bind: SocketAddr, destination: SocketAddr, enable_udp: bool) -> LinkConfig {
        LinkConfig {
            bind,
            destination,
            enable_udp,
            source_system: GCS_SYSTEM_ID,
            source_component: GCS_COMPONENT_ID,
        }
    }

    #[test]
    fn encodes_v2_manual_control_and_decodes_the_same_fields() {
        let bytes = encode_manual_control(axes(), GCS_SYSTEM_ID, GCS_COMPONENT_ID, 4).unwrap();
        assert_eq!(bytes[0], 0xFD);
        assert_eq!(bytes[1], MANUAL_CONTROL_PAYLOAD_LEN);
        assert_eq!(bytes[2], 0, "unsigned frame");
        assert_eq!(bytes[4], 4, "sequence");
        assert_eq!(bytes[5], GCS_SYSTEM_ID);
        assert_eq!(bytes[6], GCS_COMPONENT_ID);
        let wire = manual_control_wire_fields(&bytes).unwrap();
        assert_eq!(wire, axes());

        let frames = decode_frames(&bytes);
        assert_eq!(frames.len(), 1);
        let (header, message) = &frames[0];
        assert_eq!(header.system_id, GCS_SYSTEM_ID);
        assert_eq!(header.component_id, GCS_COMPONENT_ID);
        assert_eq!(header.sequence, 4);
        match message {
            MavMessage::MANUAL_CONTROL(data) => {
                assert_eq!(data.x, 200);
                assert_eq!(data.y, -500);
                assert_eq!(data.z, 500);
                assert_eq!(data.r, 1000);
                assert_eq!(data.buttons, 0);
                assert_eq!(data.target, 1);
                assert_eq!(message.message_id(), MANUAL_CONTROL_MSGID);
            }
            other => panic!("expected MANUAL_CONTROL, got {other:?}"),
        }
    }

    #[test]
    fn refuses_udp_without_the_flag_and_refuses_non_loopback() {
        let bind: SocketAddr = "127.0.0.1:0".parse().unwrap();
        let destination: SocketAddr = "127.0.0.1:9".parse().unwrap();
        let err = LoopbackLink::open(config(bind, destination, false)).unwrap_err();
        assert!(matches!(err, TransportError::UdpDisabled));

        let public: SocketAddr = "8.8.8.8:14540".parse().unwrap();
        let err = LoopbackLink::open(config(bind, public, true)).unwrap_err();
        assert!(matches!(
            err,
            TransportError::NotLoopback {
                role: "destination",
                ..
            }
        ));

        let wildcard: SocketAddr = "0.0.0.0:14540".parse().unwrap();
        let err = LoopbackLink::open(config(wildcard, destination, true)).unwrap_err();
        assert!(matches!(
            err,
            TransportError::NotLoopback { role: "bind", .. }
        ));
    }

    #[test]
    fn localhost_exchange_decodes_fields_and_disabled_input_sends_nothing() {
        let receiver = UdpSocket::bind("127.0.0.1:0").unwrap();
        let destination = receiver.local_addr().unwrap();
        receiver
            .set_read_timeout(Some(Duration::from_millis(200)))
            .unwrap();
        let mut link =
            LoopbackLink::open(config("127.0.0.1:0".parse().unwrap(), destination, true)).unwrap();

        let mut controller = Controller::new(250_000, 20, 1).unwrap();
        controller.push_sample(
            CommandSample {
                t_us: 0,
                forward: 0.2,
                right: -0.5,
                up: 0.0,
                yaw_cw: 1.0,
                enabled: true,
            },
            0,
            true,
        );
        let outcome = controller.tick(0).unwrap();
        assert!(dispatch(&mut link, outcome.packet).unwrap());

        let mut buffer = [0u8; 280];
        let (size, source) = receiver.recv_from(&mut buffer).unwrap();
        assert!(source.ip().is_loopback());
        let wire = manual_control_wire_fields(&buffer[..size]).unwrap();
        assert_eq!(wire.x, 200);
        assert_eq!(wire.y, -500);
        assert_eq!(wire.z, 500);
        assert_eq!(wire.r, 1000);
        assert_eq!(wire.buttons, 0);
        assert_eq!(wire.target, 1);
        let (header, message) = &decode_frames(&buffer[..size])[0];
        assert_eq!(header.sequence, 0);
        assert!(matches!(message, MavMessage::MANUAL_CONTROL(_)));

        controller.push_sample(
            CommandSample {
                t_us: 20_000,
                forward: 1.0,
                right: 0.0,
                up: 1.0,
                yaw_cw: 0.0,
                enabled: false,
            },
            20_000,
            true,
        );
        let suppressed = controller.tick(20_000).unwrap();
        assert!(!dispatch(&mut link, suppressed.packet).unwrap());
        let err = receiver.recv_from(&mut buffer).unwrap_err();
        assert!(
            matches!(err.kind(), ErrorKind::TimedOut | ErrorKind::WouldBlock),
            "disabled input must not produce a datagram, recv returned {err:?}"
        );

        let stale_input_arrival = controller.input_arrived_us().unwrap();
        for now in [20_001, 300_000, 1_000_000] {
            let outcome = controller.tick(now).unwrap();
            assert!(!dispatch(&mut link, outcome.packet).unwrap());
            assert_eq!(controller.input_arrived_us(), Some(stale_input_arrival));
        }
    }
}
