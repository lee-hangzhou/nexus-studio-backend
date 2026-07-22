from app.server.infra.security import get_password_hash
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.auth.persistence.repository import UserRepository
from app.server.auth.schemas.schemas_user import UserCreate, UserInfo


class UserService:
    def __init__(self) -> None:
        self.repo = UserRepository()

    async def create_user(self, data: UserCreate) -> UserInfo:
        existing = await self.repo.get_by_username(data.username)
        if existing:
            raise AppError(ErrorCode.USER_ALREADY_EXISTS)

        existing = await self.repo.get_by_email(data.email)
        if existing:
            raise AppError(ErrorCode.USER_ALREADY_EXISTS)

        hashed_password = get_password_hash(data.password)
        user = await self.repo.create_user(
            username=data.username,
            email=data.email,
            hashed_password=hashed_password,
        )

        return UserInfo.model_validate(user)

    async def get_user_by_id(self, user_id: int) -> UserInfo:
        user = await self.repo.get_by_id(user_id)
        if not user:
            raise AppError(ErrorCode.USER_NOT_FOUND)

        return UserInfo.model_validate(user)

