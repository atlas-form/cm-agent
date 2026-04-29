use std::collections::HashMap;

#[derive(Debug, Clone, Default)]
pub struct TaskMemory {
    pub(crate) progress: Vec<String>,
    pub(crate) state: HashMap<String, String>,
    pub(crate) last_error: Option<String>,
}

impl TaskMemory {
    pub fn clear(&mut self) {
        self.progress.clear();
        self.state.clear();
        self.last_error = None;
    }

    pub fn push_progress(&mut self, entry: impl Into<String>) {
        self.progress.push(entry.into());
    }

    pub fn set_state(&mut self, key: impl Into<String>, value: impl Into<String>) {
        self.state.insert(key.into(), value.into());
    }

    pub fn set_error(&mut self, error: impl Into<String>) {
        self.last_error = Some(error.into());
    }
}
