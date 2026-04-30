use std::{
    fs, io,
    path::{Path, PathBuf},
    sync::Mutex,
};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use super::{
    InMemoryMemoryStore, MemoryBundle, MemoryCompactionPolicy, MemoryQuery, MemoryRecord,
    MemoryScope, MemoryStore, MemoryWriteOutcome, SessionSnapshot,
};

const FILE_MEMORY_ENVELOPE_VERSION: u32 = 1;

#[derive(Debug, Error)]
pub enum FileMemoryStoreError {
    #[error("memory file io error at {path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("memory file json error at {path}: {source}")]
    Json {
        path: PathBuf,
        #[source]
        source: serde_json::Error,
    },
    #[error("unsupported memory file version {version}; expected {expected}")]
    UnsupportedVersion { version: u32, expected: u32 },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct FileMemoryEnvelope {
    version: u32,
    records: Vec<MemoryRecord>,
}

#[derive(Debug)]
pub struct FileMemoryStore {
    path: PathBuf,
    inner: InMemoryMemoryStore,
    flush_lock: Mutex<()>,
}

impl FileMemoryStore {
    pub fn open(path: impl Into<PathBuf>) -> Result<Self, FileMemoryStoreError> {
        Self::open_with_policy(path, MemoryCompactionPolicy::default())
    }

    pub fn open_with_policy(
        path: impl Into<PathBuf>,
        policy: MemoryCompactionPolicy,
    ) -> Result<Self, FileMemoryStoreError> {
        let path = path.into();
        let records = load_records(&path)?;
        Ok(Self {
            path,
            inner: InMemoryMemoryStore::with_records(policy, records),
            flush_lock: Mutex::new(()),
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn records(&self) -> Vec<MemoryRecord> {
        self.inner.records()
    }

    fn flush(&self) -> Result<(), FileMemoryStoreError> {
        let _guard = self
            .flush_lock
            .lock()
            .expect("file memory store lock poisoned when flushing");
        let envelope = FileMemoryEnvelope {
            version: FILE_MEMORY_ENVELOPE_VERSION,
            records: self.inner.records(),
        };
        let bytes =
            serde_json::to_vec_pretty(&envelope).map_err(|source| FileMemoryStoreError::Json {
                path: self.path.clone(),
                source,
            })?;
        write_atomic(&self.path, &bytes)
    }
}

impl MemoryStore for FileMemoryStore {
    fn load_bundle(&self, query: MemoryQuery) -> MemoryBundle {
        MemoryStore::load_bundle(&self.inner, query)
    }

    fn persist_session(
        &self,
        scope: &MemoryScope,
        snapshot: SessionSnapshot,
    ) -> MemoryWriteOutcome {
        let outcome = MemoryStore::persist_session(&self.inner, scope, snapshot);
        if outcome.written > 0 {
            self.flush()
                .expect("failed to flush file memory store after persisting session");
        }
        outcome
    }

    fn upsert_record(&self, record: MemoryRecord) -> MemoryWriteOutcome {
        let outcome = MemoryStore::upsert_record(&self.inner, record);
        if outcome.written > 0 {
            self.flush()
                .expect("failed to flush file memory store after upserting record");
        }
        outcome
    }
}

fn load_records(path: &Path) -> Result<Vec<MemoryRecord>, FileMemoryStoreError> {
    match fs::read(path) {
        Ok(bytes) => {
            let envelope =
                serde_json::from_slice::<FileMemoryEnvelope>(&bytes).map_err(|source| {
                    FileMemoryStoreError::Json {
                        path: path.to_path_buf(),
                        source,
                    }
                })?;
            if envelope.version != FILE_MEMORY_ENVELOPE_VERSION {
                return Err(FileMemoryStoreError::UnsupportedVersion {
                    version: envelope.version,
                    expected: FILE_MEMORY_ENVELOPE_VERSION,
                });
            }
            Ok(envelope.records)
        }
        Err(source) if source.kind() == io::ErrorKind::NotFound => Ok(Vec::new()),
        Err(source) => Err(FileMemoryStoreError::Io {
            path: path.to_path_buf(),
            source,
        }),
    }
}

fn write_atomic(path: &Path, bytes: &[u8]) -> Result<(), FileMemoryStoreError> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|source| FileMemoryStoreError::Io {
            path: parent.to_path_buf(),
            source,
        })?;
    }

    let tmp_path = path.with_extension(format!(
        "{}tmp",
        path.extension()
            .and_then(|extension| extension.to_str())
            .map(|extension| format!("{extension}."))
            .unwrap_or_default()
    ));
    fs::write(&tmp_path, bytes).map_err(|source| FileMemoryStoreError::Io {
        path: tmp_path.clone(),
        source,
    })?;
    fs::rename(&tmp_path, path).map_err(|source| FileMemoryStoreError::Io {
        path: path.to_path_buf(),
        source,
    })
}
