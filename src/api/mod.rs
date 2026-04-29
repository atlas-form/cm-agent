//! Public API for applications embedding `cm-agent`.
//!
//! Web servers should depend on this module instead of internal runtime modules.

pub use crate::{
    agent_error::{Error, Result},
    agent_manager::{AgentManager, AgentManagerConfig, AgentRequest},
    agent_session::{
        CognitionFactory, MemoryScope, MemoryStore, NoopMemoryStore, RoleCognitionFactory,
        SessionEventRx, SessionEventTx, SessionResult, SessionRuntimeConfig, SessionSnapshot,
    },
    cognition::{
        Cognition, CognitionEngine, CognitionFailure, CognitionInput, CognitionOutput,
        CognitionResult, Context, Fact, FailureReason, Intent, IntentKind, JsonTemplateDecoder,
    },
    core::protocol::{AgentId, SessionEvent, SessionId, UserId, WorkspaceId},
    roles::{
        RoleAction, RoleCatalog, RoleId, RoleProfile, RolePromptBuilder, RolePromptInput,
        RoleRoute, RoleRouteInput, RoleRouter, RoleScore,
    },
};
