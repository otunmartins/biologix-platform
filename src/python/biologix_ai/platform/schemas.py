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
    created_at: datetime
    updated_at: datetime
