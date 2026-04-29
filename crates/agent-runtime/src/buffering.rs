pub struct PerceptionBuffer;

pub struct CognitionResultBuffer;

pub struct ActionRegistry;

pub struct EventBuffering {
    pub perception: PerceptionBuffer,
    pub cognition: CognitionResultBuffer,
    pub actions: ActionRegistry,
}
