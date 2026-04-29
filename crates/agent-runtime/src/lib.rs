pub mod arbitration;
pub mod buffering;
pub mod epoch;
pub mod ingress;

pub struct Runtime {
    pub epoch_manager: epoch::EpochManager,
    pub ingress: ingress::EventIngress,
    pub buffering: buffering::EventBuffering,
    pub arbitration: arbitration::ArbitrationUnit,
}
