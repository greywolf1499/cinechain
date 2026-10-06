"""Golden Veto tokens: a monthly allowance, refilled lazily - there is no background job."""

from datetime import UTC, datetime, timedelta

from app.engines.rulebook import RuleSection

RULEBOOK = RuleSection(
    "Overrule a partner's latest decision.",
    [
        "Spend an available Golden Veto to undo their latest contested step or cancel their pending fork."
    ],
    ["Your account holds one token, refilled every 30 days; undo rebuilds derived progress."],
    ["You cannot veto your own step or a seed; co-op tunnel steps use Undo instead."],
    ["Save the token for a decision that closes valuable future options."],
    ["veto", "fork"],
)

from sqlalchemy import update
from sqlmodel import Session

from app.models.user import User
from app.utils.ids import utcnow

VETO_RESET_INTERVAL = timedelta(days=30)
VETO_ALLOWANCE = 1


def _aware(moment: datetime) -> datetime:
    # SQLite hands datetimes back naive; everything stored here is UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def refresh_veto_tokens(session: Session, user: User, now: datetime | None = None) -> bool:
    """JIT refill: once more than 30 days have passed since the last reset the user is back to
    one token. Returns whether a reset happened. Runs on session resolution, so it needs no
    scheduler."""
    now = now or utcnow()
    if now - _aware(user.last_veto_reset_at) <= VETO_RESET_INTERVAL:
        return False
    user.veto_tokens = VETO_ALLOWANCE
    user.last_veto_reset_at = now
    session.add(user)
    session.commit()
    session.refresh(user)
    return True


def consume_veto_token(session: Session, user: User) -> bool:
    """Spend one token. Atomic (`WHERE veto_tokens > 0`) so two concurrent requests can't both
    spend the last one. The caller commits."""
    result = session.execute(
        update(User)
        .where(User.id == user.id, User.veto_tokens > 0)  # type: ignore[arg-type]
        .values(veto_tokens=User.veto_tokens - 1)
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        return False
    session.refresh(user)
    return True
