use std::{
    collections::HashMap,
    sync::{RwLock, RwLockReadGuard, RwLockWriteGuard},
};

use agent_core::protocol::{WorkerId, WorkerProfile};

#[derive(Debug, Default)]
struct WorkerCatalogInner {
    workers: HashMap<WorkerId, WorkerProfile>,
}

#[derive(Debug, Default)]
pub struct WorkerCatalog {
    inner: RwLock<WorkerCatalogInner>,
}

impl WorkerCatalog {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn register(&self, profile: WorkerProfile) {
        let mut inner = self.write_inner("register worker profile");
        inner.workers.insert(profile.worker_id.clone(), profile);
    }

    pub fn get(&self, worker_id: &WorkerId) -> Option<WorkerProfile> {
        let inner = self.read_inner("get worker profile");
        inner.workers.get(worker_id).cloned()
    }

    pub fn list(&self) -> Vec<WorkerProfile> {
        let inner = self.read_inner("list worker profiles");
        let mut workers = inner.workers.values().cloned().collect::<Vec<_>>();
        workers.sort_by(|left, right| left.worker_id.0.cmp(&right.worker_id.0));
        workers
    }

    fn read_inner(&self, scene: &str) -> RwLockReadGuard<'_, WorkerCatalogInner> {
        self.inner
            .read()
            .unwrap_or_else(|_| panic!("worker catalog lock poisoned when {scene}"))
    }

    fn write_inner(&self, scene: &str) -> RwLockWriteGuard<'_, WorkerCatalogInner> {
        self.inner
            .write()
            .unwrap_or_else(|_| panic!("worker catalog lock poisoned when {scene}"))
    }
}

#[cfg(test)]
mod tests {
    use agent_core::protocol::{WorkerId, WorkerProfile};

    use super::WorkerCatalog;

    #[test]
    fn keeps_profiles_sorted_by_worker_id() {
        let catalog = WorkerCatalog::new();
        catalog.register(WorkerProfile {
            worker_id: WorkerId("worker-b".to_string()),
            agent_id: "worker-b".to_string(),
            name: "B".to_string(),
            description: "second".to_string(),
            capabilities: vec!["general".to_string()],
            constraints: vec![],
            status: "ready".to_string(),
        });
        catalog.register(WorkerProfile {
            worker_id: WorkerId("worker-a".to_string()),
            agent_id: "worker-a".to_string(),
            name: "A".to_string(),
            description: "first".to_string(),
            capabilities: vec!["analysis".to_string()],
            constraints: vec![],
            status: "ready".to_string(),
        });

        let workers = catalog.list();

        assert_eq!(workers.len(), 2);
        assert_eq!(workers[0].worker_id.0, "worker-a");
        assert_eq!(workers[1].worker_id.0, "worker-b");
    }
}
