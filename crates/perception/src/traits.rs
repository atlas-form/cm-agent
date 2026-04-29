use crate::{PerceptionOutput, Stimulus};

pub trait Perception {
    fn perceive(&mut self, input: Stimulus) -> PerceptionOutput;
}
