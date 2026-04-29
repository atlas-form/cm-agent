use std::time::SystemTime;

use crate::{
    Modality, Percept, PerceptContent, Perception, PerceptionOutput, Signal, Stimulus, StimulusSpan,
};

#[derive(Debug, Default, Clone)]
pub struct TextUtterancePerception {
    buffer: String,
    span_start: Option<SystemTime>,
}

impl TextUtterancePerception {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_initial_buffer(buffer: impl Into<String>, started_at: SystemTime) -> Self {
        Self {
            buffer: buffer.into(),
            span_start: Some(started_at),
        }
    }
}

impl Perception for TextUtterancePerception {
    fn perceive(&mut self, input: Stimulus) -> PerceptionOutput {
        if input.modality != Modality::Language {
            return PerceptionOutput::None;
        }

        let fragment = match input.signal {
            Signal::TextFragment(text) => text,
            _ => return PerceptionOutput::None,
        };

        if self.span_start.is_none() {
            self.span_start = Some(input.time);
        }
        self.buffer.push_str(&fragment);

        let trimmed = self.buffer.trim();
        if trimmed.is_empty() {
            return PerceptionOutput::None;
        }

        let ends_sentence =
            trimmed.ends_with('.') || trimmed.ends_with('!') || trimmed.ends_with('?');
        let long_enough = trimmed.len() >= 24;

        if !(ends_sentence || long_enough) {
            return PerceptionOutput::None;
        }

        let span = StimulusSpan {
            from: self.span_start.unwrap_or(input.time),
            to: input.time,
        };

        let text = trimmed.to_string();
        self.buffer.clear();
        self.span_start = None;

        PerceptionOutput::Percept(Percept {
            modality: Modality::Language,
            content: PerceptContent::Utterance { text },
            span,
            clarity: if ends_sentence { 0.9 } else { 0.6 },
        })
    }
}
