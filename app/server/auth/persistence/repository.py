from typing import List, Optional, Tuple

from pydantic import EmailStr

from app.server.auth.persistence.user import User
from app.server.persistence.repository_base import BaseRepository
from app.server.api.schemas import Pager


class UserRepository(BaseRepository[User]):
    def __init__(self) -> None:
        self.model = User

    async def get_by_username(self, username: str) -> Optional[User]:
        return await self.model.filter(username=username).first()

    async def get_by_email(self, email: EmailStr) -> Optional[User]:
        return await self.model.filter(email=email).first()

    async def get_users(
        self,
        username: Optional[str] = None,
        pager: Optional[Pager] = None,
        order_by: Optional[str] = "-created_at",
    ) -> Tuple[List[User], int]:
        if username:
            query = self.model.filter(username__contains=username)
        else:
            query = self.model.all()
        return await self._paginate(query, pager, order_by)

    async def create_user(
        self,
        username: str,
        email: EmailStr,
        hashed_password: str,
    ) -> User:
        return await self.create(
            username=username,
            email=email,
            hashed_password=hashed_password,
        )

    async def update_password(self, user_id: int, hashed_password: str) -> None:
        await self.model.filter(id=user_id).update(hashed_password=hashed_password)

    async def batch_get_by_ids(self, ids: List[int]) -> List[User]:
        return await self.model.filter(id__in=ids).all()
