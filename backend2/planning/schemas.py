
from typing import Literal

from pydantic import BaseModel, Field


class ProjectTask(BaseModel):
    task_id: str
    title: str
    description: str
    department: Literal[
        "frontend", "backend", "database", "devops"
    ]
    dependencies: list[str] = Field(default_factory=list)
    priority: Literal["high", "medium", "low"] = "medium"



class MasterPlan(BaseModel):
    project_name: str
    business_name: str
    project_goal: str
    architecture: str
    tech_stack: dict[str, str | list[str]]
    features: list[str]
    tasks: list[ProjectTask]
    assumptions: list[str] = Field(default_factory=list)

