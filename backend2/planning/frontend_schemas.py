
from pydantic import BaseModel, Field


class FrontendPage(BaseModel):
    name: str
    route: str
    purpose: str
    components: list[str] = Field(default_factory=list)
    requires_auth: bool = False


class FrontendTask(BaseModel):
    task_id: str
    title: str
    description: str
    dependencies: list[str] = Field(default_factory=list)


class DesignSystem(BaseModel):
    description: str = ""
    colors: dict[str, str] = Field(default_factory=dict)
    typography: dict[str, str] = Field(default_factory=dict)
    component_library: str = ""


class StateManagement(BaseModel):
    library: str = ""
    stores: list[str] = Field(default_factory=list)


class APIIntegration(BaseModel):
    name: str
    endpoint: str = ""
    method: str = ""
    purpose: str = ""
    request_fields: list[str] = Field(default_factory=list)
    response_fields: list[str] = Field(default_factory=list)
    authentication_required: bool = False


class FrontendPlan(BaseModel):
    business_name: str
    project_name: str
    framework: str
    styling: str
    design_system: DesignSystem
    pages: list[FrontendPage]
    shared_components: list[str] = Field(default_factory=list)
    state_management: StateManagement
    api_integrations: list[APIIntegration] = Field(default_factory=list)
    tasks: list[FrontendTask]
