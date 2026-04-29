use std::{hint::black_box, sync::Arc};

use async_trait::async_trait;
use cm_agent::api::{
    AgentId, AgentManager, AgentManagerConfig, AgentRequest, Cognition, CognitionInput,
    CognitionResult, SessionId, UserId,
};
use criterion::{BenchmarkId, Criterion, criterion_group, criterion_main};
use futures::future::join_all;
use serde_json::json;
use tokio::runtime::Runtime;

struct CommanderBenchCognition;

#[async_trait]
impl Cognition for CommanderBenchCognition {
    async fn evaluate(&self, _input: CognitionInput) -> CognitionResult {
        CognitionResult::Success(json!({
            "decision": {
                "kind": "RouteTask",
                "route": {
                    "target_agent_id": "worker-1",
                    "task_summary": "benchmark request",
                    "goal": "finish one isolated request",
                    "constraints": []
                },
                "handoff": {
                    "why_this_agent": "default worker",
                    "expected_output": "done"
                },
                "clarification": {
                    "question": ""
                }
            },
            "confidence": 1.0,
            "rationale": {
                "primary": "criterion benchmark",
                "evidence": [],
                "alternatives_considered": []
            }
        }))
    }
}

struct WorkerBenchCognition;

#[async_trait]
impl Cognition for WorkerBenchCognition {
    async fn evaluate(&self, _input: CognitionInput) -> CognitionResult {
        CognitionResult::Success(json!({
            "decision": {
                "kind": "NoAction"
            }
        }))
    }
}

fn bench_single_session(c: &mut Criterion) {
    let runtime = Runtime::new().expect("create tokio runtime");

    c.bench_function("session_runtime/single_session_no_llm", |b| {
        b.iter(|| {
            runtime.block_on(async {
                let manager = build_manager();
                let result = manager
                    .run_request(request_for(black_box(0)))
                    .await
                    .expect("single session should complete");
                black_box(result);
            });
        });
    });
}

fn bench_concurrent_sessions(c: &mut Criterion) {
    let runtime = Runtime::new().expect("create tokio runtime");
    let mut group = c.benchmark_group("session_runtime/concurrent_no_llm");
    group.sample_size(10);

    for total in [10usize, 100, 1000] {
        group.bench_with_input(BenchmarkId::from_parameter(total), &total, |b, &total| {
            b.iter(|| {
                runtime.block_on(async move {
                    let manager = Arc::new(build_manager());
                    let handles = (0 .. total)
                        .map(|index| {
                            let manager = Arc::clone(&manager);
                            tokio::spawn(async move {
                                manager
                                    .run_request(request_for(index))
                                    .await
                                    .expect("session should complete")
                            })
                        })
                        .collect::<Vec<_>>();

                    let results = join_all(handles).await;
                    for result in results {
                        black_box(result.expect("session task should not panic"));
                    }
                    assert_eq!(manager.active_session_count(), 0);
                });
            });
        });
    }

    group.finish();
}

fn build_manager() -> AgentManager {
    AgentManager::new(AgentManagerConfig::new(
        Arc::new(|| Ok(Box::new(CommanderBenchCognition))),
        Arc::new(|| Ok(Box::new(WorkerBenchCognition))),
    ))
}

fn request_for(index: usize) -> AgentRequest {
    AgentRequest {
        user_id: Some(UserId(format!("bench-user-{index}"))),
        workspace_id: None,
        agent_id: AgentId("bench-agent".to_string()),
        session_id: SessionId(format!("bench-session-{index}")),
        input: format!("benchmark request {index}"),
    }
}

criterion_group!(benches, bench_single_session, bench_concurrent_sessions);
criterion_main!(benches);
