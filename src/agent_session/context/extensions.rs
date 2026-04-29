use std::{
    any::{Any, TypeId},
    collections::HashMap,
    sync::RwLock,
};

#[derive(Default)]
pub struct SessionExtensions {
    inner: RwLock<HashMap<TypeId, Box<dyn Any + Send + Sync>>>,
}

impl std::fmt::Debug for SessionExtensions {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SessionExtensions").finish_non_exhaustive()
    }
}

impl SessionExtensions {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn insert<T>(&self, value: T)
    where
        T: Any + Send + Sync + 'static,
    {
        let mut inner = self
            .inner
            .write()
            .expect("session extensions lock poisoned when insert");
        inner.insert(TypeId::of::<T>(), Box::new(value));
    }

    pub fn contains<T>(&self) -> bool
    where
        T: Any + Send + Sync + 'static,
    {
        let inner = self
            .inner
            .read()
            .expect("session extensions lock poisoned when contains");
        inner.contains_key(&TypeId::of::<T>())
    }

    pub fn with<T, R>(&self, f: impl FnOnce(&T) -> R) -> Option<R>
    where
        T: Any + Send + Sync + 'static,
    {
        let inner = self
            .inner
            .read()
            .expect("session extensions lock poisoned when with");
        inner
            .get(&TypeId::of::<T>())
            .and_then(|stored| stored.downcast_ref::<T>().map(f))
    }
}
