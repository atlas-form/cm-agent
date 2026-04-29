#[derive(Debug, Clone, PartialEq, Eq)]
pub enum UiEvent {
    UserInput(String),
    Help,
    Quit,
    Empty,
}

impl UiEvent {
    pub fn parse(line: String) -> Self {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            return Self::Empty;
        }

        match trimmed {
            "/q" | "/quit" | "/exit" => Self::Quit,
            "/h" | "/help" => Self::Help,
            _ => Self::UserInput(trimmed.to_string()),
        }
    }
}
