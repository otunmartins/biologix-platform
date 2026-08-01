import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import get_db
from .dependencies import current_user
from .models import Experiment, User
from .schemas import ExperimentCreate, ExperimentResponse, LoginRequest, SignupRequest, UserResponse
from .security import create_token, hash_password, verify_password


router = APIRouter(prefix="/api/platform")
secure_cookie = os.getenv("COOKIE_SECURE", "false").lower() == "true"


def set_session(response: Response, user: User):
    response.set_cookie(
        "session",
        create_token(str(user.id)),
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        max_age=86400,
        path="/",
    )


@router.post("/auth/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, response: Response, db: Session = Depends(get_db)):
    user = User(email=payload.email.lower(), password_hash=hash_password(payload.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    db.refresh(user)
    set_session(response, user)
    return user


@router.post("/auth/login", response_model=UserResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    set_session(response, user)
    return user


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response):
    response.delete_cookie("session", path="/")


@router.get("/auth/me", response_model=UserResponse)
def me(user: User = Depends(current_user)):
    return user


@router.get("/experiments", response_model=list[ExperimentResponse])
def list_experiments(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(Experiment).where(Experiment.owner_id == user.id).order_by(Experiment.created_at.desc())
    ).all()


@router.post("/experiments", response_model=ExperimentResponse, status_code=status.HTTP_201_CREATED)
def create_experiment(payload: ExperimentCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    experiment = Experiment(owner_id=user.id, **payload.model_dump())
    db.add(experiment)
    db.commit()
    db.refresh(experiment)
    return experiment


@router.get("/experiments/{experiment_id}", response_model=ExperimentResponse)
def get_experiment(experiment_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    experiment = db.scalar(
        select(Experiment).where(Experiment.id == experiment_id, Experiment.owner_id == user.id)
    )
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return experiment
