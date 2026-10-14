//! Executable checks for replay and the localhost control path.

use std::io::ErrorKind;
use std::net::UdpSocket;
use std::path::PathBuf;
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use quad_groundstation::transport::{decode_frames, manual_control_wire_fields};

fn assert_no_datagram(socket: &UdpSocket) {
    let mut buffer = [0u8; 64];
    let err = socket
        .recv_from(&mut buffer)
        .expect_err("expected no datagram");
    assert!(
        matches!(err.kind(), ErrorKind::TimedOut | ErrorKind::WouldBlock),
        "unexpected recv error {err:?}"
    );
}

fn bin() -> Command {
    Command::new(env!("CARGO_BIN_EXE_quad-groundstation"))
}

fn scratch(name: &str) -> PathBuf {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let dir = std::env::temp_dir().join(format!("quad-gs-{name}-{nanos}"));
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

#[test]
fn replay_writes_the_spec_example_without_opening_a_listener() {
    let receiver = UdpSocket::bind("127.0.0.1:0").unwrap();
    receiver
        .set_read_timeout(Some(Duration::from_millis(150)))
        .unwrap();
    let dir = scratch("replay");
    let output = dir.join("out.csv");
    let events = dir.join("gaps.csv");
    let input = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("examples/commands.csv");
    let status = bin()
        .args([
            "replay",
            "--input",
            input.to_str().unwrap(),
            "--output",
            output.to_str().unwrap(),
            "--events",
            events.to_str().unwrap(),
        ])
        .status()
        .unwrap();
    assert!(status.success());
    let text = std::fs::read_to_string(&output).unwrap();
    assert_eq!(
        text,
        "\
t_us,forward,right,up,yaw_cw,enabled
0,0,0,0,0,0
20000,0.2,0,0,0,1
40000,0,0,0,0,0
"
    );
    assert!(std::fs::read_to_string(&events)
        .unwrap()
        .starts_with("t_us,kind,detail\n"));
    assert_no_datagram(&receiver);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn malformed_replay_keeps_the_existing_output() {
    let dir = scratch("bad-replay");
    let input = dir.join("in.csv");
    let output = dir.join("out.csv");
    std::fs::write(
        &input,
        b"t_us,forward,right,up,yaw_cw,enabled\n0,nan,0,0,0,1\n",
    )
    .unwrap();
    std::fs::write(&output, b"keep\n").unwrap();
    let status = bin()
        .args([
            "replay",
            "--input",
            input.to_str().unwrap(),
            "--output",
            output.to_str().unwrap(),
        ])
        .status()
        .unwrap();
    assert!(!status.success());
    assert_eq!(std::fs::read_to_string(&output).unwrap(), "keep\n");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn control_without_enable_udp_sends_nothing() {
    let receiver = UdpSocket::bind("127.0.0.1:0").unwrap();
    let port = receiver.local_addr().unwrap().port();
    receiver
        .set_read_timeout(Some(Duration::from_millis(200)))
        .unwrap();
    let dir = scratch("no-udp");
    let input = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("examples/loopback.csv");
    let status = bin()
        .args([
            "control",
            "--input",
            input.to_str().unwrap(),
            "--destination",
            &format!("127.0.0.1:{port}"),
        ])
        .status()
        .unwrap();
    assert!(!status.success());
    assert_no_datagram(&receiver);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn control_rejects_a_public_destination() {
    let input = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("examples/loopback.csv");
    let output = bin()
        .args([
            "control",
            "--enable-udp",
            "--input",
            input.to_str().unwrap(),
            "--destination",
            "8.8.8.8:14540",
        ])
        .output()
        .unwrap();
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("loopback"), "{stderr}");
}

#[test]
fn control_loopback_decodes_manual_control_and_then_suppresses() {
    let receiver = UdpSocket::bind("127.0.0.1:0").unwrap();
    let port = receiver.local_addr().unwrap().port();
    receiver
        .set_read_timeout(Some(Duration::from_secs(2)))
        .unwrap();
    let dir = scratch("loop");
    let input = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("examples/loopback.csv");
    let events = dir.join("events.csv");
    let commands = dir.join("commands.csv");
    let telemetry = dir.join("telemetry.csv");
    let status = bin()
        .args([
            "control",
            "--enable-udp",
            "--input",
            input.to_str().unwrap(),
            "--destination",
            &format!("127.0.0.1:{port}"),
            "--bind",
            "127.0.0.1:0",
            "--target-system",
            "1",
            "--rate-hz",
            "50",
            "--timeout-ms",
            "40",
            "--event-log",
            events.to_str().unwrap(),
            "--command-log",
            commands.to_str().unwrap(),
            "--telemetry-log",
            telemetry.to_str().unwrap(),
        ])
        .status()
        .unwrap();
    assert!(status.success(), "control command failed");
    let mut buffer = [0u8; 280];
    let (size, source) = receiver.recv_from(&mut buffer).unwrap();
    assert!(source.ip().is_loopback());
    let wire = manual_control_wire_fields(&buffer[..size]).expect("manual control frame");
    assert_eq!(wire.x, 250);
    assert_eq!(wire.y, -500);
    assert_eq!(wire.z, 500);
    assert_eq!(wire.r, 500);
    assert_eq!(wire.buttons, 0);
    assert_eq!(wire.target, 1);
    let (header, message) = &decode_frames(&buffer[..size])[0];
    assert_eq!(header.system_id, 255);
    assert_eq!(header.component_id, 190);
    assert!(matches!(
        message,
        mavlink::dialects::common::MavMessage::MANUAL_CONTROL(_)
    ));
    let event_text = std::fs::read_to_string(&events).unwrap();
    assert!(event_text.contains("sent,target=1;x=250;y=-500;z=500;r=500;buttons=0"));
    assert!(event_text.contains("reason=stale"));
    let command_text = std::fs::read_to_string(&commands).unwrap();
    assert!(command_text.contains("0,0.25,-0.5,0,0.5,1"));
    assert!(std::fs::read_to_string(&telemetry)
        .unwrap()
        .starts_with("t_mono_us,kind,msgid,sysid,compid,f1,f2,f3,f4,f5,f6,f7\n"));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn gamepad_subcommand_matches_the_compiled_feature() {
    let receiver = UdpSocket::bind("127.0.0.1:0").unwrap();
    let port = receiver.local_addr().unwrap().port();
    receiver
        .set_read_timeout(Some(Duration::from_millis(200)))
        .unwrap();
    let output = bin()
        .args([
            "gamepad",
            "--destination",
            &format!("127.0.0.1:{port}"),
            "--duration-ms",
            "20",
        ])
        .output()
        .unwrap();
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    if cfg!(feature = "gamepad") {
        assert!(
            stderr.contains("UDP is disabled"),
            "feature build should refuse to open a socket without --enable-udp: {stderr}"
        );
    } else {
        assert!(
            stderr.contains("without gamepad"),
            "default build should name the missing feature: {stderr}"
        );
    }
    assert_no_datagram(&receiver);
}

#[test]
fn invalid_control_file_does_not_send() {
    let receiver = UdpSocket::bind("127.0.0.1:0").unwrap();
    let port = receiver.local_addr().unwrap().port();
    receiver
        .set_read_timeout(Some(Duration::from_millis(200)))
        .unwrap();
    let dir = scratch("bad-control");
    let input = dir.join("in.csv");
    std::fs::write(
        &input,
        b"t_us,forward,right,up,yaw_cw,enabled\n0,0,0,0,0,1\n0,0,0,0,0,1\n",
    )
    .unwrap();
    let status = bin()
        .args([
            "control",
            "--enable-udp",
            "--input",
            input.to_str().unwrap(),
            "--destination",
            &format!("127.0.0.1:{port}"),
        ])
        .status()
        .unwrap();
    assert!(!status.success());
    assert_no_datagram(&receiver);
    let _ = std::fs::remove_dir_all(&dir);
}
