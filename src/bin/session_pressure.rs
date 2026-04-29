use std::{
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    time::{Duration, Instant},
};

use async_trait::async_trait;
use cm_agent::api::{
    AgentId, AgentManager, AgentManagerConfig, AgentRequest, Cognition, CognitionInput,
    CognitionResult, Result, SessionId, UserId,
};
use serde_json::json;

struct CommanderBenchCognition;

#[async_trait]
impl Cognition for CommanderBenchCognition {
    async fn evaluate(&self, _input: CognitionInput) -> CognitionResult {
        CognitionResult::Success(json!({
            "decision": {
                "kind": "RouteTask",
                "route": {
                    "target_agent_id": "worker-1",
                    "task_summary": "pressure request",
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
                "primary": "session pressure test",
                "evidence": [],
                "alternatives_considered": []
            }
        }))
    }
}

struct WorkerBenchCognition {
    delay: Duration,
    hold_data: Arc<String>,
}

#[async_trait]
impl Cognition for WorkerBenchCognition {
    async fn evaluate(&self, _input: CognitionInput) -> CognitionResult {
        if !self.delay.is_zero() {
            tokio::time::sleep(self.delay).await;
        }
        let held_bytes = self.hold_data.len();
        CognitionResult::Success(json!({
            "decision": {
                "kind": "NoAction",
                "held_bytes": held_bytes
            }
        }))
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let total = env_usize("SESSION_PRESSURE_TOTAL").unwrap_or(10000);
    let worker_delay_ms = env_u64("SESSION_PRESSURE_WORKER_DELAY_MS").unwrap_or(10000);
    let payload_kb = env_usize("SESSION_PRESSURE_PAYLOAD_KB").unwrap_or(0);
    let progress_interval_secs = env_u64("SESSION_PRESSURE_PROGRESS_SECS").unwrap_or(1);
    let worker_delay = Duration::from_millis(worker_delay_ms);
    let shared_payload = Arc::new(build_payload(payload_kb));

    let manager = Arc::new(AgentManager::new(AgentManagerConfig::new(
        Arc::new(|| Ok(Box::new(CommanderBenchCognition))),
        Arc::new(move || {
            Ok(Box::new(WorkerBenchCognition {
                delay: worker_delay,
                hold_data: Arc::clone(&shared_payload),
            }))
        }),
    )));

    let started_at = Instant::now();
    let completed = Arc::new(AtomicUsize::new(0));
    let failed_count = Arc::new(AtomicUsize::new(0));
    let progress_done = Arc::new(AtomicBool::new(false));

    let progress_task = spawn_progress_reporter(
        Arc::clone(&manager),
        Arc::clone(&completed),
        Arc::clone(&failed_count),
        Arc::clone(&progress_done),
        started_at,
        progress_interval_secs,
    );

    let mut handles = Vec::with_capacity(total);
    for index in 0 .. total {
        let manager = Arc::clone(&manager);
        let completed = Arc::clone(&completed);
        let failed_count = Arc::clone(&failed_count);
        let input = build_request_input(index, payload_kb);

        handles.push(tokio::spawn(async move {
            let result = manager
                .run_request(AgentRequest {
                    user_id: Some(UserId(format!("user-{index}"))),
                    workspace_id: None,
                    agent_id: AgentId("pressure-agent".to_string()),
                    session_id: SessionId(format!("session-{index}")),
                    input,
                })
                .await;

            match result {
                Ok(result) => {
                    completed.fetch_add(1, Ordering::Relaxed);
                    Ok(result)
                }
                Err(err) => {
                    failed_count.fetch_add(1, Ordering::Relaxed);
                    Err(err)
                }
            }
        }));
    }

    let mut ok = 0usize;
    let mut failed = 0usize;
    for handle in handles {
        match handle.await.expect("pressure task panicked") {
            Ok(_) => ok += 1,
            Err(err) => {
                failed += 1;
                eprintln!("request failed: {err}");
            }
        }
    }

    let elapsed = started_at.elapsed();
    progress_done.store(true, Ordering::Relaxed);
    progress_task.abort();
    let _ = progress_task.await;
    let rps = ok as f64 / elapsed.as_secs_f64();
    let active_sessions = manager.active_session_count();

    println!("total={total}");
    println!("worker_delay_ms={worker_delay_ms}");
    println!("payload_kb={payload_kb}");
    println!("ok={ok}");
    println!("failed={failed}");
    println!("active_sessions={active_sessions}");
    println!("elapsed_ms={}", elapsed.as_millis());
    println!("throughput_per_sec={rps:.2}");

    assert_eq!(ok, total);
    assert_eq!(failed, 0);
    assert_eq!(active_sessions, 0);
    Ok(())
}

fn spawn_progress_reporter(
    manager: Arc<AgentManager>,
    completed: Arc<AtomicUsize>,
    failed: Arc<AtomicUsize>,
    done: Arc<AtomicBool>,
    started_at: Instant,
    interval_secs: u64,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        let interval = Duration::from_secs(interval_secs.max(1));
        while !done.load(Ordering::Relaxed) {
            tokio::time::sleep(interval).await;
            eprintln!(
                "progress elapsed_ms={} completed={} failed={} active_sessions={}",
                started_at.elapsed().as_millis(),
                completed.load(Ordering::Relaxed),
                failed.load(Ordering::Relaxed),
                manager.active_session_count()
            );
        }
    })
}

fn env_usize(key: &str) -> Option<usize> {
    std::env::var(key).ok()?.parse().ok()
}

fn env_u64(key: &str) -> Option<u64> {
    std::env::var(key).ok()?.parse().ok()
}

fn build_request_input(index: usize, payload_kb: usize) -> String {
    if payload_kb == 0 {
        return format!("pressure request {index}");
    }

    format!(
        "pressure request {index}\n\ncontext:\n{}",
        build_payload(payload_kb)
    )
}

fn build_payload(payload_kb: usize) -> String {
    if payload_kb == 0 {
        return String::new();
    }

    let target_len = payload_kb.saturating_mul(1024);
    let line = "session-context-data: abcdefghijklmnopqrstuvwxyz0123456789\n";
    let mut payload = String::with_capacity(target_len);
    while payload.len() < target_len {
        payload.push_str(line);
    }
    payload.truncate(target_len);
    payload
}
