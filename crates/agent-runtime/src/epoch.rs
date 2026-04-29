pub type EpochId = u64;

pub enum EpochState {
    Open,
    Closed,
}

pub struct Epoch {
    pub id: EpochId,
    pub state: EpochState,
}

pub struct EpochManager {
    current: Epoch,
}
