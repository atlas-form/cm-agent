from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class SkillBase:
    name: str
    display_name: str
    description: str
    category: str  # role name: ops, data, service, design, accounting, engineering, web, creative, coordination
    input_schema: Dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    def to_tool_spec(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        raise NotImplementedError(f"Skill {self.name} not implemented")
