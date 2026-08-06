from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .models import ExperimentState


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: EmailStr
    created_at: datetime
    is_admin: bool = False


class AdminOverview(BaseModel):
    users: int
    experiments: int
    queued: int
    running: int
    done: int
    failed: int
    workers: int
    queued_jobs: int
    worker_available: bool
    queue_error: str | None = None


class AdminUserResponse(UserResponse):
    experiment_count: int


class AdminExperimentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    owner_email: EmailStr
    name: str
    biologic_target: str
    polymer_target: str | None
    status: ExperimentState
    progress: int
    current_stage: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    biologic_target: str = Field(min_length=1, max_length=200)
    polymer_target: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=2000)
    parameters: dict = Field(default_factory=dict)


class ExperimentResponse(ExperimentCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: ExperimentState
    results: dict | None
    progress: int
    current_stage: str | None
    progress_log: list[dict]
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    job_id: str | None
    created_at: datetime
    updated_at: datetime


class ArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
