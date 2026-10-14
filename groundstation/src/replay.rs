//! Offline replay. This path reads and writes files and does not open sockets.

use std::path::Path;

use crate::csv_cmd::{self, CsvError};
use crate::playback::{self, GapEvent};

#[derive(Debug, Clone, Copy)]
pub struct ReplayOptions {
    pub deadzone: f64,
    pub timeout_us: u64,
}

impl Default for ReplayOptions {
    fn default() -> Self {
        Self {
            deadzone: 0.0,
            timeout_us: crate::DEFAULT_TIMEOUT_US,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReplayProduct {
    pub commands_csv: String,
    pub gap_events_csv: String,
    pub gaps: Vec<GapEvent>,
}

pub fn replay_bytes(bytes: &[u8], options: &ReplayOptions) -> Result<ReplayProduct, CsvError> {
    let log = csv_cmd::parse_bytes(bytes)?;
    finish(log, options)
}

pub fn replay_str(text: &str, options: &ReplayOptions) -> Result<ReplayProduct, CsvError> {
    let log = csv_cmd::parse_str(text)?;
    finish(log, options)
}

fn finish(log: csv_cmd::CommandLog, options: &ReplayOptions) -> Result<ReplayProduct, CsvError> {
    let commands_csv = csv_cmd::canonicalize(&log, options.deadzone)?;
    let gaps = playback::find_gaps(&log.samples, options.timeout_us);
    let gap_events_csv = playback::render_gaps(&gaps);
    Ok(ReplayProduct {
        commands_csv,
        gap_events_csv,
        gaps,
    })
}

pub fn replay_paths(
    input: &Path,
    output: &Path,
    events: Option<&Path>,
    options: &ReplayOptions,
) -> Result<ReplayProduct, CsvError> {
    if input == output || events.is_some_and(|path| path == input) {
        return Err(CsvError::Io(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "output paths must differ from the input path",
        )));
    }
    let bytes = std::fs::read(input).map_err(CsvError::Io)?;
    let product = replay_bytes(&bytes, options)?;
    csv_cmd::write_atomic(output, &product.commands_csv)?;
    if let Some(path) = events {
        csv_cmd::write_atomic(path, &product.gap_events_csv)?;
    }
    Ok(product)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn replay_is_canonical_and_reports_a_hold_gap() {
        let text = "\
t_us,forward,right,up,yaw_cw,enabled
0,0.2,0,0,0,1
20000,0.2,0,0,0,1
400000,0,0,0,0,0
";
        let product = replay_str(text, &ReplayOptions::default()).unwrap();
        assert_eq!(product.gaps.len(), 1);
        assert!(product.gap_events_csv.contains("gap"));
        assert!(product.gap_events_csv.contains("dt_us=380000"));
        assert!(product.commands_csv.contains("400000,0,0,0,0,0\n"));
        let again = replay_str(&product.commands_csv, &ReplayOptions::default()).unwrap();
        assert_eq!(again.commands_csv, product.commands_csv);
    }

    #[test]
    fn invalid_input_does_not_replace_an_existing_output() {
        let dir = std::env::temp_dir().join(format!("quad-gs-replay-{}", unique()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let input = dir.join("in.csv");
        let output = dir.join("out.csv");
        std::fs::write(&input, b"bad\n").unwrap();
        std::fs::write(&output, b"keep\n").unwrap();
        let err = replay_paths(&input, &output, None, &ReplayOptions::default()).unwrap_err();
        assert!(err.to_string().contains("header"));
        assert_eq!(std::fs::read_to_string(&output).unwrap(), "keep\n");
        let _ = std::fs::remove_dir_all(&dir);
    }

    fn unique() -> u128 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    }
}
