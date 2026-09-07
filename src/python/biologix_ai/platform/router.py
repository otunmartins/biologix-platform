import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import get_db
from .dependencies import admin_user, current_user, is_admin
from .extractions import apply_to_parameters, preflight
from .models import Experiment, ExperimentArtifact, User
from .schemas import (
    AdminExperimentResponse,
    AdminOverview,
    AdminUserResponse,
    ArtifactResponse,
    ExperimentCreate,
    ExperimentResponse,
    ExperimentRetry,
    ExtractionPreflightRequest,
    ExtractionPreflightResponse,
    LoginRequest,
    SignupRequest,
    UserResponse,
)
from .security import create_token, hash_password, verify_password
from .queue import enqueue_experiment
from .storage import ArtifactStorage


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
    response = UserResponse.model_validate(user).model_dump()
    response["is_admin"] = is_admin(user)
    return response


@router.get("/admin/overview", response_model=AdminOverview)
def admin_overview(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    counts = dict(db.execute(select(Experiment.status, func.count()).group_by(Experiment.status)).all())
    queue = {"workers": 0, "queued_jobs": 0, "worker_available": False, "queue_error": None}
    try:
        from rq import Worker
        from .queue import experiment_queue, redis_connection

        experiment_queue_instance = experiment_queue()
        workers = Worker.all(connection=redis_connection(), queue=experiment_queue_instance)
        queue.update(workers=len(workers), queued_jobs=experiment_queue_instance.count, worker_available=bool(workers))
    except Exception as exc:
        queue["queue_error"] = str(exc)
    return {
        "users": db.scalar(select(func.count()).select_from(User)) or 0,
        "experiments": db.scalar(select(func.count()).select_from(Experiment)) or 0,
        **{state: counts.get(state, 0) for state in ("queued", "running", "done", "failed")},
        **queue,
    }


@router.get("/admin/users", response_model=list[AdminUserResponse])
def admin_users(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(User, func.count(Experiment.id))
        .outerjoin(Experiment, Experiment.owner_id == User.id)
        .group_by(User.id)
        .order_by(User.created_at.desc())
        .limit(100)
    ).all()
    return [{**UserResponse.model_validate(user).model_dump(), "experiment_count": count} for user, count in rows]


@router.get("/admin/experiments", response_model=list[AdminExperimentResponse])
def admin_experiments(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Experiment, User.email)
        .join(User, User.id == Experiment.owner_id)
        .order_by(Experiment.created_at.desc())
        .limit(200)
    ).all()
    return [{
        "id": experiment.id,
        "owner_email": email,
        "name": experiment.name,
        "biologic_target": experiment.biologic_target,
        "polymer_target": experiment.polymer_target,
        "status": experiment.status,
        "progress": experiment.progress,
        "current_stage": experiment.current_stage,
        "error_message": experiment.error_message,
        "created_at": experiment.created_at,
        "updated_at": experiment.updated_at,
    } for experiment, email in rows]


@router.get("/experiments", response_model=list[ExperimentResponse])
def list_experiments(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(Experiment).where(Experiment.owner_id == user.id).order_by(Experiment.created_at.desc())
    ).all()


@router.post("/retrosynthesis/preflight", response_model=ExtractionPreflightResponse)
def preflight_extractions(payload: ExtractionPreflightRequest, _: User = Depends(current_user)):
    """Check synthesis evidence against the engine's own rules before an experiment is queued."""
    try:
        return preflight(payload.material_name.strip(), payload.sources)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/experiments", response_model=ExperimentResponse, status_code=status.HTTP_201_CREATED)
def create_experiment(payload: ExperimentCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    fields = payload.model_dump()
    try:
        fields["parameters"] = apply_to_parameters(fields.get("parameters"), fields.get("polymer_target"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    experiment = Experiment(owner_id=user.id, **fields)
    db.add(experiment)
    db.commit()
    db.refresh(experiment)
    try:
        experiment.job_id = enqueue_experiment(str(experiment.id))
        db.commit()
        db.refresh(experiment)
    except Exception as exc:
        experiment.status = "failed"
        experiment.error_message = f"Unable to queue experiment: {exc}"
        db.commit()
        db.refresh(experiment)
    return experiment


@router.post("/experiments/{experiment_id}/retry", response_model=ExperimentResponse)
def retry_experiment(
    experiment_id: uuid.UUID,
    payload: ExperimentRetry | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    experiment = db.scalar(select(Experiment).where(Experiment.id == experiment_id, Experiment.owner_id == user.id))
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    if experiment.status == "running":
        raise HTTPException(status_code=409, detail="Experiment is already running")
    if payload and payload.parameters is not None:
        merged = {**(experiment.parameters or {}), **payload.parameters}
        try:
            experiment.parameters = apply_to_parameters(merged, experiment.polymer_target)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    experiment.status = "queued"
    experiment.progress = 0
    experiment.current_stage = "queued"
    experiment.error_message = None
    experiment.results = None
    experiment.progress_log = []
    experiment.job_id = enqueue_experiment(str(experiment.id))
    db.commit()
    db.refresh(experiment)
    return experiment


@router.get("/experiments/{experiment_id}/artifacts", response_model=list[ArtifactResponse])
def list_artifacts(experiment_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ownership_query = select(Experiment.id).where(Experiment.id == experiment_id)
    if not is_admin(user):
        ownership_query = ownership_query.where(Experiment.owner_id == user.id)
    owned = db.scalar(ownership_query)
    if not owned:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return db.scalars(
        select(ExperimentArtifact)
        .where(ExperimentArtifact.experiment_id == experiment_id)
        .order_by(ExperimentArtifact.created_at)
    ).all()


@router.get("/experiments/{experiment_id}/artifacts/{artifact_id}")
def download_artifact(
    experiment_id: uuid.UUID,
    artifact_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    query = (
        select(ExperimentArtifact)
        .join(Experiment, Experiment.id == ExperimentArtifact.experiment_id)
        .where(ExperimentArtifact.id == artifact_id, ExperimentArtifact.experiment_id == experiment_id)
    )
    if not is_admin(user):
        query = query.where(Experiment.owner_id == user.id)
    artifact = db.scalar(query)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    obj = ArtifactStorage().get(artifact.object_key)
    return StreamingResponse(
        obj["Body"].iter_chunks(),
        media_type=artifact.content_type,
        headers={"Content-Disposition": f'attachment; filename="{artifact.filename}"'},
    )


@router.get("/worker/status")
def worker_status(user: User = Depends(current_user)):
    from rq import Worker
    from .queue import experiment_queue, redis_connection

    connection = redis_connection()
    workers = Worker.all(connection=connection, queue=experiment_queue())
    return {
        "available": bool(workers),
        "workers": len(workers),
        "queued_jobs": experiment_queue().count,
    }


@router.get("/experiments/{experiment_id}", response_model=ExperimentResponse)
def get_experiment(experiment_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(Experiment).where(Experiment.id == experiment_id)
    if not is_admin(user):
        query = query.where(Experiment.owner_id == user.id)
    experiment = db.scalar(query)
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return experiment
