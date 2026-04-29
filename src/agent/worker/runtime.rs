use std::collections::HashMap;

use tracing::warn;

use super::{Task, TaskMemory, WorkerPhase, WorkerState, action_bridge::decision_to_action};
use crate::{
    action::{Action, ActionResult, ActionState},
    cognition::{Cognition, CognitionInput, CognitionResult, Context, Fact, Intent, IntentKind},
    core::{
        messaging::{MessageRx, MessageTx},
        protocol::{AgentId, Message, MessageId, Payload, TaskId, WorkerId},
    },
    roles::{RoleProfile, RolePromptBuilder, RolePromptInput},
};

pub type BoxedAction = Box<dyn Action<Result = ActionResult<String, String, ()>> + Send>;

pub struct Worker {
    id: AgentId,
    role: RoleProfile,
    state: WorkerState,
    phase: WorkerPhase,
    current_task: Option<Task>,
    current_action: Option<BoxedAction>,
    cognition: Box<dyn Cognition + Send>,
    receiver: MessageRx,
    sender: MessageTx,
    memory: TaskMemory,
}

impl Worker {
    pub fn new(
        role: RoleProfile,
        cognition: Box<dyn Cognition + Send>,
        receiver: MessageRx,
        sender: MessageTx,
    ) -> Self {
        let id = AgentId(format!("worker.{}", role.runtime_role));
        Self {
            id,
            role,
            state: WorkerState::Idle,
            phase: WorkerPhase::Thinking,
            current_task: None,
            current_action: None,
            cognition,
            receiver,
            sender,
            memory: TaskMemory::default(),
        }
    }

    pub async fn run(&mut self) {
        while self.state != WorkerState::Shutdown {
            let Some(message) = self.receiver.recv().await else {
                break;
            };
            self.handle_message(message);

            while self.state == WorkerState::Running {
                self.runtime_step().await;
            }
        }
    }

    async fn runtime_step(&mut self) {
        match self.phase {
            WorkerPhase::Thinking => self.step_thinking().await,
            WorkerPhase::Acting => self.step_acting(),
            WorkerPhase::Finished => self.handle_task_finished(),
            WorkerPhase::Failed => self.handle_task_failed(),
        }
    }

    fn handle_message(&mut self, message: Message) {
        let context = message.context.clone();
        let requester = message.from.clone();
        let payload = match message.payload {
            Payload::WorkerAssignment { assignment } => {
                self.start_task(Task {
                    id: assignment.task_id.0.clone(),
                    context,
                    description: assignment.objective.clone(),
                    requester,
                    assignment: Some(assignment),
                });
                return;
            }
            Payload::Text { content } => content,
            _ => return,
        };
        let payload = payload.trim();

        match payload {
            "control:start" => {
                if self.current_task.is_some() {
                    self.state = WorkerState::Running;
                }
            }
            "control:pause" => {
                self.state = WorkerState::Paused;
                if let Some(action) = self.current_action.as_mut() {
                    action.suspend();
                }
            }
            "control:resume" => {
                self.state = WorkerState::Running;
                if let Some(action) = self.current_action.as_mut() {
                    action.resume();
                }
            }
            "control:shutdown" => {
                self.state = WorkerState::Shutdown;
            }
            _ => {
                if let Some(description) = payload.strip_prefix("task:start:") {
                    self.start_task(Task {
                        id: new_task_id(),
                        context: context.clone(),
                        description: description.trim().to_string(),
                        requester: requester.clone(),
                        assignment: None,
                    });
                }
            }
        }
    }

    fn start_task(&mut self, task: Task) {
        let _ = self.sender.send(Message {
            id: next_message_id(),
            context: task.context.clone(),
            from: self.id.clone(),
            to: task.requester.clone(),
            payload: Payload::WorkerReportStarted {
                worker_id: WorkerId(self.id.0.clone()),
                task_id: TaskId(task.id.clone()),
            },
        });
        self.current_task = Some(task);
        self.current_action = None;
        self.phase = WorkerPhase::Thinking;
        self.state = WorkerState::Running;
        self.memory.clear();
        self.memory.push_progress("task accepted");
    }

    async fn step_thinking(&mut self) {
        let Some(task) = self.current_task.as_ref() else {
            self.state = WorkerState::Idle;
            return;
        };

        self.memory.push_progress("thinking");

        let input = CognitionInput {
            intent: Intent {
                id: task.id.clone(),
                kind: IntentKind::Planning,
                description: task.description.clone(),
            },
            context: self.build_cognition_context(task),
        };

        match self.cognition.evaluate(input).await {
            CognitionResult::Success(output) => {
                self.memory
                    .set_state("last_cognition_output", output.to_string());
                if let Some(action) = decision_to_action(&output) {
                    self.current_action = Some(action);
                    self.phase = WorkerPhase::Acting;
                    self.memory.push_progress("action selected");
                } else {
                    self.phase = WorkerPhase::Finished;
                    self.memory.push_progress("no further action");
                }
            }
            CognitionResult::Failure(failure) => {
                warn!(error = %failure.description, "worker cognition failure");
                self.phase = WorkerPhase::Failed;
                self.memory
                    .set_error(format!("cognition failure: {}", failure.description));
            }
        }
    }

    fn step_acting(&mut self) {
        let Some(action) = self.current_action.as_mut() else {
            self.phase = WorkerPhase::Failed;
            self.memory
                .set_error("acting phase entered without current_action");
            warn!("worker acting phase without action");
            return;
        };

        self.memory.push_progress("acting");
        action.drive();

        match action.state() {
            ActionState::Completed => {
                if let Some(result) = action.take_result() {
                    self.memory
                        .set_state("last_action_status", format!("{:?}", result.status));
                }
                self.current_action = None;
                self.phase = WorkerPhase::Finished;
                self.memory.push_progress("action completed");
            }
            ActionState::Aborted => {
                self.phase = WorkerPhase::Failed;
                self.memory.set_error("action aborted");
                warn!("worker action aborted");
            }
            ActionState::Eligible | ActionState::Active | ActionState::Suspended => {}
        }
    }

    fn handle_task_finished(&mut self) {
        let Some(task) = self.current_task.take() else {
            self.state = WorkerState::Idle;
            return;
        };

        let _ = self.sender.send(Message {
            id: next_message_id(),
            context: task.context,
            from: self.id.clone(),
            to: task.requester,
            payload: task
                .assignment
                .as_ref()
                .map(|assignment| Payload::WorkerReport {
                    report: crate::core::protocol::WorkerReport {
                        graph_id: assignment.graph_id.clone(),
                        node_id: assignment.node_id.clone(),
                        task_id: TaskId(task.id.clone()),
                        worker_id: WorkerId(self.id.0.clone()),
                        role: assignment.role.clone(),
                        content: self.memory.state.get("last_cognition_output").map(|output| {
                            parse_worker_role_output(output).content
                        }).unwrap_or_default(),
                        role_output: self
                            .memory
                            .state
                            .get("last_cognition_output")
                            .and_then(|output| parse_worker_role_output(output).role_output),
                        evidence: self
                            .memory
                            .state
                            .get("last_cognition_output")
                            .map(|output| parse_worker_role_output(output).evidence)
                            .unwrap_or_default(),
                        risks: self
                            .memory
                            .state
                            .get("last_cognition_output")
                            .map(|output| parse_worker_role_output(output).risks)
                            .unwrap_or_default(),
                        open_questions: self
                            .memory
                            .state
                            .get("last_cognition_output")
                            .map(|output| parse_worker_role_output(output).open_questions)
                            .unwrap_or_default(),
                        status: crate::core::protocol::WorkerReportStatus::Completed,
                    },
                })
                .unwrap_or_else(|| Payload::WorkerReportFinished {
                    worker_id: WorkerId(self.id.0.clone()),
                    task_id: TaskId(task.id),
                    output: self.memory.state.get("last_cognition_output").cloned(),
                }),
        });
        self.phase = WorkerPhase::Thinking;
        self.state = WorkerState::Idle;
    }

    fn handle_task_failed(&mut self) {
        let Some(task) = self.current_task.take() else {
            self.state = WorkerState::Idle;
            return;
        };

        let reason = self
            .memory
            .last_error
            .clone()
            .unwrap_or_else(|| "unknown error".to_string());
        let _ = self.sender.send(Message {
            id: next_message_id(),
            context: task.context,
            from: self.id.clone(),
            to: task.requester,
            payload: task
                .assignment
                .as_ref()
                .map(|assignment| Payload::WorkerReport {
                    report: crate::core::protocol::WorkerReport {
                        graph_id: assignment.graph_id.clone(),
                        node_id: assignment.node_id.clone(),
                        task_id: TaskId(task.id.clone()),
                        worker_id: WorkerId(self.id.0.clone()),
                        role: assignment.role.clone(),
                        content: reason.clone(),
                        role_output: None,
                        evidence: Vec::new(),
                        risks: Vec::new(),
                        open_questions: vec![reason.clone()],
                        status: crate::core::protocol::WorkerReportStatus::Failed,
                    },
                })
                .unwrap_or_else(|| Payload::WorkerReportFailed {
                    worker_id: WorkerId(self.id.0.clone()),
                    task_id: TaskId(task.id.clone()),
                    reason: reason.clone(),
                }),
        });
        self.current_action = None;
        self.phase = WorkerPhase::Thinking;
        self.state = WorkerState::Idle;
        warn!(task_id = %task.id, reason = %reason, "worker reporting task failed");
    }
    fn build_cognition_context(&self, task: &Task) -> Context {
        build_context(
            self.id.clone(),
            self.phase,
            &self.memory,
            &self.role,
            task,
        )
    }
}

fn build_context(
    worker_id: AgentId,
    phase: WorkerPhase,
    memory: &TaskMemory,
    role: &RoleProfile,
    task: &Task,
) -> Context {
    let mut metadata = HashMap::new();
    metadata.insert("worker_id".to_string(), worker_id.0);
    metadata.insert("role.id".to_string(), role.id.0.clone());
    metadata.insert("role.name".to_string(), role.name.clone());
    metadata.insert("role.runtime_role".to_string(), role.runtime_role.clone());
    metadata.insert("phase".to_string(), format!("{phase:?}"));

    for (key, value) in &memory.state {
        metadata.insert(format!("memory.{key}"), value.clone());
    }

    if let Some(assignment) = &task.assignment {
        metadata.insert("assignment.graph_id".to_string(), assignment.graph_id.0.clone());
        metadata.insert("assignment.node_id".to_string(), assignment.node_id.0.clone());
        metadata.insert("assignment.role".to_string(), assignment.role.clone());
        metadata.insert("assignment.attempt".to_string(), assignment.attempt.to_string());
        metadata.insert(
            "assignment.input_count".to_string(),
            assignment.inputs.len().to_string(),
        );
        if let Some(instruction) = &assignment.rework_instruction {
            metadata.insert("assignment.rework_instruction".to_string(), instruction.clone());
        }
    }

    let mut facts = Vec::new();
    if let Some(assignment) = &task.assignment {
        facts.push(Fact {
            source: "worker.assignment".to_string(),
            content: format!(
                "node={} role={} attempt={} objective={}",
                assignment.node_id.0, assignment.role, assignment.attempt, assignment.objective
            ),
            reliability: 1.0,
        });
        for input in &assignment.inputs {
            facts.push(Fact {
                source: format!("worker.assignment.input.{}", input.node_id.0),
                content: input.content.clone(),
                reliability: 1.0,
            });
        }
    }

    let role_prompt = RolePromptBuilder::build_worker_prompt(&RolePromptInput {
        role: role.clone(),
        task: task.description.clone(),
        facts: memory.progress.clone(),
    });

    facts.push(Fact {
        source: "roles.prompt".to_string(),
        content: role_prompt,
        reliability: 1.0,
    });
    facts.extend(
        memory
            .progress
            .iter()
            .map(|entry| Fact {
                source: "worker.runtime".to_string(),
                content: entry.clone(),
                reliability: 1.0,
            })
            .collect::<Vec<_>>(),
    );

    Context { facts, metadata }
}

fn extract_worker_content(output: &str) -> String {
    let trimmed = output.trim();
    let Ok(value) = serde_json::from_str::<serde_json::Value>(trimmed) else {
        return trimmed.to_string();
    };

    value
        .pointer("/decision/action/parameters/answer")
        .and_then(|value| value.as_str())
        .or_else(|| value.pointer("/role_output").and_then(|value| value.as_str()))
        .or_else(|| value.pointer("/rationale/primary").and_then(|value| value.as_str()))
        .map(ToString::to_string)
        .unwrap_or_else(|| trimmed.to_string())
}

#[derive(Debug, Clone, Default)]
struct ParsedWorkerRoleOutput {
    content: String,
    role_output: Option<crate::core::protocol::RoleWorkOutput>,
    evidence: Vec<String>,
    risks: Vec<String>,
    open_questions: Vec<String>,
}

fn parse_worker_role_output(output: &str) -> ParsedWorkerRoleOutput {
    let trimmed = output.trim();
    let Ok(value) = serde_json::from_str::<serde_json::Value>(trimmed) else {
        return ParsedWorkerRoleOutput {
            content: trimmed.to_string(),
            ..ParsedWorkerRoleOutput::default()
        };
    };

    let role_output = parse_role_work_output(&value);
    let evidence = role_output
        .as_ref()
        .map(|output| output.evidence.clone())
        .filter(|items| !items.is_empty())
        .unwrap_or_else(|| string_array_field(&value, "evidence"));
    let risks = role_output
        .as_ref()
        .map(|output| output.risks.clone())
        .filter(|items| !items.is_empty())
        .unwrap_or_else(|| string_array_field(&value, "risks"));
    let open_questions = role_output
        .as_ref()
        .map(|output| output.open_questions.clone())
        .filter(|items| !items.is_empty())
        .unwrap_or_else(|| string_array_field(&value, "open_questions"));
    let content = role_output
        .as_ref()
        .map(format_role_work_output_content)
        .filter(|content| !content.trim().is_empty())
        .unwrap_or_else(|| extract_worker_content(trimmed));

    ParsedWorkerRoleOutput {
        content,
        role_output,
        evidence,
        risks,
        open_questions,
    }
}

fn parse_role_work_output(value: &serde_json::Value) -> Option<crate::core::protocol::RoleWorkOutput> {
    let source = value.get("role_output").unwrap_or(value);
    if let Some(text) = source.as_str() {
        let summary = text.trim().to_string();
        return (!summary.is_empty()).then_some(crate::core::protocol::RoleWorkOutput {
            summary,
            evidence: string_array_field(value, "evidence"),
            risks: string_array_field(value, "risks"),
            open_questions: string_array_field(value, "open_questions"),
            ..crate::core::protocol::RoleWorkOutput::default()
        });
    }

    if !source.is_object() {
        return None;
    }

    let summary = string_field(source, "summary")
        .or_else(|| string_field(source, "结论"))
        .or_else(|| string_field(value, "summary"))
        .unwrap_or_default();
    let findings = string_array_field(source, "findings");
    let recommendations = string_array_field(source, "recommendations")
        .into_iter()
        .chain(string_array_field(source, "actions"))
        .collect::<Vec<_>>();
    let evidence = string_array_field(source, "evidence")
        .into_iter()
        .chain(string_array_field(value, "evidence"))
        .collect::<Vec<_>>();
    let risks = string_array_field(source, "risks")
        .into_iter()
        .chain(string_array_field(value, "risks"))
        .collect::<Vec<_>>();
    let open_questions = string_array_field(source, "open_questions")
        .into_iter()
        .chain(string_array_field(value, "open_questions"))
        .collect::<Vec<_>>();

    let output = crate::core::protocol::RoleWorkOutput {
        summary,
        findings,
        recommendations,
        evidence,
        risks,
        open_questions,
    };
    has_role_work_output_content(&output).then_some(output)
}

fn has_role_work_output_content(output: &crate::core::protocol::RoleWorkOutput) -> bool {
    !output.summary.trim().is_empty()
        || !output.findings.is_empty()
        || !output.recommendations.is_empty()
        || !output.evidence.is_empty()
        || !output.risks.is_empty()
        || !output.open_questions.is_empty()
}

fn format_role_work_output_content(output: &crate::core::protocol::RoleWorkOutput) -> String {
    let mut sections = Vec::new();
    if !output.summary.trim().is_empty() {
        sections.push(output.summary.trim().to_string());
    }
    extend_section(&mut sections, "发现", &output.findings);
    extend_section(&mut sections, "建议", &output.recommendations);
    extend_section(&mut sections, "风险", &output.risks);
    extend_section(&mut sections, "缺口", &output.open_questions);
    sections.join("\n")
}

fn extend_section(lines: &mut Vec<String>, title: &str, items: &[String]) {
    if items.is_empty() {
        return;
    }
    lines.push(format!(
        "{}：{}",
        title,
        items
            .iter()
            .filter(|item| !item.trim().is_empty())
            .map(|item| item.trim().to_string())
            .collect::<Vec<_>>()
            .join("；")
    ));
}

fn string_field(value: &serde_json::Value, key: &str) -> Option<String> {
    value
        .get(key)
        .and_then(|value| value.as_str())
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToString::to_string)
}

fn string_array_field(value: &serde_json::Value, key: &str) -> Vec<String> {
    value
        .get(key)
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_str())
                .map(str::trim)
                .filter(|item| !item.is_empty())
                .map(ToString::to_string)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default()
}

fn new_task_id() -> String {
    use std::{
        sync::atomic::{AtomicU64, Ordering},
        time::{SystemTime, UNIX_EPOCH},
    };

    static NEXT_TASK_COUNTER: AtomicU64 = AtomicU64::new(1);

    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let sequence = NEXT_TASK_COUNTER.fetch_add(1, Ordering::Relaxed);

    format!("task-{millis}-{sequence}")
}

fn next_message_id() -> MessageId {
    use std::{
        sync::atomic::{AtomicU64, Ordering},
        time::{SystemTime, UNIX_EPOCH},
    };

    static NEXT_MESSAGE_COUNTER: AtomicU64 = AtomicU64::new(1);

    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let sequence = NEXT_MESSAGE_COUNTER.fetch_add(1, Ordering::Relaxed);

    MessageId(format!("msg-{millis}-{sequence}"))
}

#[cfg(test)]
mod tests {
    use super::parse_worker_role_output;

    #[test]
    fn parses_structured_role_work_output() {
        let parsed = parse_worker_role_output(
            r#"{
              "decision": {"kind": "NoAction"},
              "role_output": {
                "summary": "运营方案需要先拆优先级",
                "findings": ["预算有限", "目标明确"],
                "recommendations": ["P1先做转化链路", "P2补素材", "P3复盘"],
                "evidence": ["assignment.input.data"],
                "risks": ["预算消耗过快"],
                "open_questions": []
              }
            }"#,
        );

        let role_output = parsed.role_output.expect("role output should parse");
        assert_eq!(role_output.summary, "运营方案需要先拆优先级");
        assert_eq!(role_output.recommendations.len(), 3);
        assert_eq!(parsed.evidence, vec!["assignment.input.data"]);
        assert_eq!(parsed.risks, vec!["预算消耗过快"]);
        assert!(parsed.content.contains("P1先做转化链路"));
    }

    #[test]
    fn wraps_legacy_string_role_output_as_summary() {
        let parsed = parse_worker_role_output(
            r#"{
              "decision": {"kind": "NoAction"},
              "role_output": "这是旧格式角色产物",
              "evidence": ["legacy evidence"]
            }"#,
        );

        let role_output = parsed.role_output.expect("legacy role output should parse");
        assert_eq!(role_output.summary, "这是旧格式角色产物");
        assert_eq!(role_output.evidence, vec!["legacy evidence"]);
        assert_eq!(parsed.content, "这是旧格式角色产物");
    }
}
