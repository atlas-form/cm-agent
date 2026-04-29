use std::time::SystemTime;

use crate::Modality;

#[derive(Debug, Clone)]
pub enum PerceptionOutput {
    Percept(Percept),
    None,
}

#[derive(Debug, Clone)]
pub struct Percept {
    pub modality: Modality,
    pub content: PerceptContent,
    pub span: StimulusSpan,
    pub clarity: f32,
}

#[derive(Debug, Clone)]
pub enum PerceptContent {
    Utterance { text: String },
    VisualObject { description: String },
    FeltState { description: String },
}

#[derive(Debug, Clone)]
pub struct StimulusSpan {
    pub from: SystemTime,
    pub to: SystemTime,
}
