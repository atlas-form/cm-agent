use std::{
    cmp::Ordering,
    collections::HashMap,
    sync::{Arc, RwLock},
};

use agent_llm::llm::Llm;

pub type SharedLlm = Arc<dyn Llm + Send + Sync>;

#[derive(Debug, Clone, Hash, PartialEq, Eq)]
pub struct LlmKey {
    pub provider: String,
    pub model: String,
    pub endpoint: String,
    pub profile: String,
}

impl LlmKey {
    pub fn new(
        provider: impl Into<String>,
        model: impl Into<String>,
        endpoint: impl Into<String>,
        profile: impl Into<String>,
    ) -> Self {
        Self {
            provider: provider.into(),
            model: model.into(),
            endpoint: endpoint.into(),
            profile: profile.into(),
        }
    }
}

#[derive(Default)]
pub struct LlmManager {
    inner: RwLock<HashMap<LlmKey, SharedLlm>>,
}

impl std::fmt::Debug for LlmManager {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LlmManager").finish_non_exhaustive()
    }
}

impl LlmManager {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn register(&self, key: LlmKey, llm: SharedLlm) {
        let mut inner = self
            .inner
            .write()
            .expect("llm manager lock poisoned when register");
        inner.insert(key, llm);
    }

    pub fn get(&self, key: &LlmKey) -> Option<SharedLlm> {
        let inner = self
            .inner
            .read()
            .expect("llm manager lock poisoned when get");
        inner.get(key).cloned()
    }

    pub fn first(&self) -> Option<SharedLlm> {
        let inner = self
            .inner
            .read()
            .expect("llm manager lock poisoned when first");

        inner
            .iter()
            .filter(|(key, _)| key.profile == "default")
            .min_by(|(left, _), (right, _)| compare_llm_key(left, right))
            .or_else(|| {
                inner
                    .iter()
                    .min_by(|(left, _), (right, _)| compare_llm_key(left, right))
            })
            .map(|(_, llm)| llm.clone())
    }

    pub fn contains(&self, key: &LlmKey) -> bool {
        let inner = self
            .inner
            .read()
            .expect("llm manager lock poisoned when contains");
        inner.contains_key(key)
    }

    pub fn get_or_try_register(
        &self,
        key: LlmKey,
        f: impl FnOnce() -> Result<SharedLlm, String>,
    ) -> Result<SharedLlm, String> {
        if let Some(existing) = self.get(&key) {
            return Ok(existing);
        }

        let llm = f()?;
        let mut inner = self
            .inner
            .write()
            .expect("llm manager lock poisoned when get_or_try_register");

        if let Some(existing) = inner.get(&key) {
            return Ok(existing.clone());
        }

        inner.insert(key, llm.clone());
        Ok(llm)
    }

    pub fn len(&self) -> usize {
        let inner = self
            .inner
            .read()
            .expect("llm manager lock poisoned when len");
        inner.len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

fn compare_llm_key(left: &LlmKey, right: &LlmKey) -> Ordering {
    (&left.profile, &left.provider, &left.model, &left.endpoint).cmp(&(
        &right.profile,
        &right.provider,
        &right.model,
        &right.endpoint,
    ))
}
