//! Decode the telemetry this milestone records: HEARTBEAT and ATTITUDE.
//!
//! Other messages are counted and ignored. Nonfinite attitude values are not
//! written into the CSV.

use mavlink::dialects::common::{MavMessage, ATTITUDE_DATA, HEARTBEAT_DATA};
use mavlink::{MavHeader, Message};

#[derive(Debug, Clone, PartialEq)]
pub enum TelemetryKind {
    Heartbeat {
        mavtype: u32,
        autopilot: u32,
        base_mode: u8,
        custom_mode: u32,
        system_status: u32,
        mavlink_version: u8,
    },
    Attitude {
        time_boot_ms: u32,
        roll: f32,
        pitch: f32,
        yaw: f32,
        rollspeed: f32,
        pitchspeed: f32,
        yawspeed: f32,
    },
}

#[derive(Debug, Clone, PartialEq)]
pub struct TelemetryRow {
    pub t_mono_us: u64,
    pub msgid: u32,
    pub sysid: u8,
    pub compid: u8,
    pub kind: TelemetryKind,
}

pub fn classify(header: &MavHeader, message: &MavMessage, t_mono_us: u64) -> Option<TelemetryRow> {
    let kind = match message {
        MavMessage::HEARTBEAT(data) => heartbeat_kind(data)?,
        MavMessage::ATTITUDE(data) => attitude_kind(data)?,
        _ => return None,
    };
    Some(TelemetryRow {
        t_mono_us,
        msgid: message.message_id(),
        sysid: header.system_id,
        compid: header.component_id,
        kind,
    })
}

fn heartbeat_kind(data: &HEARTBEAT_DATA) -> Option<TelemetryKind> {
    Some(TelemetryKind::Heartbeat {
        mavtype: data.mavtype as u32,
        autopilot: data.autopilot as u32,
        base_mode: data.base_mode.bits(),
        custom_mode: data.custom_mode,
        system_status: data.system_status as u32,
        mavlink_version: data.mavlink_version,
    })
}

fn attitude_kind(data: &ATTITUDE_DATA) -> Option<TelemetryKind> {
    let values = [
        data.roll,
        data.pitch,
        data.yaw,
        data.rollspeed,
        data.pitchspeed,
        data.yawspeed,
    ];
    if values.iter().any(|value| !value.is_finite()) {
        return None;
    }
    Some(TelemetryKind::Attitude {
        time_boot_ms: data.time_boot_ms,
        roll: data.roll,
        pitch: data.pitch,
        yaw: data.yaw,
        rollspeed: data.rollspeed,
        pitchspeed: data.pitchspeed,
        yawspeed: data.yawspeed,
    })
}

pub fn render(rows: &[TelemetryRow]) -> String {
    let mut out = String::from("t_mono_us,kind,msgid,sysid,compid,f1,f2,f3,f4,f5,f6,f7\n");
    for row in rows {
        out.push_str(&row.t_mono_us.to_string());
        out.push(',');
        match &row.kind {
            TelemetryKind::Heartbeat {
                mavtype,
                autopilot,
                base_mode,
                custom_mode,
                system_status,
                mavlink_version,
            } => {
                out.push_str(&format!(
                    "heartbeat,{},{},{},{},{},{},{},{},{},0\n",
                    row.msgid,
                    row.sysid,
                    row.compid,
                    mavtype,
                    autopilot,
                    base_mode,
                    custom_mode,
                    system_status,
                    mavlink_version
                ));
            }
            TelemetryKind::Attitude {
                time_boot_ms,
                roll,
                pitch,
                yaw,
                rollspeed,
                pitchspeed,
                yawspeed,
            } => {
                out.push_str(&format!(
                    "attitude,{},{},{},{},{},{},{},{},{},{}\n",
                    row.msgid,
                    row.sysid,
                    row.compid,
                    time_boot_ms,
                    format_float(*roll),
                    format_float(*pitch),
                    format_float(*yaw),
                    format_float(*rollspeed),
                    format_float(*pitchspeed),
                    format_float(*yawspeed)
                ));
            }
        }
    }
    debug_assert!(!out.contains("NaN") && !out.contains("inf"));
    out
}

fn format_float(value: f32) -> String {
    let value = f64::from(value);
    if value == 0.0 {
        "0".to_string()
    } else {
        format!("{value}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use mavlink::dialects::common::{MavAutopilot, MavModeFlag, MavState, MavType};
    use mavlink::{write_v2_msg, MavHeader, MavlinkVersion};

    fn round_trip(message: MavMessage) -> (MavHeader, MavMessage) {
        let header = MavHeader {
            system_id: 1,
            component_id: 1,
            sequence: 2,
        };
        let mut bytes = Vec::new();
        write_v2_msg(&mut bytes, header, &message).unwrap();
        let frames = crate::transport::decode_frames(&bytes);
        assert_eq!(frames.len(), 1);
        frames.into_iter().next().unwrap()
    }

    #[test]
    fn records_heartbeat_and_attitude_and_drops_nonfinite_attitude() {
        let (header, heartbeat) = round_trip(MavMessage::HEARTBEAT(HEARTBEAT_DATA {
            custom_mode: 0x0004_0000,
            mavtype: MavType::MAV_TYPE_QUADROTOR,
            autopilot: MavAutopilot::MAV_AUTOPILOT_PX4,
            base_mode: MavModeFlag::MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            system_status: MavState::MAV_STATE_STANDBY,
            mavlink_version: 3,
        }));
        let row = classify(&header, &heartbeat, 15).unwrap();
        assert_eq!(row.msgid, 0);
        assert_eq!(row.sysid, 1);
        match row.kind {
            TelemetryKind::Heartbeat {
                custom_mode,
                mavlink_version,
                autopilot,
                ..
            } => {
                assert_eq!(custom_mode, 0x0004_0000);
                assert_eq!(mavlink_version, 3);
                assert_eq!(autopilot, MavAutopilot::MAV_AUTOPILOT_PX4 as u32);
            }
            other => panic!("expected heartbeat, got {other:?}"),
        }

        let (header, attitude) = round_trip(MavMessage::ATTITUDE(ATTITUDE_DATA {
            time_boot_ms: 40,
            roll: 0.25,
            pitch: -0.5,
            yaw: 0.0,
            rollspeed: 0.0,
            pitchspeed: 1.0,
            yawspeed: -1.0,
        }));
        let row = classify(&header, &attitude, 80).unwrap();
        assert_eq!(row.msgid, 30);
        let text = render(&[row]);
        assert!(text.contains("attitude,30,1,1,40,"));
        assert!(text.contains(",-0.5,"));
        assert!(!text.contains('\r'));

        let (header, bad) = round_trip(MavMessage::ATTITUDE(ATTITUDE_DATA {
            time_boot_ms: 1,
            roll: f32::NAN,
            pitch: 0.0,
            yaw: 0.0,
            rollspeed: 0.0,
            pitchspeed: 0.0,
            yawspeed: 0.0,
        }));
        assert!(classify(&header, &bad, 1).is_none());
        let _ = MavlinkVersion::V2;
    }
}
