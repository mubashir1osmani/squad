//! End-to-end read-only prediction capture, replay artifacts, and live listener.
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

fn scratch(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "quad-predictions-{name}-{}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir_all(&dir).unwrap();
    dir
}
fn packet(timestamp: u64) -> Value {
    json!({"schema_version":1,"model_id":"test-trajectory","model_mode":"trajectory","source_kind":"software_fixture","position_frame":"NED","rates_frame":"FRD","timestamp_us":timestamp,"prediction_valid":true,"outside_training_range":false,"reason":"ok","observed_position_m":[1.,2.,-3.],"planned_path":[{"target_timestamp_us":timestamp+1000,"position_m":[2.,3.,-3.]}],"predictions":[{"target_timestamp_us":timestamp+1000,"rates_rad_s":[0.1,0.2,0.3],"position_m":[1.9,3.1,-3.],"velocity_m_s":[1.,1.,0.]}]})
}
fn bin(dir: &std::path::Path) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_quad-groundstation"));
    command.args([
        "predictions",
        "--input",
        "-",
        "--capture",
        dir.join("capture.jsonl").to_str().unwrap(),
        "--dashboard",
        dir.join("replay.html").to_str().unwrap(),
    ]);
    command
}
fn feed(mut command: Command, input: &str) -> std::process::Output {
    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(input.as_bytes())
        .unwrap();
    child.wait_with_output().unwrap()
}

#[test]
fn stdin_capture_keeps_explicit_invalid_packets_and_builds_safe_bounded_replay() {
    let dir = scratch("capture");
    let mut invalid = packet(2000);
    invalid["prediction_valid"] = json!(false);
    invalid["reason"] = json!("missing telemetry");
    invalid["predictions"] = json!([]);
    invalid["planned_path"] = json!([]);
    invalid["observed_position_m"] = Value::Null;
    let mut command = bin(&dir);
    command.args(["--max-packets", "1"]);
    let output = feed(command, &format!("{}\n{}\n", packet(1000), invalid));
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let capture = std::fs::read_to_string(dir.join("capture.jsonl")).unwrap();
    assert_eq!(capture.lines().count(), 2);
    assert!(capture.contains("missing telemetry"));
    let replay = std::fs::read_to_string(dir.join("replay.html")).unwrap();
    assert!(replay.contains("\"dropped\":1"));
    assert!(replay.contains("\"prediction_valid\":false"));
    assert!(!replay.contains("__SNAPSHOT_JSON__"));
    assert!(replay.contains("REPLAY · RECORDED TELEMETRY"));
    std::fs::remove_dir_all(dir).unwrap();
}

#[test]
fn rejected_lines_clear_display_but_preserve_valid_capture_and_report_failure() {
    let dir = scratch("reject");
    let output = feed(
        bin(&dir),
        &format!("{}\n{{broken\n{}\n", packet(1000), packet(1000)),
    );
    assert!(!output.status.success());
    assert_eq!(
        std::fs::read_to_string(dir.join("capture.jsonl"))
            .unwrap()
            .lines()
            .count(),
        1
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("line 2: rejected"));
    assert!(stderr.contains("line 3: rejected"));
    let replay = std::fs::read_to_string(dir.join("replay.html")).unwrap();
    assert!(replay.contains("\"packet\":null"));
    assert!(replay.contains("\"rejected\":2"));
    std::fs::remove_dir_all(dir).unwrap();
}

#[test]
fn existing_capture_or_dashboard_is_never_replaced() {
    for existing in ["capture.jsonl", "replay.html"] {
        let dir = scratch("preserve");
        std::fs::write(dir.join(existing), "preserve me").unwrap();
        let output = feed(bin(&dir), &format!("{}\n", packet(1000)));
        assert!(!output.status.success());
        assert_eq!(
            std::fs::read_to_string(dir.join(existing)).unwrap(),
            "preserve me"
        );
        std::fs::remove_dir_all(dir).unwrap();
    }
}

#[test]
fn public_listener_is_rejected_before_creating_outputs() {
    let dir = scratch("public");
    let mut command = bin(&dir);
    command.args(["--serve", "0.0.0.0:8080"]);
    let output = command.output().unwrap();
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("loopback"));
    assert!(!dir.join("capture.jsonl").exists());
    std::fs::remove_dir_all(dir).unwrap();
}

struct Running(Child);
impl Drop for Running {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
fn snapshot(address: &str) -> Value {
    let mut stream = TcpStream::connect(address).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .unwrap();
    stream
        .write_all(b"GET /snapshot HTTP/1.1\r\nHost: localhost\r\n\r\n")
        .unwrap();
    let mut response = String::new();
    stream.read_to_string(&mut response).unwrap();
    assert!(response.starts_with("HTTP/1.1 200 OK"));
    serde_json::from_str(response.split_once("\r\n\r\n").unwrap().1).unwrap()
}
fn wait_for(address: &str, predicate: impl Fn(&Value) -> bool) -> Value {
    let start = Instant::now();
    loop {
        let value = snapshot(address);
        if predicate(&value) {
            return value;
        }
        assert!(
            start.elapsed() < Duration::from_secs(3),
            "last snapshot {value}"
        );
        std::thread::sleep(Duration::from_millis(15));
    }
}

#[test]
fn live_http_blanks_invalid_malformed_stale_and_eof_and_keeps_source_labels() {
    let dir = scratch("live");
    let mut command = bin(&dir);
    command.args(["--serve", "127.0.0.1:0", "--stale-ms", "150"]);
    let child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let mut running = Running(child);
    let mut line = String::new();
    BufReader::new(running.0.stdout.take().unwrap())
        .read_line(&mut line)
        .unwrap();
    let address = line
        .split("http://")
        .nth(1)
        .unwrap()
        .split('/')
        .next()
        .unwrap();
    assert!(snapshot(address)["current"]["packet"].is_null());
    let mut input = running.0.stdin.take().unwrap();
    writeln!(input, "{}", packet(1000)).unwrap();
    input.flush().unwrap();
    let fresh = wait_for(address, |value| value["accepted"] == 1);
    assert_eq!(fresh["identity"]["source_kind"], "software_fixture");
    assert!(!fresh["current"]["packet"].is_null());
    writeln!(input, "{{bad").unwrap();
    input.flush().unwrap();
    let rejected = wait_for(address, |value| value["rejected"] == 1);
    assert!(rejected["current"]["packet"].is_null());
    let mut invalid = packet(2000);
    invalid["prediction_valid"] = json!(false);
    invalid["reason"] = json!("sensor gap");
    invalid["observed_position_m"] = Value::Null;
    invalid["predictions"] = json!([]);
    invalid["planned_path"] = json!([]);
    writeln!(input, "{invalid}").unwrap();
    input.flush().unwrap();
    let invalid = wait_for(address, |value| value["accepted"] == 2);
    assert_eq!(invalid["current"]["packet"]["predictions"], json!([]));
    writeln!(input, "{}", packet(3000)).unwrap();
    input.flush().unwrap();
    wait_for(address, |value| value["accepted"] == 3);
    let stale = wait_for(address, |value| value["status"] == "stale source");
    assert!(stale["current"]["packet"].is_null());
    drop(input);
    let ended = wait_for(address, |value| {
        value["status"] == "end of stream / disconnected"
    });
    assert!(ended["current"]["packet"].is_null());
    #[cfg(unix)]
    {
        assert!(Command::new("kill")
            .args(["-INT", &running.0.id().to_string()])
            .status()
            .unwrap()
            .success());
        let status = running.0.wait().unwrap();
        assert!(!status.success()); // malformed input is a nonzero result, with captures preserved.
        assert!(std::fs::read_to_string(dir.join("replay.html"))
            .unwrap()
            .contains("\"accepted\":3"));
    }
    assert_eq!(
        std::fs::read_to_string(dir.join("capture.jsonl"))
            .unwrap()
            .lines()
            .count(),
        3
    );
    drop(running);
    std::fs::remove_dir_all(dir).unwrap();
}

#[test]
fn capture_preserves_original_precision_and_source_json_bytes() {
    let dir = scratch("precision");
    // Actual exporter value that the default serde_json fast float parser rounded by 1 ULP.
    let source = packet(1000)
        .to_string()
        .replace("0.3", "-0.0037932717241346836")
        .replace(
            "0.1",
            "0.1000000000000000055511151231257827021181583404541015625",
        );
    let input = format!("  {source}  \n");
    let output = feed(bin(&dir), &input);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        std::fs::read_to_string(dir.join("capture.jsonl")).unwrap(),
        input
    );
    std::fs::remove_dir_all(dir).unwrap();
}
