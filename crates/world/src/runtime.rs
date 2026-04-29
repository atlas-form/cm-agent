use crate::{EndpointDirectory, LlmManager, WorkerCatalog, WorldBlackboard, WorldExtensions};

#[derive(Debug, Default)]
pub struct WorldRuntime {
    directory: EndpointDirectory,
    worker_catalog: WorkerCatalog,
    blackboard: WorldBlackboard,
    llm_manager: LlmManager,
    extensions: WorldExtensions,
}

impl WorldRuntime {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn directory(&self) -> &EndpointDirectory {
        &self.directory
    }

    pub fn blackboard(&self) -> &WorldBlackboard {
        &self.blackboard
    }

    pub fn worker_catalog(&self) -> &WorkerCatalog {
        &self.worker_catalog
    }

    pub fn llm_manager(&self) -> &LlmManager {
        &self.llm_manager
    }

    pub fn extensions(&self) -> &WorldExtensions {
        &self.extensions
    }
}
