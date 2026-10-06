from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.api.deps import get_current_user
from app.db import get_session
from app.models.user import User
from app.schemas.auth import UserSummary
from app.services.veto import refresh_veto_tokens

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserSummary])
def list_users(
    session: Session = Depends(get_session),
    _current_user: User = Depends(get_current_user),
) -> list[User]:
    users = list(session.exec(select(User)).all())
    for user in users:
        refresh_veto_tokens(session, user)
    return users
