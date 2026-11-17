from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


EndpointType = Literal[
    "crud",
    "authentication",
    "registration",
    "search",
    "upload",
    "payment",
    "custom",
]


class EndpointSecurityMetadata(BaseModel):
    """Additional security requirements for an API endpoint."""

    model_config = ConfigDict(extra="allow")

    roles: list[str] = Field(default_factory=list)
    auth_scheme: str | None = None
    permissions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class EndpointHandlerMetadata(BaseModel):
    """Describes how an endpoint should be implemented."""

    model_config = ConfigDict(extra="allow")

    operation: str | None = None
    service: str | None = None
    resource: str | None = None
    custom_logic: bool = False
    notes: list[str] = Field(default_factory=list)


class BackendEndpoint(BaseModel):
    """A backend API endpoint and its implementation requirements.

    Existing fields are intentionally preserved so older planners,
    reconcilers and generators can continue constructing this model.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    method: str
    path: str
    purpose: str

    # Existing fields — keep their names and defaults.
    authentication_required: bool = False
    request_fields: list[str] = Field(default_factory=list)
    response_fields: list[str] = Field(default_factory=list)

    # New structured endpoint metadata.
    endpoint_type: EndpointType = "crud"
    request_schema: dict[str, Any] = Field(default_factory=dict)
    response_schema: dict[str, Any] = Field(default_factory=dict)
    security: EndpointSecurityMetadata = Field(
        default_factory=EndpointSecurityMetadata
    )
    handler: EndpointHandlerMetadata = Field(
        default_factory=EndpointHandlerMetadata
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    
    @model_validator(mode="before")
    @classmethod
    def infer_legacy_endpoint_type(cls, values: Any) -> Any:
        """Infer endpoint types while prioritizing specific route actions."""

        if not isinstance(values, dict):
            return values

        if values.get("endpoint_type"):
            return values

        path = str(values.get("path", "")).lower().rstrip("/")
        method = str(values.get("method", "GET")).upper()
        purpose = str(values.get("purpose", "")).lower()

        if any(word in path for word in (
            "/register", "/signup", "/sign-up"
        )):
            endpoint_type = "registration"

        elif any(word in path for word in (
            "/login", "/logout", "/token", "/auth"
        )):
            endpoint_type = "authentication"

        elif any(word in path for word in (
            "/upload", "/uploads"
        )):
            endpoint_type = "upload"

        elif any(word in path for word in (
            "/payment", "/payments", "/checkout"
        )):
            endpoint_type = "payment"

        elif "search" in path or "search" in purpose:
            endpoint_type = "search"

        elif method in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            endpoint_type = "crud"

        else:
            endpoint_type = "custom"

        values["endpoint_type"] = endpoint_type
        return values


    @model_validator(mode="after")
    def synchronize_security_metadata(self) -> "BackendEndpoint":
        """Keep the legacy authentication flag consistent with security data."""

        if self.authentication_required:
            if not self.security.auth_scheme:
                self.security.auth_scheme = "bearer"
        elif self.security.roles or self.security.permissions:
            # Roles/permissions imply that this endpoint must be protected.
            self.authentication_required = True
            if not self.security.auth_scheme:
                self.security.auth_scheme = "bearer"

        if self.endpoint_type in {"authentication", "registration"}:
            # Login and registration are commonly public endpoints.
            # Do not automatically require authentication for them.
            pass

        return self


class FrontendAPIRequirement(BaseModel):
    """An API requirement discovered from generated frontend files."""

    source_file: str
    method: str
    path: str
    purpose: str = ""
    request_fields: list[str] = Field(default_factory=list)
    response_fields: list[str] = Field(default_factory=list)
    authentication_required: bool = False

    # Optional structured fields; old analyzer output remains valid.
    endpoint_type: EndpointType = "crud"
    request_schema: dict[str, Any] = Field(default_factory=dict)
    response_schema: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


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