use std::time::SystemTime;

#[derive(Debug, Clone)]
pub struct Stimulus {
    pub time: SystemTime,
    pub modality: Modality,
    pub signal: Signal,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Modality {
    Language,
    Vision,
    Auditory,
    Tactile,
    Internal,
}

#[derive(Debug, Clone)]
pub enum Signal {
    TextFragment(String),
    VisualPattern(String),
    SoundPattern(String),
    InternalPulse(String),
}
