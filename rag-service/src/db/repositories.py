from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import KnowledgeBase, ModelSetting, User
from src.shared.errors import AppError


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_first_admin(self, username: str, password_hash: str) -> User:
        normalized = username.strip()
        if not normalized:
            raise AppError("USERNAME_REQUIRED", "用户名不能为空")

        user_count = await self._session.scalar(select(func.count(User.id)))
        if user_count:
            raise AppError(
                "ADMIN_ALREADY_INITIALIZED",
                "首个账户管理员已经初始化",
                status_code=409,
            )

        user = User(
            username=normalized,
            password_hash=password_hash,
            role="account_admin",
            status="active",
        )
        self._session.add(user)
        await self._session.commit()
        await self._session.refresh(user)
        return user


class KnowledgeBaseRepository:
    MAX_PER_USER = 20

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count_by_owner(self, owner_user_id: int) -> int:
        return await self._session.scalar(
            select(func.count(KnowledgeBase.id)).where(
                KnowledgeBase.owner_user_id == owner_user_id,
                KnowledgeBase.enabled == True,
            )
        ) or 0

    async def create(
        self,
        *,
        owner_user_id: int,
        name: str,
        description: str | None,
        embedding_model: str,
        embedding_dimension: int,
        chunk_size: int,
        overlap: int,
    ) -> KnowledgeBase:
        if not name.strip():
            raise AppError("KB_NAME_REQUIRED", "知识库名称不能为空")

        count = await self.count_by_owner(owner_user_id)
        if count >= self.MAX_PER_USER:
            raise AppError("KB_LIMIT_REACHED", f"每个用户最多 {self.MAX_PER_USER} 个知识库", status_code=409)

        kb = KnowledgeBase(
            owner_user_id=owner_user_id,
            name=name.strip(),
            description=description.strip() if description else None,
            chunk_size=chunk_size,
            overlap=overlap,
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
            active_collection="",  # set after flush
        )
        self._session.add(kb)
        await self._session.flush()
        kb.active_collection = f"kb_{kb.id}_v1"
        await self._session.commit()
        await self._session.refresh(kb)
        return kb

    async def list_all(self) -> list[KnowledgeBase]:
        """全员可见：返回所有启用中的知识库，不按 owner 过滤。"""
        result = await self._session.execute(
            select(KnowledgeBase)
            .where(KnowledgeBase.enabled == True)
            .order_by(KnowledgeBase.updated_at.desc())
        )
        return list(result.scalars().all())

    async def get_owned(self, kb_id: int, owner_user_id: int) -> KnowledgeBase | None:
        return await self._session.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.id == kb_id,
                KnowledgeBase.owner_user_id == owner_user_id,
            )
        )

    async def get_owned_many(self, kb_ids: list[int], owner_user_id: int) -> dict[int, KnowledgeBase]:
        """批量查询本人拥有的知识库，返回 {kb_id: KnowledgeBase}。"""
        result = await self._session.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id.in_(kb_ids),
                KnowledgeBase.owner_user_id == owner_user_id,
            )
        )
        return {kb.id: kb for kb in result.scalars().all()}


class ModelSettingRepository:
    """模型配置持久化：单行（id=1）的读与 upsert。"""

    SETTING_ID = 1

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self) -> ModelSetting | None:
        return await self._session.scalar(
            select(ModelSetting).where(ModelSetting.id == self.SETTING_ID)
        )

    async def upsert(self, values: dict[str, str | None]) -> None:
        """已有行则更新（embedding 缺省列回写 NULL），否则插入 id=1。"""
        setting = await self.get()
        if setting is None:
            setting = ModelSetting(
                id=self.SETTING_ID,
                base_url=values["base_url"],
                api_key=values["api_key"],
                chat_model=values["chat_model"],
                embedding_model=values["embedding_model"],
                embedding_base_url=values.get("embedding_base_url"),
                embedding_api_key=values.get("embedding_api_key"),
            )
            self._session.add(setting)
        else:
            setting.base_url = values["base_url"]
            setting.api_key = values["api_key"]
            setting.chat_model = values["chat_model"]
            setting.embedding_model = values["embedding_model"]
            setting.embedding_base_url = values.get("embedding_base_url")
            setting.embedding_api_key = values.get("embedding_api_key")
        await self._session.commit()

