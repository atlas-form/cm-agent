use std::{
    collections::HashMap,
    sync::{RwLock, RwLockReadGuard, RwLockWriteGuard},
};

use agent_core::{messaging::MessageTx, protocol::WorkerId};

#[derive(Debug, Default)]
struct EndpointDirectoryInner {
    commander_tx: Option<MessageTx>,
    workers: HashMap<WorkerId, MessageTx>,
}

#[derive(Debug, Default)]
pub struct EndpointDirectory {
    inner: RwLock<EndpointDirectoryInner>,
}

impl EndpointDirectory {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn register_commander_tx(&self, tx: MessageTx) {
        let mut inner = self.write_inner("register commander tx");
        inner.commander_tx = Some(tx);
    }

    pub fn register_worker_tx(&self, worker_id: WorkerId, tx: MessageTx) {
        let mut inner = self.write_inner("register worker tx");
        inner.workers.insert(worker_id, tx);
    }

    pub fn remove_worker_tx(&self, worker_id: &WorkerId) -> Option<MessageTx> {
        let mut inner = self.write_inner("remove worker tx");
        inner.workers.remove(worker_id)
    }

    pub fn get_commander_tx(&self) -> Option<MessageTx> {
        let inner = self.read_inner("get commander tx");
        inner.commander_tx.clone()
    }

    pub fn get_worker_tx(&self, worker_id: &WorkerId) -> Option<MessageTx> {
        let inner = self.read_inner("get worker tx");
        inner.workers.get(worker_id).cloned()
    }

    pub fn worker_count(&self) -> usize {
        let inner = self.read_inner("get worker count");
        inner.workers.len()
    }

    fn read_inner(&self, scene: &str) -> RwLockReadGuard<'_, EndpointDirectoryInner> {
        self.inner
            .read()
            .unwrap_or_else(|_| panic!("session context directory lock poisoned when {scene}"))
    }

    fn write_inner(&self, scene: &str) -> RwLockWriteGuard<'_, EndpointDirectoryInner> {
        self.inner
            .write()
            .unwrap_or_else(|_| panic!("session context directory lock poisoned when {scene}"))
    }
}
