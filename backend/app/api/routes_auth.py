from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session, select

from app.api.deps import get_current_user, get_optional_user
from app.db import get_session
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, UserPublic
from app.services.security import (
    clear_session_cookie,
    hash_password,
    set_session_cookie,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    session: Session = Depends(get_session),
    current_user: User | None = Depends(get_optional_user),
) -> User:
    is_first_user = session.exec(select(User)).first() is None

    # Bootstrap: the very first account becomes admin with no auth required.
    # Every later registration requires an active admin session.
    if not is_first_user and (current_user is None or not current_user.is_admin):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin authentication required"
        )

    existing = session.exec(select(User).where(
        User.username == payload.username)).first()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Username already exists")

    user = User(
        username=payload.username,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        is_admin=is_first_user,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@router.post("/login", response_model=UserPublic)
def login(
    payload: LoginRequest, response: Response, session: Session = Depends(get_session)
) -> User:
    user = session.exec(select(User).where(
        User.username == payload.username)).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password"
        )
    set_session_cookie(response, user.id)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    clear_session_cookie(response)


@router.get("/me", response_model=UserPublic)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
