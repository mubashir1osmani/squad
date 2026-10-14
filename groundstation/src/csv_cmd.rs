//! Strict reader and canonical writer for `commands.csv`.

use std::fmt;
use std::path::Path;

use crate::normalize::{self, NormalizeError};

pub const HEADER: &str = "t_us,forward,right,up,yaw_cw,enabled";

/// One normalized manual-command sample.
///
/// Axes are finite and inside [-1, 1]. `enabled` is the operator deadman.
/// `t_us` is nonnegative microseconds from the start of one run.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CommandSample {
    pub t_us: u64,
    pub forward: f64,
    pub right: f64,
    pub up: f64,
    pub yaw_cw: f64,
    pub enabled: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CommandLog {
    pub samples: Vec<CommandSample>,
}

#[derive(Debug)]
pub enum CsvError {
    Empty,
    NotUtf8,
    CarriageReturn,
    Bom,
    BlankLine {
        line: usize,
    },
    Header {
        line: usize,
        found: String,
    },
    Columns {
        line: usize,
        found: usize,
    },
    Timestamp {
        line: usize,
        field: String,
    },
    Axis {
        line: usize,
        name: &'static str,
        field: String,
    },
    Range {
        line: usize,
        name: &'static str,
        value: f64,
    },
    Enabled {
        line: usize,
        field: String,
    },
    NotIncreasing {
        line: usize,
        previous: u64,
        value: u64,
    },
    Deadzone(NormalizeError),
    Io(std::io::Error),
}

impl fmt::Display for CsvError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            CsvError::Empty => write!(f, "command CSV is empty"),
            CsvError::NotUtf8 => write!(f, "command CSV is not UTF-8"),
            CsvError::CarriageReturn => write!(f, "command CSV must use LF newlines"),
            CsvError::Bom => write!(f, "command CSV must not start with a BOM"),
            CsvError::BlankLine { line } => write!(f, "line {line}: blank line"),
            CsvError::Header { line, found } => {
                write!(f, "line {line}: header must be `{HEADER}`, found `{found}`")
            }
            CsvError::Columns { line, found } => {
                write!(f, "line {line}: expected 6 unquoted fields, found {found}")
            }
            CsvError::Timestamp { line, field } => {
                write!(
                    f,
                    "line {line}: timestamp `{field}` is not a nonnegative integer"
                )
            }
            CsvError::Axis { line, name, field } => {
                write!(f, "line {line}: {name} `{field}` is not a plain decimal")
            }
            CsvError::Range { line, name, value } => {
                write!(f, "line {line}: {name} {value} is outside [-1, 1]")
            }
            CsvError::Enabled { line, field } => {
                write!(f, "line {line}: enabled `{field}` is not 0 or 1")
            }
            CsvError::NotIncreasing {
                line,
                previous,
                value,
            } => write!(
                f,
                "line {line}: timestamp {value} is not strictly greater than {previous}"
            ),
            CsvError::Deadzone(err) => write!(f, "deadzone: {err}"),
            CsvError::Io(err) => write!(f, "command CSV io: {err}"),
        }
    }
}

impl std::error::Error for CsvError {}

impl From<NormalizeError> for CsvError {
    fn from(value: NormalizeError) -> Self {
        CsvError::Deadzone(value)
    }
}

pub fn read_path(path: &Path) -> Result<CommandLog, CsvError> {
    let bytes = std::fs::read(path).map_err(CsvError::Io)?;
    parse_bytes(&bytes)
}

pub fn parse_bytes(bytes: &[u8]) -> Result<CommandLog, CsvError> {
    if bytes.starts_with(&[0xEF, 0xBB, 0xBF]) {
        return Err(CsvError::Bom);
    }
    let text = std::str::from_utf8(bytes).map_err(|_| CsvError::NotUtf8)?;
    parse_str(text)
}

pub fn parse_str(text: &str) -> Result<CommandLog, CsvError> {
    if text.is_empty() {
        return Err(CsvError::Empty);
    }
    if text.starts_with('\u{feff}') {
        return Err(CsvError::Bom);
    }
    if text.contains('\r') {
        return Err(CsvError::CarriageReturn);
    }
    let mut lines: Vec<&str> = text.split('\n').collect();
    if lines.last().copied() == Some("") {
        lines.pop();
    }
    if lines.is_empty() {
        return Err(CsvError::Empty);
    }
    for (index, line) in lines.iter().enumerate() {
        if line.is_empty() {
            return Err(CsvError::BlankLine { line: index + 1 });
        }
    }
    if lines[0] != HEADER {
        return Err(CsvError::Header {
            line: 1,
            found: lines[0].to_string(),
        });
    }

    let mut samples: Vec<CommandSample> = Vec::new();
    for (index, line) in lines.iter().enumerate().skip(1) {
        let line_no = index + 1;
        let sample = parse_row(line_no, line)?;
        if let Some(previous) = samples.last() {
            if sample.t_us <= previous.t_us {
                return Err(CsvError::NotIncreasing {
                    line: line_no,
                    previous: previous.t_us,
                    value: sample.t_us,
                });
            }
        }
        samples.push(sample);
    }
    Ok(CommandLog { samples })
}

fn parse_row(line: usize, line_text: &str) -> Result<CommandSample, CsvError> {
    if line_text.contains('"') {
        return Err(CsvError::Columns {
            line,
            found: line_text.split(',').count(),
        });
    }
    let fields: Vec<&str> = line_text.split(',').collect();
    if fields.len() != 6 {
        return Err(CsvError::Columns {
            line,
            found: fields.len(),
        });
    }
    let t_us = parse_timestamp(line, fields[0])?;
    let forward = parse_axis(line, "forward", fields[1])?;
    let right = parse_axis(line, "right", fields[2])?;
    let up = parse_axis(line, "up", fields[3])?;
    let yaw_cw = parse_axis(line, "yaw_cw", fields[4])?;
    let enabled = parse_enabled(line, fields[5])?;
    Ok(CommandSample {
        t_us,
        forward,
        right,
        up,
        yaw_cw,
        enabled,
    })
}

fn parse_timestamp(line: usize, field: &str) -> Result<u64, CsvError> {
    if !is_plain_u64(field) {
        return Err(CsvError::Timestamp {
            line,
            field: field.to_string(),
        });
    }
    field.parse::<u64>().map_err(|_| CsvError::Timestamp {
        line,
        field: field.to_string(),
    })
}

fn parse_axis(line: usize, name: &'static str, field: &str) -> Result<f64, CsvError> {
    if !is_plain_decimal(field) {
        return Err(CsvError::Axis {
            line,
            name,
            field: field.to_string(),
        });
    }
    let value: f64 = field.parse().map_err(|_| CsvError::Axis {
        line,
        name,
        field: field.to_string(),
    })?;
    if !value.is_finite() || !(-1.0..=1.0).contains(&value) {
        return Err(CsvError::Range { line, name, value });
    }
    Ok(value)
}

fn parse_enabled(line: usize, field: &str) -> Result<bool, CsvError> {
    match field {
        "0" => Ok(false),
        "1" => Ok(true),
        other => Err(CsvError::Enabled {
            line,
            field: other.to_string(),
        }),
    }
}

fn is_plain_u64(text: &str) -> bool {
    let bytes = text.as_bytes();
    if bytes.is_empty() || !bytes.iter().all(|byte| byte.is_ascii_digit()) {
        return false;
    }
    !(bytes.len() > 1 && bytes[0] == b'0')
}

fn is_plain_decimal(text: &str) -> bool {
    let bytes = text.as_bytes();
    if bytes.is_empty() {
        return false;
    }
    let mut index = 0;
    if bytes[0] == b'-' {
        index = 1;
        if index >= bytes.len() {
            return false;
        }
    }
    if !bytes[index].is_ascii_digit() {
        return false;
    }
    if bytes[index] == b'0' {
        index += 1;
    } else {
        while index < bytes.len() && bytes[index].is_ascii_digit() {
            index += 1;
        }
    }
    if index < bytes.len() && bytes[index] == b'.' {
        index += 1;
        let fraction = index;
        while index < bytes.len() && bytes[index].is_ascii_digit() {
            index += 1;
        }
        if index == fraction {
            return false;
        }
    }
    index == bytes.len()
}

/// Apply an axial deadzone and render the canonical command CSV.
pub fn canonicalize(log: &CommandLog, deadzone: f64) -> Result<String, CsvError> {
    let mut out = String::from(HEADER);
    out.push('\n');
    for sample in &log.samples {
        let forward = normalize::apply_deadzone(sample.forward, deadzone)?;
        let right = normalize::apply_deadzone(sample.right, deadzone)?;
        let up = normalize::apply_deadzone(sample.up, deadzone)?;
        let yaw_cw = normalize::apply_deadzone(sample.yaw_cw, deadzone)?;
        out.push_str(&sample.t_us.to_string());
        out.push(',');
        out.push_str(&format_axis(forward));
        out.push(',');
        out.push_str(&format_axis(right));
        out.push(',');
        out.push_str(&format_axis(up));
        out.push(',');
        out.push_str(&format_axis(yaw_cw));
        out.push(',');
        out.push(if sample.enabled { '1' } else { '0' });
        out.push('\n');
    }
    Ok(out)
}

pub fn format_axis(value: f64) -> String {
    if value == 0.0 {
        "0".to_string()
    } else {
        format!("{value}")
    }
}

pub fn write_atomic(path: &Path, text: &str) -> Result<(), CsvError> {
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty());
    let parent = parent.unwrap_or_else(|| Path::new("."));
    let mut temp_name = path
        .file_name()
        .ok_or_else(|| {
            CsvError::Io(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "output path has no file name",
            ))
        })?
        .to_os_string();
    temp_name.push(".tmp");
    let temp_path = parent.join(temp_name);
    let write_result = (|| {
        let mut file = std::fs::File::create(&temp_path)?;
        std::io::Write::write_all(&mut file, text.as_bytes())?;
        file.sync_all()?;
        std::fs::rename(&temp_path, path)?;
        Ok(())
    })();
    if write_result.is_err() {
        let _ = std::fs::remove_file(&temp_path);
    }
    write_result.map_err(CsvError::Io)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ok(text: &str) -> CommandLog {
        parse_str(text).unwrap()
    }

    #[test]
    fn accepts_spec_example_and_round_trips() {
        let text = "\
t_us,forward,right,up,yaw_cw,enabled
0,0,0,0,0,0
20000,0.2,0,0,0,1
40000,0,0,0,0,0
";
        let log = ok(text);
        assert_eq!(log.samples.len(), 3);
        assert!(!log.samples[0].enabled);
        assert!(log.samples[1].enabled);
        assert_eq!(log.samples[1].forward, 0.2);
        let rendered = canonicalize(&log, 0.0).unwrap();
        assert_eq!(rendered, text);
        assert!(!rendered.contains('\r'));
        assert!(rendered.ends_with('\n'));
    }

    #[test]
    fn canonical_form_trims_and_keeps_disabled_rows() {
        let log = ok("\
t_us,forward,right,up,yaw_cw,enabled
0,0.10,-0.50,1.0,-0.0,0
10,1,-1,0,0.25,1
");
        let rendered = canonicalize(&log, 0.0).unwrap();
        assert_eq!(
            rendered,
            "\
t_us,forward,right,up,yaw_cw,enabled
0,0.1,-0.5,1,0,0
10,1,-1,0,0.25,1
"
        );
        let again = canonicalize(&parse_str(&rendered).unwrap(), 0.0).unwrap();
        assert_eq!(again, rendered);
    }

    #[test]
    fn header_only_is_an_empty_log() {
        let log = ok("t_us,forward,right,up,yaw_cw,enabled\n");
        assert!(log.samples.is_empty());
        assert_eq!(
            canonicalize(&log, 0.0).unwrap(),
            "t_us,forward,right,up,yaw_cw,enabled\n"
        );
    }

    #[test]
    fn deadzone_is_applied_before_write() {
        let log = ok("\
t_us,forward,right,up,yaw_cw,enabled
0,0.05,1,0,-1,1
");
        let rendered = canonicalize(&log, 0.1).unwrap();
        let parsed = parse_str(&rendered).unwrap();
        assert_eq!(parsed.samples[0].forward, 0.0);
        assert_eq!(parsed.samples[0].right, 1.0);
        assert!(parsed.samples[0].enabled);
    }

    #[test]
    fn rejects_malformed_rows() {
        let cases = [
            ("", "empty"),
            ("\u{feff}t_us,forward,right,up,yaw_cw,enabled\n", "BOM"),
            (
                "t_us,forward,right,up,yaw_cw,enabled\r\n0,0,0,0,0,0\n",
                "LF",
            ),
            ("t_us,forward,right,up,yaw_cw,enabled\n\n", "blank"),
            ("time,forward,right,up,yaw_cw,enabled\n", "header"),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n0,0,0,0,0\n",
                "6 unquoted",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n0,0,0,0,0,0,1\n",
                "6 unquoted",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n0,\"0\",0,0,0,0\n",
                "6 unquoted",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n0,nan,0,0,0,0\n",
                "plain decimal",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n0,inf,0,0,0,0\n",
                "plain decimal",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n0,-inf,0,0,0,0\n",
                "plain decimal",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n0,1e-1,0,0,0,0\n",
                "plain decimal",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n0,+0.2,0,0,0,0\n",
                "plain decimal",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n0, 0,0,0,0,0\n",
                "plain decimal",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n0,1.1,0,0,0,0\n",
                "outside",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n0,-1.1,0,0,0,0\n",
                "outside",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n0,0,0,0,0,2\n",
                "0 or 1",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n0,0,0,0,0,0.5\n",
                "0 or 1",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n0,0,0,0,0,true\n",
                "0 or 1",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n-1,0,0,0,0,0\n",
                "nonnegative",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n1.5,0,0,0,0,0\n",
                "nonnegative",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n01,0,0,0,0,0\n",
                "nonnegative",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n0,0,0,0,0,0\n0,0,0,0,0,0\n",
                "strictly greater",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n20,0,0,0,0,0\n10,0,0,0,0,0\n",
                "strictly greater",
            ),
            (
                "t_us,forward,right,up,yaw_cw,enabled\n0,1,000,0,0,0,0\n",
                "6 unquoted",
            ),
        ];
        for (text, needle) in cases {
            let err = parse_str(text).expect_err(text);
            let rendered = err.to_string();
            assert!(
                rendered.contains(needle),
                "case `{text}` produced `{rendered}`, expected `{needle}`"
            );
        }
    }

    #[test]
    fn rejects_non_utf8() {
        let err = parse_bytes(&[0xFF, 0xFE]).unwrap_err();
        assert!(err.to_string().contains("UTF-8"));
    }

    #[test]
    fn accepts_endpoints_and_a_single_zero_timestamp() {
        let log = ok("\
t_us,forward,right,up,yaw_cw,enabled
0,-1,1,-1,1,1
");
        assert_eq!(log.samples[0].t_us, 0);
        assert_eq!(log.samples[0].forward, -1.0);
        assert_eq!(log.samples[0].up, -1.0);
    }

    #[test]
    fn failed_write_leaves_an_existing_file() {
        let dir = std::env::temp_dir().join(format!("quad-gs-csv-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let output = dir.join("out.csv");
        std::fs::write(&output, b"keep\n").unwrap();
        let bad = dir.join("bad.csv");
        std::fs::write(&bad, b"nope\n").unwrap();
        let err = read_path(&bad).unwrap_err();
        assert!(err.to_string().contains("header"));
        assert_eq!(std::fs::read_to_string(&output).unwrap(), "keep\n");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
