
from pydantic import BaseModel, Field


class BackendEndpoint(BaseModel):
    name: str
    method: str
    path: str
    purpose: str
    authentication_required: bool = False
    request_fields: list[str] = Field(default_factory=list)
    response_fields: list[str] = Field(default_factory=list)

class FrontendAPIRequirement(BaseModel):
    """An API requirement discovered from generated frontend files."""

    source_file: str
    method: str
    path: str
    purpose: str = ""
    request_fields: list[str] = Field(default_factory=list)
    response_fields: list[str] = Field(default_factory=list)
    authentication_required: bool = False
    
    
class DatabaseField(BaseModel):
    name: str
    data_type: str
    required: bool = True
    description: str = ""


class DatabaseRelationship(BaseModel):
    type: str
    related_model: str
    field: str


class DatabaseIndex(BaseModel):
    fields: list[str]
    unique: bool = False


class DatabaseModel(BaseModel):
    name: str
    description: str
    fields: list[DatabaseField]
    relationships: list[DatabaseRelationship] = Field(
        default_factory=list
    )
    indexes: list[DatabaseIndex] = Field(
        default_factory=list
    )


class BackendTask(BaseModel):
    task_id: str
    title: str
    description: str
    dependencies: list[str] = Field(default_factory=list)


class BackendPlan(BaseModel):
    business_name: str
    project_name: str
    framework: str
    storage_type: str = "json"
    storage_path: str = "data/"
    architecture: str
    authentication: list[str] = Field(default_factory=list)
    security_requirements: list[str] = Field(default_factory=list)
    endpoints: list[BackendEndpoint] = Field(default_factory=list)
    database_models: list[DatabaseModel] = Field(default_factory=list)
    tasks: list[BackendTask]
    assumptions: list[str] = Field(default_factory=list)
