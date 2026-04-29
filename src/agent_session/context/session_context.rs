use crate::{
    agent::commander::CommanderSessionContext,
    context::{EndpointDirectory, SessionBlackboard, SessionExtensions, WorkerCatalog},
    core::{
        messaging::MessageTx,
        protocol::{WorkerId, WorkerProfile},
    },
};

#[derive(Debug, Default)]
pub struct SessionContext {
    directory: EndpointDirectory,
    blackboard: SessionBlackboard,
    worker_catalog: WorkerCatalog,
    extensions: SessionExtensions,
}

impl SessionContext {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn directory(&self) -> &EndpointDirectory {
        &self.directory
    }

    pub fn blackboard(&self) -> &SessionBlackboard {
        &self.blackboard
    }

    pub fn worker_catalog(&self) -> &WorkerCatalog {
        &self.worker_catalog
    }

    pub fn extensions(&self) -> &SessionExtensions {
        &self.extensions
    }
}

impl CommanderSessionContext for SessionContext {
    fn get_worker_tx(&self, worker_id: &WorkerId) -> Option<MessageTx> {
        self.directory.get_worker_tx(worker_id)
    }

    fn list_worker_profiles(&self) -> Vec<WorkerProfile> {
        self.worker_catalog.list()
    }
}
