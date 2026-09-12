from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_tmdb_client, run_participant_guard
from app.db import get_session
from app.models.cache import CachedMovie
from app.models.run import Run, RunParticipant, RunStep
from app.models.user import User
from app.schemas.runs import (
    ParticipantAdd,
    ParticipantPublic,
    RunCreate,
    RunDetail,
    RunStepCreate,
    RunStepPublic,
    RunStepUpdate,
    RunSummary,
    RunUpdate,
)
from app.services import cache_repo
from app.services.tmdb import TMDBClient
from app.utils.ids import utcnow

router = APIRouter(prefix="/runs", tags=["runs"])

VALID_STATUSES = {"active", "completed", "abandoned"}


def _parse_release_year(release_date: str | None) -> int | None:
    if not release_date or len(release_date) < 4:
        return None
    try:
        return int(release_date[:4])
    except ValueError:
        return None


def _step_fields_from_movie(movie: CachedMovie) -> dict:
    return {
        "movie_id": movie.tmdb_id,
        "movie_title": movie.title,
        "movie_poster_path": movie.poster_path,
        "movie_release_year": _parse_release_year(movie.release_date),
        "movie_origin_country": movie.origin_country,
    }


def _check_chain_link(session: Session, run: Run, movie: CachedMovie, force: bool) -> None:
    """Placeholder for `CineChainEngine.validate_next_step` (Phase 5).

    Once the engine layer lands, this should look up the run's last step (if
    any) and raise 409 with the shared-actor validation result unless `force`
    is set. For now it's a no-op so step logging works end-to-end.
    """
    return


def _to_run_detail(session: Session, run: Run) -> RunDetail:
    steps = session.exec(
        select(RunStep).where(RunStep.run_id ==
                              run.id).order_by(RunStep.logged_at)
    ).all()
    participants = session.exec(
        select(RunParticipant)
        .where(RunParticipant.run_id == run.id)
        .order_by(RunParticipant.joined_at)
    ).all()
    return RunDetail(
        **RunSummary.model_validate(run).model_dump(),
        steps=[RunStepPublic.model_validate(s) for s in steps],
        participants=[ParticipantPublic.model_validate(
            p) for p in participants],
    )


@router.get("", response_model=list[RunSummary])
def list_runs(
    status_filter: str | None = Query(default=None, alias="status"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[Run]:
    statement = (
        select(Run)
        .join(RunParticipant, RunParticipant.run_id == Run.id)
        .where(RunParticipant.user_id == current_user.id)
    )
    if status_filter is not None:
        statement = statement.where(Run.status == status_filter)
    statement = statement.order_by(Run.created_at.desc())
    return list(session.exec(statement).all())


@router.post("", response_model=RunDetail, status_code=status.HTTP_201_CREATED)
async def create_run(
    payload: RunCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tmdb: TMDBClient = Depends(get_tmdb_client),
) -> RunDetail:
    participant_ids = {current_user.id, *payload.participant_user_ids}
    for user_id in participant_ids:
        if session.get(User, user_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"User {user_id} not found"
            )

    run = Run(name=payload.name, game_type=payload.game_type)
    session.add(run)
    session.commit()
    session.refresh(run)

    for user_id in participant_ids:
        role = "owner" if user_id == current_user.id else "member"
        session.add(RunParticipant(run_id=run.id, user_id=user_id, role=role))
    session.commit()

    if payload.seed_movie_id is not None:
        movie = await cache_repo.get_movie(session, tmdb, payload.seed_movie_id)
        step = RunStep(
            run_id=run.id,
            logged_by_user_id=current_user.id,
            **_step_fields_from_movie(movie),
        )
        session.add(step)
        session.commit()

    return _to_run_detail(session, run)


@router.get("/{run_id}", response_model=RunDetail)
def get_run(session: Session = Depends(get_session), run: Run = Depends(run_participant_guard)):
    return _to_run_detail(session, run)


@router.patch("/{run_id}", response_model=RunDetail)
def update_run(
    payload: RunUpdate,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
):
    if payload.name is not None:
        run.name = payload.name
    if payload.status is not None:
        if payload.status not in VALID_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid status"
            )
        run.status = payload.status
        run.completed_at = utcnow() if payload.status == "completed" else None
    session.add(run)
    session.commit()
    session.refresh(run)
    return _to_run_detail(session, run)


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(
    session: Session = Depends(get_session), run: Run = Depends(run_participant_guard)
) -> None:
    # No DB-level ON DELETE CASCADE is configured, so children go first.
    for step in session.exec(select(RunStep).where(RunStep.run_id == run.id)).all():
        session.delete(step)
    for participant in session.exec(
        select(RunParticipant).where(RunParticipant.run_id == run.id)
    ).all():
        session.delete(participant)
    # Flush children in their own transaction: no ORM relationship() links
    # Run to its children, so a single flush can't be trusted to order
    # deletes correctly across unrelated mapped tables.
    session.commit()

    session.delete(run)
    session.commit()


@router.post(
    "/{run_id}/participants", response_model=ParticipantPublic, status_code=status.HTTP_201_CREATED
)
def add_participant(
    payload: ParticipantAdd,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
):
    if session.get(User, payload.user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if session.get(RunParticipant, (run.id, payload.user_id)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Already a participant"
        )
    participant = RunParticipant(
        run_id=run.id, user_id=payload.user_id, role=payload.role)
    session.add(participant)
    session.commit()
    session.refresh(participant)
    return participant


@router.delete("/{run_id}/participants/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_participant(
    user_id: str,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
) -> None:
    participant = session.get(RunParticipant, (run.id, user_id))
    if participant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Participant not found")
    session.delete(participant)
    session.commit()


@router.post("/{run_id}/steps", response_model=RunStepPublic, status_code=status.HTTP_201_CREATED)
async def create_step(
    payload: RunStepCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    run: Run = Depends(run_participant_guard),
    tmdb: TMDBClient = Depends(get_tmdb_client),
):
    movie = await cache_repo.get_movie(session, tmdb, payload.movie_id)
    _check_chain_link(session, run, movie, payload.force)

    step = RunStep(
        run_id=run.id,
        transition_metadata=payload.transition_metadata,
        user_notes=payload.user_notes,
        logged_by_user_id=current_user.id,
        **_step_fields_from_movie(movie),
    )
    session.add(step)
    session.commit()
    session.refresh(step)
    return step


@router.patch("/{run_id}/steps/{step_id}", response_model=RunStepPublic)
def update_step(
    step_id: str,
    payload: RunStepUpdate,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
):
    step = session.get(RunStep, step_id)
    if step is None or step.run_id != run.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Step not found")
    if payload.user_notes is not None:
        step.user_notes = payload.user_notes
    if payload.transition_metadata is not None:
        step.transition_metadata = payload.transition_metadata
    session.add(step)
    session.commit()
    session.refresh(step)
    return step


@router.delete("/{run_id}/steps/{step_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_step(
    step_id: str,
    session: Session = Depends(get_session),
    run: Run = Depends(run_participant_guard),
) -> None:
    step = session.get(RunStep, step_id)
    if step is None or step.run_id != run.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Step not found")

    last_step = session.exec(
        select(RunStep).where(RunStep.run_id == run.id).order_by(
            RunStep.logged_at.desc())
    ).first()
    if last_step is None or last_step.id != step.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only the most recently logged step can be deleted",
        )

    session.delete(step)
    session.commit()
