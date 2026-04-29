use agent_core::protocol::{DecisionIntent, TaskSpec, WorkerId};
use cognition::CognitionOutput;
use serde_json::Value;

use super::CommanderTask;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RoutedDecision {
    pub intent: DecisionIntent,
    pub target_worker_id: Option<WorkerId>,
    pub clarification: Option<String>,
}

pub fn decision_intent_from_json(
    output: &CognitionOutput,
    current_task: Option<CommanderTask>,
) -> RoutedDecision {
    let decision = output.get("decision");
    let kind = decision
        .and_then(|value| value.get("kind"))
        .and_then(|value| value.as_str());

    match kind {
        Some("RouteTask") => route_task(decision, current_task),
        Some("KeepCurrentAssignment") => RoutedDecision {
            intent: DecisionIntent::KeepCurrentTask,
            target_worker_id: parse_target_worker_id(decision),
            clarification: None,
        },
        Some("AskForClarification") => RoutedDecision {
            intent: DecisionIntent::Ignore,
            target_worker_id: None,
            clarification: decision
                .and_then(|value| value.get("clarification"))
                .and_then(|value| value.get("question"))
                .and_then(|value| value.as_str())
                .map(ToOwned::to_owned),
        },
        Some("NoRoute") => RoutedDecision {
            intent: DecisionIntent::IgnoreNewTask,
            target_worker_id: None,
            clarification: None,
        },
        Some("NoAction") => RoutedDecision {
            intent: DecisionIntent::IgnoreNewTask,
            target_worker_id: None,
            clarification: None,
        },
        Some("ActionIntent") => RoutedDecision {
            intent: current_task
                .map(task_to_spec)
                .map(|task| DecisionIntent::ExecuteTask { task })
                .unwrap_or(DecisionIntent::Ignore),
            target_worker_id: parse_target_worker_id(decision),
            clarification: None,
        },
        Some("StateProposal") => {
            let key = decision
                .and_then(|value| value.get("state_change"))
                .and_then(|value| value.get("key"))
                .and_then(|value| value.as_str());
            let intent = if key == Some("replace_current_task") {
                current_task
                    .map(task_to_spec)
                    .map(|task| DecisionIntent::ReplaceCurrentTask { task })
                    .unwrap_or(DecisionIntent::Ignore)
            } else {
                DecisionIntent::KeepCurrentTask
            };

            RoutedDecision {
                intent,
                target_worker_id: None,
                clarification: None,
            }
        }
        Some("StrategyHint") => RoutedDecision {
            intent: DecisionIntent::StrategyHint {
                hint: decision
                    .and_then(|value| value.get("strategy"))
                    .and_then(|value| value.get("hint"))
                    .and_then(|value| value.as_str())
                    .unwrap_or_default()
                    .to_string(),
            },
            target_worker_id: None,
            clarification: None,
        },
        _ => RoutedDecision {
            intent: DecisionIntent::Ignore,
            target_worker_id: None,
            clarification: None,
        },
    }
}

fn route_task(decision: Option<&Value>, current_task: Option<CommanderTask>) -> RoutedDecision {
    let intent = current_task
        .map(task_to_spec)
        .map(|task| DecisionIntent::ExecuteTask { task })
        .unwrap_or(DecisionIntent::Ignore);

    RoutedDecision {
        intent,
        target_worker_id: parse_target_worker_id(decision),
        clarification: None,
    }
}

fn parse_target_worker_id(decision: Option<&Value>) -> Option<WorkerId> {
    let route = decision.and_then(|value: &Value| value.get("route"))?;
    let target = route
        .get("target_agent_id")
        .and_then(|value: &Value| value.as_str())
        .map(str::trim)
        .filter(|value: &&str| !value.is_empty())?;

    Some(WorkerId(target.to_string()))
}

fn task_to_spec(task: CommanderTask) -> TaskSpec {
    TaskSpec {
        id: agent_core::protocol::TaskId(task.id),
        description: task.description,
    }
}

#[cfg(test)]
mod tests {
    use cognition::CognitionOutput;
    use serde_json::json;

    use super::decision_intent_from_json;
    use crate::commander::CommanderTask;

    #[test]
    fn parses_route_task_and_target_worker() {
        let output: CognitionOutput = json!({
            "decision": {
                "kind": "RouteTask",
                "route": {
                    "target_agent_id": "worker-2",
                    "task_summary": "sum",
                    "goal": "finish",
                    "constraints": []
                },
                "handoff": {
                    "why_this_agent": "best fit",
                    "expected_output": "done"
                },
                "clarification": {
                    "question": ""
                }
            },
            "confidence": 0.9,
            "rationale": {
                "primary": "fit",
                "evidence": [],
                "alternatives_considered": []
            }
        });

        let decision = decision_intent_from_json(
            &output,
            Some(CommanderTask {
                id: "task-1".to_string(),
                description: "test".to_string(),
            }),
        );

        assert_eq!(decision.target_worker_id.unwrap().0, "worker-2");
        assert!(matches!(
            decision.intent,
            agent_core::protocol::DecisionIntent::ExecuteTask { .. }
        ));
    }

    #[test]
    fn parses_clarification_question() {
        let output: CognitionOutput = json!({
            "decision": {
                "kind": "AskForClarification",
                "route": {
                    "target_agent_id": "",
                    "task_summary": "",
                    "goal": "",
                    "constraints": []
                },
                "handoff": {
                    "why_this_agent": "",
                    "expected_output": ""
                },
                "clarification": {
                    "question": "需要先确认目标环境"
                }
            },
            "confidence": 0.4,
            "rationale": {
                "primary": "missing info",
                "evidence": [],
                "alternatives_considered": []
            }
        });

        let decision = decision_intent_from_json(&output, None);

        assert_eq!(
            decision.clarification.as_deref(),
            Some("需要先确认目标环境")
        );
    }
}
