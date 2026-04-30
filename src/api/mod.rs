//! Public API for applications embedding `cm-agent`.
//!
//! Web servers should depend on this module instead of internal runtime modules.

pub use crate::{
    agent_error::{Error, Result},
    agent_manager::{AgentManager, AgentManagerConfig, AgentRequest},
    agent_session::{
        CognitionFactory, DeterministicMemoryCompactor, FileMemoryStore, FileMemoryStoreError,
        InMemoryMemoryStore, MemoryBudget, MemoryBundle, MemoryCompactionPolicy,
        MemoryCompactionStats, MemoryCompactor, MemoryId, MemoryKind, MemoryQuery, MemoryRecord,
        MemoryScope, MemorySource, MemoryStore, MemoryWriteOutcome, NoopMemoryStore,
        RoleCognitionFactory, SessionEventRx, SessionEventTx, SessionResult, SessionRuntimeConfig,
        SessionSnapshot,
    },
    cognition::{
        Cognition, CognitionEngine, CognitionFailure, CognitionInput, CognitionOutput,
        CognitionResult, Context, Fact, FailureReason, Intent, IntentKind, JsonTemplateDecoder,
    },
    core::protocol::{AgentId, SessionEvent, SessionId, UserId, WorkspaceId},
    roles::{
        RoleAction, RoleAssignment, RoleCatalog, RoleCollaborationPlan, RoleContribution, RoleId,
        RoleProfile, RolePromptBuilder, RolePromptInput, RoleRoute, RoleRouteInput, RoleRouter,
        RoleScore,
    },
};
