//! Read-only neural telemetry capture and bounded, explicitly labelled dashboards.

use std::collections::VecDeque;
use std::fs::{File, OpenOptions};
use std::io::{self, BufRead, BufReader, Read, Seek, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

pub const MAX_LINE_BYTES: usize = 1024 * 1024;
const MAX_HISTORY_BYTES: usize = 32 * 1024 * 1024;
const DASHBOARD: &str = include_str!("predictions_dashboard.html");
type Error = Box<dyn std::error::Error>;

#[derive(Debug, clap::Args)]
pub struct PredictionArgs {
    /// JSONL file, or - for stdin. No aircraft commands are sent.
    #[arg(long)]
    pub input: PathBuf,
    /// New capture file. Existing files are never replaced.
    #[arg(long)]
    pub capture: PathBuf,
    /// New standalone HTML replay dashboard (latest bounded history).
    #[arg(long)]
    pub dashboard: PathBuf,
    /// Explicitly enable live HTTP on a numeric loopback address. Ctrl-C stops it.
    #[arg(long, value_parser = parse_loopback)]
    pub serve: Option<SocketAddr>,
    /// Receipt freshness limit for live display only; source timestamps stay intact.
    #[arg(long, default_value_t = 1000, value_parser = clap::value_parser!(u64).range(1..=60000))]
    pub stale_ms: u64,
    /// Retain this many events for replay, also capped at 32 MiB. Capture is complete.
    #[arg(long, default_value_t = 2048, value_parser = clap::value_parser!(u32).range(1..=10000))]
    pub max_packets: u32,
}

fn parse_loopback(value: &str) -> Result<SocketAddr, String> {
    let address: SocketAddr = value
        .parse()
        .map_err(|_| "expected numeric loopback IP:port")?;
    if !address.ip().is_loopback() {
        return Err("prediction HTTP listener must be loopback-only".into());
    }
    Ok(address)
}

// deserialize_with requires the field even when its value may be null.
fn required_option<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer)
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PlannedPoint {
    pub target_timestamp_us: u64,
    pub position_m: [f64; 3],
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PredictionPoint {
    pub target_timestamp_us: u64,
    pub rates_rad_s: [f64; 3],
    #[serde(deserialize_with = "required_option")]
    pub position_m: Option<[f64; 3]>,
    #[serde(deserialize_with = "required_option")]
    pub velocity_m_s: Option<[f64; 3]>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Packet {
    pub schema_version: u32,
    pub model_id: String,
    pub model_mode: String,
    pub source_kind: String,
    pub position_frame: String,
    pub rates_frame: String,
    pub timestamp_us: u64,
    pub prediction_valid: bool,
    pub outside_training_range: bool,
    pub reason: String,
    #[serde(deserialize_with = "required_option")]
    pub observed_position_m: Option<[f64; 3]>,
    pub planned_path: Vec<PlannedPoint>,
    pub predictions: Vec<PredictionPoint>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
struct Identity {
    model_id: String,
    model_mode: String,
    source_kind: String,
    position_frame: String,
    rates_frame: String,
}

impl Packet {
    fn identity(&self) -> Identity {
        Identity {
            model_id: self.model_id.clone(),
            model_mode: self.model_mode.clone(),
            source_kind: self.source_kind.clone(),
            position_frame: self.position_frame.clone(),
            rates_frame: self.rates_frame.clone(),
        }
    }

    fn validate(&self) -> Result<(), String> {
        let fail = |message: &str| Err(message.to_string());
        if self.schema_version != 1 {
            return fail("schema_version must be 1");
        }
        if self.model_id.trim().is_empty() || self.model_id.chars().count() > 128 {
            return fail("model_id must contain 1..128 characters");
        }
        if !matches!(self.model_mode.as_str(), "rates" | "trajectory") {
            return fail("model_mode must be rates or trajectory");
        }
        if !matches!(
            self.source_kind.as_str(),
            "real_flight" | "px4_sitl" | "software_fixture" | "unknown"
        ) {
            return fail("unsupported source_kind");
        }
        if self.position_frame != "NED" || self.rates_frame != "FRD" {
            return fail("position_frame/rates_frame must be NED/FRD");
        }
        if !self.prediction_valid {
            if self.reason.trim().is_empty()
                || self.observed_position_m.is_some()
                || !self.predictions.is_empty()
                || !self.planned_path.is_empty()
            {
                return fail("invalid packet needs explanatory reason, null observed position and empty paths");
            }
            return Ok(());
        }
        if !(1..=128).contains(&self.predictions.len()) {
            return fail("valid packet needs 1..128 prediction points");
        }
        let trajectory = self.model_mode == "trajectory";
        if trajectory {
            if !self.observed_position_m.is_some_and(finite)
                || self.planned_path.len() != self.predictions.len()
            {
                return fail(
                    "trajectory requires finite observed position and matching planned points",
                );
            }
        } else if self.observed_position_m.is_some() || !self.planned_path.is_empty() {
            return fail("rates model must have null observed position and empty planned_path");
        }
        let mut previous = self.timestamp_us;
        for (index, point) in self.predictions.iter().enumerate() {
            if point.target_timestamp_us <= previous {
                return fail("prediction targets must strictly increase after the packet anchor");
            }
            previous = point.target_timestamp_us;
            if !finite(point.rates_rad_s) {
                return fail("rates must be finite");
            }
            if trajectory {
                let planned = &self.planned_path[index];
                if !point.position_m.is_some_and(finite)
                    || !point.velocity_m_s.is_some_and(finite)
                    || !finite(planned.position_m)
                    || planned.target_timestamp_us != point.target_timestamp_us
                {
                    return fail("trajectory positions/velocity must be finite; planned target times must match predictions");
                }
            } else if point.position_m.is_some() || point.velocity_m_s.is_some() {
                return fail("rates model must not contain position or velocity predictions");
            }
        }
        Ok(())
    }
}

fn finite(vector: [f64; 3]) -> bool {
    vector.into_iter().all(f64::is_finite)
}

#[derive(Default)]
pub struct Validator {
    identity: Option<Identity>,
    timestamp_us: Option<u64>,
}

impl Validator {
    pub fn parse(&mut self, bytes: &[u8]) -> Result<Packet, String> {
        if bytes.len() > MAX_LINE_BYTES {
            return Err("input line exceeds maximum 1 MiB".into());
        }
        let packet: Packet =
            serde_json::from_slice(bytes).map_err(|err| format!("malformed packet: {err}"))?;
        packet.validate()?;
        if self
            .identity
            .as_ref()
            .is_some_and(|identity| *identity != packet.identity())
        {
            return Err("model ID/mode, source kind and coordinate frames must stay fixed".into());
        }
        if self
            .timestamp_us
            .is_some_and(|timestamp| packet.timestamp_us <= timestamp)
        {
            return Err("packet anchors must strictly increase".into());
        }
        self.identity = Some(packet.identity());
        self.timestamp_us = Some(packet.timestamp_us);
        Ok(packet)
    }
}

// A bounded read also drains oversized lines without retaining their remainder.
fn read_line_bounded(reader: &mut dyn BufRead) -> io::Result<Option<Result<Vec<u8>, String>>> {
    let mut line = Vec::new();
    let mut oversized = false;
    loop {
        let buffer = reader.fill_buf()?;
        if buffer.is_empty() {
            return if line.is_empty() && !oversized {
                Ok(None)
            } else if oversized {
                Ok(Some(Err("input line exceeds maximum 1 MiB".into())))
            } else {
                Ok(Some(Ok(line)))
            };
        }
        let newline = buffer.iter().position(|byte| *byte == b'\n');
        let count = newline.map_or(buffer.len(), |position| position + 1);
        let content = count - usize::from(newline.is_some());
        if line.len().saturating_add(content) > MAX_LINE_BYTES {
            oversized = true;
            line.clear();
        }
        if !oversized {
            line.extend_from_slice(&buffer[..content]);
        }
        reader.consume(count);
        if newline.is_some() {
            return Ok(Some(if oversized {
                Err("input line exceeds maximum 1 MiB".into())
            } else {
                Ok(line)
            }));
        }
    }
}

#[derive(Clone, Serialize)]
struct Event {
    sequence: u64,
    status: String,
    packet: Option<Value>,
}

struct DisplayState {
    events: VecDeque<(Event, usize)>,
    bytes: usize,
    dropped: u64,
    limit: usize,
    accepted: u64,
    rejected: u64,
    identity: Option<Identity>,
    last_receipt: Option<Instant>,
    ended: bool,
    failure: Option<String>,
}

impl DisplayState {
    fn new(limit: usize) -> Self {
        Self {
            events: VecDeque::new(),
            bytes: 0,
            dropped: 0,
            limit,
            accepted: 0,
            rejected: 0,
            identity: None,
            last_receipt: None,
            ended: false,
            failure: None,
        }
    }

    fn push(&mut self, status: String, packet: Option<Packet>) {
        self.last_receipt = Some(Instant::now());
        let packet = packet.map(|packet| {
            self.identity = Some(packet.identity());
            packet_for_display(&packet)
        });
        let event = Event {
            sequence: self.accepted + self.rejected,
            status,
            packet,
        };
        let size = serde_json::to_vec(&event)
            .expect("finite display event")
            .len();
        self.bytes += size;
        self.events.push_back((event, size));
        while self.events.len() > self.limit || self.bytes > MAX_HISTORY_BYTES {
            if let Some((_, size)) = self.events.pop_front() {
                self.bytes -= size;
                self.dropped += 1;
            }
        }
    }

    fn snapshot(&self, live: bool, stale: Duration) -> Value {
        let status = if self.ended {
            self.failure
                .as_deref()
                .unwrap_or("end of stream / disconnected")
        } else if self.last_receipt.is_none() {
            "waiting for input"
        } else if self.last_receipt.is_some_and(|time| time.elapsed() > stale) {
            "stale source"
        } else {
            self.events
                .back()
                .map_or("waiting for input", |(event, _)| event.status.as_str())
        };
        let current = if live
            && (self.ended || self.last_receipt.is_none_or(|time| time.elapsed() > stale))
        {
            json!({"sequence": self.accepted + self.rejected, "status": status, "packet": null})
        } else {
            self.events
                .back()
                .map_or(Value::Null, |(event, _)| json!(event))
        };
        // HTTP polling transfers only the current event and bounded observed anchors.
        let track: Vec<Value> = self
            .events
            .iter()
            .filter_map(|(event, _)| event.packet.as_ref())
            .filter_map(|packet| packet.get("observed_position_m"))
            .filter(|position| !position.is_null())
            .cloned()
            .collect();
        json!({"mode": if live {"live"} else {"replay"}, "identity": self.identity, "status": status, "current": current, "track": track, "accepted": self.accepted, "rejected": self.rejected, "dropped": self.dropped, "frames": if live {Vec::<Event>::new()} else {self.events.iter().map(|(event, _)| event.clone()).collect()}})
    }
}

// Preserve all u64 source-clock precision in JavaScript with decimal strings.
fn packet_for_display(packet: &Packet) -> Value {
    let mut value = serde_json::to_value(packet).expect("validated finite packet");
    value["timestamp_us"] = json!(packet.timestamp_us.to_string());
    for name in ["predictions", "planned_path"] {
        for point in value[name].as_array_mut().expect("point array") {
            let timestamp = point["target_timestamp_us"].as_u64().expect("timestamp");
            point["target_timestamp_us"] = json!(timestamp.to_string());
        }
    }
    value
}

fn html(snapshot: Value, live: bool) -> String {
    // An untrusted model/reason can contain HTML; prevent closing the data script.
    let data = snapshot
        .to_string()
        .replace('&', "\\u0026")
        .replace('<', "\\u003c")
        .replace('>', "\\u003e");
    DASHBOARD
        .replace("__LIVE_MODE__", if live { "true" } else { "false" })
        .replace("__SNAPSHOT_JSON__", &data)
}

fn ingest(
    mut reader: Box<dyn BufRead + Send>,
    mut capture: File,
    shared: &Arc<Mutex<DisplayState>>,
    stop: &AtomicBool,
) -> Result<(), Error> {
    let mut validator = Validator::default();
    let mut line_number = 0u64;
    while !stop.load(Ordering::Relaxed) {
        let Some(line) = read_line_bounded(reader.as_mut())? else {
            break;
        };
        if stop.load(Ordering::Relaxed) {
            break;
        }
        line_number += 1;
        let result = line
            .as_ref()
            .map_err(Clone::clone)
            .and_then(|line| validator.parse(line));
        match result {
            Ok(packet) => {
                capture.write_all(line.as_ref().expect("validated source line"))?;
                capture.write_all(b"\n")?;
                capture.flush()?;
                let mut state = shared.lock().expect("display lock");
                state.accepted += 1;
                let status = if packet.outside_training_range {
                    "outside training range"
                } else {
                    "valid"
                }
                .to_string();
                state.push(
                    if packet.prediction_valid {
                        status
                    } else {
                        format!("invalid prediction: {}", packet.reason)
                    },
                    Some(packet),
                );
            }
            Err(error) => {
                eprintln!("line {line_number}: rejected: {error}");
                let mut state = shared.lock().expect("display lock");
                state.rejected += 1;
                state.push(format!("rejected line {line_number}: {error}"), None);
            }
        }
    }
    capture.sync_all()?;
    Ok(())
}

pub fn run(args: PredictionArgs) -> Result<(), Error> {
    if !(1..=10_000).contains(&args.max_packets) || !(1..=60_000).contains(&args.stale_ms) {
        return Err("max-packets must be 1..10000 and stale-ms must be 1..60000".into());
    }
    if args
        .serve
        .is_some_and(|address| !address.ip().is_loopback())
    {
        return Err("prediction HTTP listener must be loopback-only".into());
    }
    let reader: Box<dyn BufRead + Send> = if args.input.as_os_str() == "-" {
        Box::new(BufReader::new(io::stdin()))
    } else {
        Box::new(BufReader::new(File::open(&args.input)?))
    };
    let capture = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&args.capture)?;
    let mut dashboard = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&args.dashboard)?;
    let shared = Arc::new(Mutex::new(DisplayState::new(args.max_packets as usize)));
    let stop = Arc::new(AtomicBool::new(false));
    let stale = Duration::from_millis(args.stale_ms);
    if let Some(address) = args.serve {
        if !address.ip().is_loopback() {
            return Err("prediction HTTP listener must be loopback-only".into());
        }
        let listener = TcpListener::bind(address)?;
        listener.set_nonblocking(true)?;
        let signal_stop = stop.clone();
        ctrlc::set_handler(move || signal_stop.store(true, Ordering::Relaxed))?;
        let worker_shared = shared.clone();
        let worker_stop = stop.clone();
        // Blocking stdin may stay blocked after Ctrl-C; detached worker exits with process.
        std::thread::spawn(move || {
            let result = ingest(reader, capture, &worker_shared, &worker_stop);
            let mut state = worker_shared.lock().expect("display lock");
            state.ended = true;
            if let Err(error) = result {
                state.failure = Some(format!("input failure: {error}"));
            }
        });
        dashboard.write_all(
            html(
                shared.lock().expect("display lock").snapshot(false, stale),
                false,
            )
            .as_bytes(),
        )?;
        dashboard.flush()?;
        println!(
            "prediction dashboard http://{}/ (read-only live; Ctrl-C to stop)",
            listener.local_addr()?
        );
        io::stdout().flush()?;
        while !stop.load(Ordering::Relaxed) {
            match listener.accept() {
                Ok((stream, _)) => {
                    let _ = serve_request(stream, &shared, stale);
                }
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(10))
                }
                Err(error) => return Err(error.into()),
            }
        }
    } else {
        let result = ingest(reader, capture, &shared, &stop);
        let mut state = shared.lock().expect("display lock");
        state.ended = true;
        if let Err(error) = result {
            state.failure = Some(format!("input failure: {error}"));
        }
    }
    let state = shared.lock().expect("display lock");
    dashboard.rewind()?;
    dashboard.set_len(0)?;
    dashboard.write_all(html(state.snapshot(false, stale), false).as_bytes())?;
    dashboard.sync_all()?;
    println!(
        "prediction capture accepted={} rejected={} replay_events={} omitted={} source={}",
        state.accepted,
        state.rejected,
        state.events.len(),
        state.dropped,
        state
            .identity
            .as_ref()
            .map_or("unknown", |identity| identity.source_kind.as_str())
    );
    if let Some(failure) = &state.failure {
        return Err(failure.clone().into());
    }
    if state.rejected > 0 {
        return Err(format!(
            "{} malformed/contradictory packet(s) rejected; valid capture and replay preserved",
            state.rejected
        )
        .into());
    }
    Ok(())
}

fn serve_request(
    mut stream: TcpStream,
    shared: &Arc<Mutex<DisplayState>>,
    stale: Duration,
) -> io::Result<()> {
    stream.set_read_timeout(Some(Duration::from_millis(100)))?;
    stream.set_write_timeout(Some(Duration::from_millis(100)))?;
    let started = Instant::now();
    let mut request = Vec::new();
    let mut chunk = [0u8; 1024];
    while request.len() < 4096 && !request.windows(4).any(|window| window == b"\r\n\r\n") {
        let count = stream.read(&mut chunk)?;
        if count == 0 {
            break;
        }
        request.extend_from_slice(&chunk[..count]);
        if started.elapsed() > Duration::from_millis(150) {
            return Ok(());
        }
    }
    let first = std::str::from_utf8(&request)
        .unwrap_or("")
        .lines()
        .next()
        .unwrap_or("");
    let (status, content_type, body) = match first {
        "GET / HTTP/1.1" | "GET / HTTP/1.0" => (
            "200 OK",
            "text/html; charset=utf-8",
            html(
                shared.lock().expect("display lock").snapshot(true, stale),
                true,
            ),
        ),
        "GET /snapshot HTTP/1.1" | "GET /snapshot HTTP/1.0" => (
            "200 OK",
            "application/json",
            shared
                .lock()
                .expect("display lock")
                .snapshot(true, stale)
                .to_string(),
        ),
        _ => ("404 Not Found", "text/plain", "not found".to_string()),
    };
    write!(stream, "HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nContent-Security-Policy: default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'none'; frame-ancestors 'none'\r\nConnection: close\r\n\r\n{body}", body.len())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn packet() -> Value {
        json!({"schema_version":1,"model_id":"test-model","model_mode":"trajectory","source_kind":"software_fixture","position_frame":"NED","rates_frame":"FRD","timestamp_us":100,"prediction_valid":true,"outside_training_range":false,"reason":"ok","observed_position_m":[1.,2.,-3.],"planned_path":[{"target_timestamp_us":200,"position_m":[2.,3.,-3.]}],"predictions":[{"target_timestamp_us":200,"rates_rad_s":[0.1,0.2,0.3],"position_m":[1.9,3.1,-3.],"velocity_m_s":[1.,1.,0.]}]})
    }

    fn parse(value: Value) -> Result<Packet, String> {
        Validator::default().parse(&serde_json::to_vec(&value).unwrap())
    }

    #[test]
    fn accepts_trajectory_and_rates_without_fabricated_positions() {
        assert!(parse(packet()).is_ok());
        let mut rates = packet();
        rates["model_mode"] = json!("rates");
        rates["observed_position_m"] = Value::Null;
        rates["planned_path"] = json!([]);
        rates["predictions"][0]["position_m"] = Value::Null;
        rates["predictions"][0]["velocity_m_s"] = Value::Null;
        assert!(parse(rates.clone()).is_ok());
        rates["predictions"][0]["position_m"] = json!([0., 0., 0.]);
        assert!(parse(rates).unwrap_err().contains("must not contain"));
    }

    #[test]
    fn rejects_schema_missing_duplicate_unknown_and_nonfinite_fields() {
        let mut bad = packet();
        bad.as_object_mut().unwrap().remove("observed_position_m");
        assert!(parse(bad).is_err());
        let mut bad = packet();
        bad["unexpected"] = json!(1);
        assert!(parse(bad).is_err());
        for change in ["NaN", "Infinity", "1e999"] {
            let raw = packet().to_string().replace("0.1", change);
            assert!(Validator::default().parse(raw.as_bytes()).is_err());
        }
        let duplicate = packet()
            .to_string()
            .replacen('{', "{\"schema_version\":1,", 1);
        assert!(Validator::default().parse(duplicate.as_bytes()).is_err());
        assert!(Validator::default().parse(b"\xff").is_err());
        let mut bad = packet();
        bad["timestamp_us"] = json!(1.5);
        assert!(parse(bad).is_err());
    }

    #[test]
    fn enforces_anchor_and_target_chronology_and_stream_identity() {
        let mut validator = Validator::default();
        let original = serde_json::to_vec(&packet()).unwrap();
        validator.parse(&original).unwrap();
        assert!(validator.parse(&original).unwrap_err().contains("anchors"));
        for field in [
            "model_id",
            "model_mode",
            "source_kind",
            "position_frame",
            "rates_frame",
        ] {
            let mut changed = packet();
            changed["timestamp_us"] = json!(101);
            changed[field] = json!("contradiction");
            assert!(validator
                .parse(&serde_json::to_vec(&changed).unwrap())
                .is_err());
        }
        let mut changed_source = packet();
        changed_source["timestamp_us"] = json!(101);
        changed_source["source_kind"] = json!("real_flight");
        assert!(validator
            .parse(&serde_json::to_vec(&changed_source).unwrap())
            .unwrap_err()
            .contains("stay fixed"));
        let mut next = packet();
        next["timestamp_us"] = json!(101);
        assert!(validator.parse(&serde_json::to_vec(&next).unwrap()).is_ok());
        for target in [0, 99, 100] {
            let mut bad = packet();
            bad["predictions"][0]["target_timestamp_us"] = json!(target);
            assert!(parse(bad).is_err());
        }
        let mut bad = packet();
        bad["planned_path"][0]["target_timestamp_us"] = json!(201);
        assert!(parse(bad).is_err());
        let mut bad = packet();
        bad["predictions"]
            .as_array_mut()
            .unwrap()
            .push(packet()["predictions"][0].clone());
        bad["planned_path"]
            .as_array_mut()
            .unwrap()
            .push(packet()["planned_path"][0].clone());
        assert!(parse(bad).is_err());
    }

    #[test]
    fn enforces_point_bounds_required_velocity_and_invalid_packet_rules() {
        for count in [0, 129] {
            let mut bad = packet();
            bad["predictions"] = json!(vec![packet()["predictions"][0].clone(); count]);
            assert!(parse(bad).is_err());
        }
        let mut bad = packet();
        bad["predictions"][0]["velocity_m_s"] = Value::Null;
        assert!(parse(bad).is_err());
        let mut invalid = packet();
        invalid["prediction_valid"] = json!(false);
        assert!(parse(invalid.clone()).is_err());
        invalid["observed_position_m"] = Value::Null;
        invalid["planned_path"] = json!([]);
        invalid["predictions"] = json!([]);
        invalid["reason"] = json!("missing telemetry");
        assert!(parse(invalid.clone()).is_ok());
        invalid["reason"] = json!("  ");
        assert!(parse(invalid).is_err());
    }

    #[test]
    fn oversized_reader_drains_and_recovers_with_bounded_allocation() {
        let mut bytes = vec![b'x'; MAX_LINE_BYTES + 5000];
        bytes.extend_from_slice(b"\n{}\n");
        let mut reader = BufReader::with_capacity(4096, Cursor::new(bytes));
        assert!(read_line_bounded(&mut reader).unwrap().unwrap().is_err());
        assert_eq!(
            read_line_bounded(&mut reader).unwrap().unwrap().unwrap(),
            b"{}"
        );
        assert!(read_line_bounded(&mut reader).unwrap().is_none());
        let mut reader = Cursor::new(vec![b' '; MAX_LINE_BYTES]);
        assert_eq!(
            read_line_bounded(&mut reader)
                .unwrap()
                .unwrap()
                .unwrap()
                .len(),
            MAX_LINE_BYTES
        );
    }

    #[test]
    fn live_invalid_stale_and_end_clear_current_and_history_is_bounded() {
        let mut state = DisplayState::new(2);
        state.accepted = 1;
        state.push("valid".into(), Some(parse(packet()).unwrap()));
        assert!(!state.snapshot(true, Duration::from_secs(1))["current"]["packet"].is_null());
        state.last_receipt = Some(Instant::now() - Duration::from_secs(2));
        let stale = state.snapshot(true, Duration::from_secs(1));
        assert!(stale["current"]["packet"].is_null());
        assert_eq!(stale["status"], "stale source");
        state.rejected += 1;
        state.push("malformed".into(), None);
        assert!(state.snapshot(true, Duration::from_secs(1))["current"]["packet"].is_null());
        state.accepted += 1;
        state.push("valid".into(), Some(parse(packet()).unwrap()));
        assert_eq!(state.events.len(), 2);
        assert_eq!(state.dropped, 1);
        state.ended = true;
        assert!(state.snapshot(true, Duration::from_secs(1))["current"]["packet"].is_null());
        assert!(!state.snapshot(false, Duration::from_secs(1))["current"]["packet"].is_null());
    }

    #[test]
    fn html_escapes_untrusted_strings_and_preserves_u64_clock_precision() {
        let mut raw = packet();
        raw["model_id"] = json!("</script><script>alert('bad')</script>");
        raw["timestamp_us"] = json!(u64::MAX - 1);
        raw["predictions"][0]["target_timestamp_us"] = json!(u64::MAX);
        raw["planned_path"][0]["target_timestamp_us"] = json!(u64::MAX);
        let validated = parse(raw).unwrap();
        let value = packet_for_display(&validated);
        assert_eq!(value["timestamp_us"], (u64::MAX - 1).to_string());
        let page = html(value, false);
        assert!(!page.contains("</script><script>alert"));
        assert!(page.contains("\\u003c/script\\u003e"));
        assert!(!page.contains("innerHTML"));
        assert!(page.contains("This model predicts body rates only"));
        assert!(html(json!({"model_id":"__LIVE_MODE__"}), false)
            .contains("\"model_id\":\"__LIVE_MODE__\""));
    }

    #[test]
    fn loopback_binding_parser_and_http_routes_are_read_only() {
        assert!(parse_loopback("0.0.0.0:8000").is_err());
        assert!(parse_loopback("192.168.1.1:8000").is_err());
        assert!(parse_loopback("localhost:8000").is_err());
        assert!(parse_loopback("[::1]:0").is_ok());
        for (path, expected) in [
            ("/snapshot", "200 OK"),
            ("/", "200 OK"),
            ("/arm", "404 Not Found"),
        ] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let shared = Arc::new(Mutex::new(DisplayState::new(2)));
            let address = listener.local_addr().unwrap();
            let worker = std::thread::spawn(move || {
                let (stream, _) = listener.accept().unwrap();
                serve_request(stream, &shared, Duration::from_secs(1)).unwrap();
            });
            let mut client = TcpStream::connect(address).unwrap();
            write!(client, "GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n").unwrap();
            let mut response = String::new();
            client.read_to_string(&mut response).unwrap();
            assert!(response.starts_with(&format!("HTTP/1.1 {expected}")));
            if path == "/snapshot" {
                assert!(response.contains("waiting for input"));
                assert!(response.contains("\"packet\":null"));
            }
            worker.join().unwrap();
        }
    }
}
