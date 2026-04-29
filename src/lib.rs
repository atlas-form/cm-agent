#![allow(dead_code, unused_imports)]

pub mod api;

#[path = "action/lib.rs"]
pub(crate) mod action;
#[path = "agent/lib.rs"]
pub(crate) mod agent;
#[path = "agent_error/lib.rs"]
pub(crate) mod agent_error;
#[path = "agent_manager/lib.rs"]
pub(crate) mod agent_manager;
#[path = "agent_session/lib.rs"]
pub(crate) mod agent_session;
#[path = "agent_utils/lib.rs"]
pub(crate) mod agent_utils;
#[path = "cognition/lib.rs"]
pub(crate) mod cognition;
#[path = "core/lib.rs"]
pub(crate) mod core;
#[path = "perception/lib.rs"]
pub(crate) mod perception;

pub(crate) use core::{messaging, messaging::*, protocol, protocol::*};

pub(crate) use action::{
    Action, ActionId, ActionInput, ActionLifecycle, ActionResult, ActionState, ActionStatus,
    ActionTarget, ActionTransitionError, ActionType,
};
pub(crate) use agent_session::{
    AgentSession, AgentSessionConfig, AgentSessionScope, CognitionFactory, EndpointDirectory,
    MemoryScope, MemoryStore, NoopMemoryStore, SessionBlackboard, SessionContext, SessionEventRx,
    SessionEventTx, SessionExtensions, SessionResult, SessionRuntime, SessionRuntimeConfig,
    SessionRuntimeInput, SessionSnapshot, WorkerCatalog, context,
};
pub(crate) use cognition::{
    Cognition, CognitionDecoder, CognitionEngine, CognitionFailure, CognitionInput,
    CognitionOutput, CognitionResult, Context, Fact, FailureReason, Intent, IntentKind,
    JsonTemplateDecoder,
};
pub(crate) use perception::{
    Modality, Percept, PerceptContent, Perception, PerceptionOutput, Signal, Stimulus,
    StimulusSpan, TextUtterancePerception,
};
pub extern crate tracing;
