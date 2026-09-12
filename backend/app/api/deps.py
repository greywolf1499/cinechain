from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session

from app.db import get_session
from app.models.run import Run, RunParticipant
from app.models.user import User
from app.services.security import COOKIE_NAME, read_session_token
from app.services.tmdb import TMDBClient


def get_optional_user(
    request: Request, session: Session = Depends(get_session)
) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    user_id = read_session_token(token)
    if user_id is None:
        return None
    return session.get(User, user_id)


def get_current_user(user: User | None = Depends(get_optional_user)) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
    return user


def run_participant_guard(
    run_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Run:
    """404 (never 403) for both a missing run and a run the user isn't part of -
    participants shouldn't be able to tell those two cases apart."""
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    membership = session.get(RunParticipant, (run_id, current_user.id))
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


def get_tmdb_client(request: Request) -> TMDBClient:
    return request.app.state.tmdb
