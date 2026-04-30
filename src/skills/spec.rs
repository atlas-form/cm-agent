use std::path::PathBuf;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use thiserror::Error;

#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[derive(Default)]
pub enum SkillCategory {
    #[default]
    General,
    Planning,
    Research,
    Coding,
    Filesystem,
    Shell,
    Browser,
    Memory,
    Communication,
    Data,
    Custom(String),
}

#[derive(
    Clone, Copy, Debug, Default, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize,
)]
#[serde(rename_all = "snake_case")]
pub enum SkillPriority {
    Low,
    #[default]
    Normal,
    High,
    Critical,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SkillInputField {
    pub name: String,
    pub description: String,
    pub required: bool,
    #[serde(default)]
    pub schema: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub default: Option<Value>,
}

impl SkillInputField {
    pub fn new(
        name: impl Into<String>,
        description: impl Into<String>,
        required: bool,
        schema: Value,
    ) -> Self {
        Self {
            name: name.into(),
            description: description.into(),
            required,
            schema,
            default: None,
        }
    }

    pub fn required(
        name: impl Into<String>,
        description: impl Into<String>,
        schema: Value,
    ) -> Self {
        Self::new(name, description, true, schema)
    }

    pub fn optional(
        name: impl Into<String>,
        description: impl Into<String>,
        schema: Value,
    ) -> Self {
        Self::new(name, description, false, schema)
    }

    pub fn with_default(mut self, default: Value) -> Self {
        self.default = Some(default);
        self
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SkillSpec {
    pub id: String,
    pub name: String,
    pub description: String,
    #[serde(default)]
    pub category: SkillCategory,
    #[serde(default)]
    pub priority: SkillPriority,
    #[serde(default)]
    pub inputs: Vec<SkillInputField>,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub version: Option<String>,
}

impl SkillSpec {
    pub fn new(
        id: impl Into<String>,
        name: impl Into<String>,
        description: impl Into<String>,
    ) -> Self {
        Self {
            id: id.into(),
            name: name.into(),
            description: description.into(),
            category: SkillCategory::default(),
            priority: SkillPriority::default(),
            inputs: Vec::new(),
            tags: Vec::new(),
            version: None,
        }
    }

    pub fn with_category(mut self, category: SkillCategory) -> Self {
        self.category = category;
        self
    }

    pub fn with_priority(mut self, priority: SkillPriority) -> Self {
        self.priority = priority;
        self
    }

    pub fn with_input(mut self, input: SkillInputField) -> Self {
        self.inputs.push(input);
        self
    }

    pub fn with_tag(mut self, tag: impl Into<String>) -> Self {
        self.tags.push(tag.into());
        self
    }

    pub fn with_version(mut self, version: impl Into<String>) -> Self {
        self.version = Some(version.into());
        self
    }
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct SkillContext {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub invocation_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub working_dir: Option<PathBuf>,
    #[serde(default)]
    pub metadata: Map<String, Value>,
}

impl SkillContext {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_invocation_id(mut self, invocation_id: impl Into<String>) -> Self {
        self.invocation_id = Some(invocation_id.into());
        self
    }

    pub fn with_agent_id(mut self, agent_id: impl Into<String>) -> Self {
        self.agent_id = Some(agent_id.into());
        self
    }

    pub fn with_session_id(mut self, session_id: impl Into<String>) -> Self {
        self.session_id = Some(session_id.into());
        self
    }

    pub fn with_working_dir(mut self, working_dir: impl Into<PathBuf>) -> Self {
        self.working_dir = Some(working_dir.into());
        self
    }

    pub fn with_metadata(mut self, key: impl Into<String>, value: Value) -> Self {
        self.metadata.insert(key.into(), value);
        self
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SkillOutcome {
    pub output: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub summary: Option<String>,
    #[serde(default)]
    pub metadata: Map<String, Value>,
}

impl SkillOutcome {
    pub fn new(output: Value) -> Self {
        Self {
            output,
            summary: None,
            metadata: Map::new(),
        }
    }

    pub fn with_summary(mut self, summary: impl Into<String>) -> Self {
        self.summary = Some(summary.into());
        self
    }

    pub fn with_metadata(mut self, key: impl Into<String>, value: Value) -> Self {
        self.metadata.insert(key.into(), value);
        self
    }
}

#[derive(Clone, Debug, Error, PartialEq)]
pub enum SkillError {
    #[error("missing required parameter '{name}'")]
    MissingParameter { name: String },
    #[error("invalid parameter '{name}': expected {expected}")]
    InvalidParameter { name: String, expected: String },
    #[error("skill execution failed: {message}")]
    Execution { message: String },
    #[error("skill was cancelled: {message}")]
    Cancelled { message: String },
    #[error("internal skill error: {message}")]
    Internal { message: String },
}

impl SkillError {
    pub fn missing_parameter(name: impl Into<String>) -> Self {
        Self::MissingParameter { name: name.into() }
    }

    pub fn invalid_parameter(name: impl Into<String>, expected: impl Into<String>) -> Self {
        Self::InvalidParameter {
            name: name.into(),
            expected: expected.into(),
        }
    }

    pub fn execution(message: impl Into<String>) -> Self {
        Self::Execution {
            message: message.into(),
        }
    }

    pub fn cancelled(message: impl Into<String>) -> Self {
        Self::Cancelled {
            message: message.into(),
        }
    }

    pub fn internal(message: impl Into<String>) -> Self {
        Self::Internal {
            message: message.into(),
        }
    }
}

pub type SkillResult = Result<SkillOutcome, SkillError>;

#[async_trait]
pub trait Skill: Send + Sync {
    fn spec(&self) -> &SkillSpec;

    async fn run(&self, params: Value, context: SkillContext) -> SkillResult;
}

pub fn object_params(params: &Value) -> Result<&Map<String, Value>, SkillError> {
    params
        .as_object()
        .ok_or_else(|| SkillError::invalid_parameter("$", "object"))
}

pub fn param<'a>(params: &'a Value, name: &str) -> Result<&'a Value, SkillError> {
    object_params(params)?
        .get(name)
        .ok_or_else(|| SkillError::missing_parameter(name))
}

pub fn string_param(params: &Value, name: &str) -> Result<String, SkillError> {
    param(params, name).and_then(|value| string_value(name, value))
}

pub fn optional_string_param(params: &Value, name: &str) -> Result<Option<String>, SkillError> {
    optional_param(params, name, string_value)
}

pub fn bool_param(params: &Value, name: &str) -> Result<bool, SkillError> {
    param(params, name)?
        .as_bool()
        .ok_or_else(|| SkillError::invalid_parameter(name, "boolean"))
}

pub fn optional_bool_param(params: &Value, name: &str) -> Result<Option<bool>, SkillError> {
    optional_param(params, name, |field, value| {
        value
            .as_bool()
            .ok_or_else(|| SkillError::invalid_parameter(field, "boolean"))
    })
}

pub fn i64_param(params: &Value, name: &str) -> Result<i64, SkillError> {
    param(params, name)?
        .as_i64()
        .ok_or_else(|| SkillError::invalid_parameter(name, "integer"))
}

pub fn optional_i64_param(params: &Value, name: &str) -> Result<Option<i64>, SkillError> {
    optional_param(params, name, |field, value| {
        value
            .as_i64()
            .ok_or_else(|| SkillError::invalid_parameter(field, "integer"))
    })
}

pub fn u64_param(params: &Value, name: &str) -> Result<u64, SkillError> {
    param(params, name)?
        .as_u64()
        .ok_or_else(|| SkillError::invalid_parameter(name, "unsigned integer"))
}

pub fn optional_u64_param(params: &Value, name: &str) -> Result<Option<u64>, SkillError> {
    optional_param(params, name, |field, value| {
        value
            .as_u64()
            .ok_or_else(|| SkillError::invalid_parameter(field, "unsigned integer"))
    })
}

pub fn f64_param(params: &Value, name: &str) -> Result<f64, SkillError> {
    param(params, name)?
        .as_f64()
        .ok_or_else(|| SkillError::invalid_parameter(name, "number"))
}

pub fn optional_f64_param(params: &Value, name: &str) -> Result<Option<f64>, SkillError> {
    optional_param(params, name, |field, value| {
        value
            .as_f64()
            .ok_or_else(|| SkillError::invalid_parameter(field, "number"))
    })
}

pub fn string_vec_param(params: &Value, name: &str) -> Result<Vec<String>, SkillError> {
    param(params, name).and_then(|value| string_vec_value(name, value))
}

pub fn optional_string_vec_param(
    params: &Value,
    name: &str,
) -> Result<Option<Vec<String>>, SkillError> {
    optional_param(params, name, string_vec_value)
}

fn optional_param<T>(
    params: &Value,
    name: &str,
    parse: impl FnOnce(&str, &Value) -> Result<T, SkillError>,
) -> Result<Option<T>, SkillError> {
    Ok(match object_params(params)?.get(name) {
        Some(Value::Null) | None => None,
        Some(value) => Some(parse(name, value)?),
    })
}

fn string_value(name: &str, value: &Value) -> Result<String, SkillError> {
    value
        .as_str()
        .map(ToOwned::to_owned)
        .ok_or_else(|| SkillError::invalid_parameter(name, "string"))
}

fn string_vec_value(name: &str, value: &Value) -> Result<Vec<String>, SkillError> {
    let values = value
        .as_array()
        .ok_or_else(|| SkillError::invalid_parameter(name, "array of strings"))?;

    values
        .iter()
        .enumerate()
        .map(|(index, value)| {
            value
                .as_str()
                .map(ToOwned::to_owned)
                .ok_or_else(|| SkillError::invalid_parameter(format!("{name}[{index}]"), "string"))
        })
        .collect()
}
