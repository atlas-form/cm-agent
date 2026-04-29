use std::{
    collections::HashMap,
    sync::{RwLock, RwLockReadGuard, RwLockWriteGuard},
};

#[derive(Debug, Default)]
pub struct SessionBlackboard {
    inner: RwLock<HashMap<String, String>>,
}

impl SessionBlackboard {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn set(&self, key: impl Into<String>, value: impl Into<String>) {
        let mut map = self.write_inner("set blackboard value");
        map.insert(key.into(), value.into());
    }

    pub fn get(&self, key: &str) -> Option<String> {
        let map = self.read_inner("get blackboard value");
        map.get(key).cloned()
    }

    pub fn remove(&self, key: &str) -> Option<String> {
        let mut map = self.write_inner("remove blackboard value");
        map.remove(key)
    }

    pub fn snapshot(&self) -> HashMap<String, String> {
        self.read_inner("snapshot blackboard").clone()
    }

    fn read_inner(&self, scene: &str) -> RwLockReadGuard<'_, HashMap<String, String>> {
        self.inner
            .read()
            .unwrap_or_else(|_| panic!("session blackboard lock poisoned when {scene}"))
    }

    fn write_inner(&self, scene: &str) -> RwLockWriteGuard<'_, HashMap<String, String>> {
        self.inner
            .write()
            .unwrap_or_else(|_| panic!("session blackboard lock poisoned when {scene}"))
    }
}
