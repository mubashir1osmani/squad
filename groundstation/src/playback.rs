//! File-time scheduling for CSV samples.
//!
//! Due samples are accepted once. Later polls do not reissue them, so a gap
//! longer than the input timeout cannot be filled by the output loop.

use crate::csv_cmd::CommandSample;

#[derive(Debug, Clone)]
pub struct Playback {
    samples: Vec<CommandSample>,
    cursor: usize,
}

impl Playback {
    pub fn new(samples: Vec<CommandSample>) -> Self {
        Self { samples, cursor: 0 }
    }

    pub fn done(&self) -> bool {
        self.cursor >= self.samples.len()
    }

    pub fn cursor(&self) -> usize {
        self.cursor
    }

    /// Return samples whose `t_us` is at or before `elapsed_us`, in file order.
    pub fn poll(&mut self, elapsed_us: u64) -> Vec<CommandSample> {
        let mut due = Vec::new();
        while self.cursor < self.samples.len() && self.samples[self.cursor].t_us <= elapsed_us {
            due.push(self.samples[self.cursor]);
            self.cursor += 1;
        }
        due
    }
}

/// Offline gap report. A gap larger than `timeout_us` means a live sender
/// must stop holding the earlier sample before the later one arrives.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GapEvent {
    pub t_us: u64,
    pub dt_us: u64,
    pub previous_t_us: u64,
    pub timeout_us: u64,
}

pub fn find_gaps(samples: &[CommandSample], timeout_us: u64) -> Vec<GapEvent> {
    let mut events = Vec::new();
    for pair in samples.windows(2) {
        let dt_us = pair[1].t_us - pair[0].t_us;
        if dt_us > timeout_us {
            events.push(GapEvent {
                t_us: pair[1].t_us,
                dt_us,
                previous_t_us: pair[0].t_us,
                timeout_us,
            });
        }
    }
    events
}

pub fn render_gaps(events: &[GapEvent]) -> String {
    let mut out = String::from("t_us,kind,detail\n");
    for event in events {
        out.push_str(&format!(
            "{},gap,dt_us={};timeout_us={};previous_t_us={}\n",
            event.t_us, event.dt_us, event.timeout_us, event.previous_t_us
        ));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(t_us: u64, enabled: bool) -> CommandSample {
        CommandSample {
            t_us,
            forward: 0.2,
            right: 0.0,
            up: 0.0,
            yaw_cw: 0.0,
            enabled,
        }
    }

    #[test]
    fn polls_each_sample_once() {
        let mut playback = Playback::new(vec![
            sample(0, false),
            sample(20_000, true),
            sample(1_000_000, true),
        ]);
        assert_eq!(playback.poll(0).len(), 1);
        assert!(playback.poll(10).is_empty());
        assert_eq!(playback.poll(20_000)[0].t_us, 20_000);
        assert!(playback.poll(20_000).is_empty());
        assert!(!playback.done());
        assert_eq!(playback.poll(1_000_000).len(), 1);
        assert!(playback.done());
        assert!(playback.poll(2_000_000).is_empty());
    }

    #[test]
    fn reports_only_gaps_beyond_the_timeout() {
        let samples = vec![
            sample(0, true),
            sample(20_000, true),
            sample(400_000, false),
        ];
        let gaps = find_gaps(&samples, 250_000);
        assert_eq!(gaps.len(), 1);
        assert_eq!(gaps[0].previous_t_us, 20_000);
        assert_eq!(gaps[0].t_us, 400_000);
        assert_eq!(gaps[0].dt_us, 380_000);
        let text = render_gaps(&gaps);
        assert!(text.contains("dt_us=380000"));
        assert!(!text.contains('\r'));
    }
}
