use std::sync::{Arc, OnceLock};

use agent_core::messaging::MessageTx;

use crate::{LlmKey, SharedLlm, WorkerId, WorkerProfile, WorldRuntime};

static WORLD_RUNTIME: OnceLock<Arc<WorldRuntime>> = OnceLock::new();

pub fn init_world() -> Arc<WorldRuntime> {
    let runtime = Arc::new(WorldRuntime::new());
    install_world(runtime.clone());
    runtime
}

pub fn install_world(world: Arc<WorldRuntime>) {
    WORLD_RUNTIME
        .set(world)
        .expect("World runtime already initialized");
}

pub fn world() -> Arc<WorldRuntime> {
    WORLD_RUNTIME
        .get()
        .cloned()
        .expect("World runtime not initialized")
}

pub fn register_commander_tx(tx: MessageTx) {
    world().directory().register_commander_tx(tx);
}

pub fn register_worker_tx(worker_id: WorkerId, tx: MessageTx) {
    world().directory().register_worker_tx(worker_id, tx);
}

pub fn register_worker_profile(profile: WorkerProfile) {
    world().worker_catalog().register(profile);
}

pub fn get_commander_tx() -> Option<MessageTx> {
    world().directory().get_commander_tx()
}

pub fn get_worker_tx(worker_id: &WorkerId) -> Option<MessageTx> {
    world().directory().get_worker_tx(worker_id)
}

pub fn worker_count() -> usize {
    world().directory().worker_count()
}

pub fn get_worker_profile(worker_id: &WorkerId) -> Option<WorkerProfile> {
    world().worker_catalog().get(worker_id)
}

pub fn list_worker_profiles() -> Vec<WorkerProfile> {
    world().worker_catalog().list()
}

pub fn register_llm(key: LlmKey, llm: SharedLlm) {
    world().llm_manager().register(key, llm);
}

pub fn get_llm(key: &LlmKey) -> Option<SharedLlm> {
    world().llm_manager().get(key)
}

pub fn get_default_llm() -> Option<SharedLlm> {
    world().llm_manager().first()
}

pub fn llm_count() -> usize {
    world().llm_manager().len()
}
