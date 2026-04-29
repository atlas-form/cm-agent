use agent_error::{CognitionError, Result};
use cognition::{Cognition, CognitionEngine};
use world::get_default_llm;

const COMMANDER_COGNITION_PROMPT_PATH: &str = "prompts/zh/cognition/commander_routing.md";
const WORKER_COGNITION_PROMPT_PATH: &str = "prompts/zh/cognition/worker_execution.md";

pub fn build_commander_cognition() -> Result<Box<dyn Cognition + Send>> {
    build_cognition(COMMANDER_COGNITION_PROMPT_PATH)
}

pub fn build_worker_cognition() -> Result<Box<dyn Cognition + Send>> {
    build_cognition(WORKER_COGNITION_PROMPT_PATH)
}

fn build_cognition(prompt_path: &str) -> Result<Box<dyn Cognition + Send>> {
    let llm = get_default_llm()
        .ok_or_else(|| CognitionError::internal("no llm registered in world manager"))?;

    let engine = CognitionEngine::new(llm, prompt_path)
        .map_err(|err| CognitionError::internal(err.to_string()))?;
    Ok(Box::new(engine))
}

#[cfg(test)]
mod tests {
    use cognition::{CognitionInput, CognitionResult, Context, Fact, Intent, IntentKind};

    use super::build_commander_cognition;
    use crate::startup::{settings::Settings, world::init_shared_services};

    #[tokio::test]
    #[ignore = "requires running chat completions-compatible service and model"]
    async fn chat_completions_cognition_smoke() {
        let settings = Settings::load_default().expect("load settings failed");
        init_shared_services(&settings).expect("world init failed");
        let cognition = build_commander_cognition().expect("build cognition failed");

        let input = CognitionInput {
            intent: Intent {
                id: "smoke-chat-completions-cognition".to_string(),
                kind: IntentKind::Decision,
                description: "判断是否应该执行一次简单任务".to_string(),
            },
            context: Context {
                facts: vec![
                    Fact {
                        source: "test".to_string(),
                        content: "当前无阻塞风险".to_string(),
                        reliability: 0.9,
                    },
                    Fact {
                        source: "test".to_string(),
                        content: "任务成本很低".to_string(),
                        reliability: 0.8,
                    },
                ],
                metadata: Default::default(),
            },
        };

        let result = cognition.evaluate(input).await;
        match result {
            CognitionResult::Success(output) => {
                println!("smoke cognition success: {}", output["decision"]["kind"]);
                assert!(output.get("decision").is_some());
                assert!(output.get("confidence").is_some());
                assert!(output.get("rationale").is_some());
            }
            CognitionResult::Failure(failure) => {
                panic!("smoke cognition failed: {:?}", failure);
            }
        }
    }
}
