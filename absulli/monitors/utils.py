from sqlalchemy.orm import Session

from absulli.database.models import AbsUser


def friendly_names(db: Session) -> dict[str, str]:
    return {
        user.abs_user_id: (user.display_name or user.username or user.abs_user_id)
        for user in db.query(AbsUser).all()
    }
