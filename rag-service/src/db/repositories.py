from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import KnowledgeBase, ModelSetting, User, VideoSetting, VideoTaskRecord
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
        tenant_id: str | None = None,
        department_id: str | None = None,
    ) -> KnowledgeBase:
        if not name.strip():
            raise AppError("KB_NAME_REQUIRED", "知识库名称不能为空")

        count = await self.count_by_owner(owner_user_id)
        if count >= self.MAX_PER_USER:
            raise AppError("KB_LIMIT_REACHED", f"每个用户最多 {self.MAX_PER_USER} 个知识库", status_code=409)

        kb = KnowledgeBase(
            owner_user_id=owner_user_id,
            tenant_id=tenant_id,
            department_id=department_id,
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

    async def list_all(self, scope_condition=None) -> list[KnowledgeBase]:
        """返回启用中的知识库（按租户与 dataScope 过滤）。"""
        conditions = [KnowledgeBase.enabled == True]
        if scope_condition is not None:
            conditions.append(scope_condition)
        result = await self._session.execute(
            select(KnowledgeBase)
            .where(*conditions)
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


class VideoSettingRepository:
    """视频解析 agent 设置持久化：单行（id=1）的读与 upsert。"""

    SETTING_ID = 1

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self) -> VideoSetting | None:
        return await self._session.scalar(
            select(VideoSetting).where(VideoSetting.id == self.SETTING_ID)
        )

    async def upsert(self, values: dict[str, str | int]) -> None:
        """已有行则更新，否则插入 id=1。"""
        setting = await self.get()
        if setting is None:
            setting = VideoSetting(
                id=self.SETTING_ID,
                asr_provider=str(values["asr_provider"]),
                asr_model=str(values["asr_model"]),
                asr_api_key=str(values["asr_api_key"]),
                asr_app_id=str(values.get("asr_app_id") or ""),
                asr_access_token=str(values.get("asr_access_token") or ""),
                chat_base_url=str(values["chat_base_url"]),
                chat_model=str(values["chat_model"]),
                chat_api_key=str(values["chat_api_key"]),
                qa_model=str(values.get("qa_model") or ""),
                qa_base_url=str(values.get("qa_base_url") or ""),
                qa_api_key=str(values.get("qa_api_key") or ""),
                frames=int(values["frames"]),
            )
            self._session.add(setting)
        else:
            setting.asr_provider = str(values["asr_provider"])
            setting.asr_model = str(values["asr_model"])
            setting.asr_api_key = str(values["asr_api_key"])
            setting.asr_app_id = str(values.get("asr_app_id") or "")
            setting.asr_access_token = str(values.get("asr_access_token") or "")
            setting.chat_base_url = str(values["chat_base_url"])
            setting.chat_model = str(values["chat_model"])
            setting.chat_api_key = str(values["chat_api_key"])
            setting.qa_model = str(values.get("qa_model") or "")
            setting.qa_base_url = str(values.get("qa_base_url") or "")
            setting.qa_api_key = str(values.get("qa_api_key") or "")
            setting.frames = int(values["frames"])
        await self._session.commit()


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(UTC).replace(tzinfo=None)
        return dt
    except ValueError:
        return None


class VideoTaskRepository:
    """视频解析任务结果持久化：以 task_id 为业务唯一键的 upsert。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _row_values(self, state: dict[str, Any]) -> dict[str, Any]:
        report = state.get("report") if isinstance(state.get("report"), dict) else {}
        duration = report.get("duration_seconds") if report else None
        if not isinstance(duration, (int, float)):
            duration = state.get("duration_seconds")
        if not isinstance(duration, (int, float)):
            duration = None

        created = _parse_dt(state.get("created_at"))
        updated = _parse_dt(state.get("updated_at"))
        now = datetime.now(UTC).replace(tzinfo=None)
        return {
            "task_id": str(state.get("task_id") or ""),
            "source": str(state.get("source") or ""),
            "kind": str(state.get("kind") or "url"),
            "status": str(state.get("status") or "submitted"),
            "stage": state.get("stage") or None,
            "created_at": created or now,
            "updated_at": updated or now,
            "output_dir": state.get("output_dir") or None,
            "error": state.get("error") or None,
            "frames_requested": state.get("frames_requested"),
            "transcript_source": state.get("transcript_source") or None,
            "duration_seconds": float(duration) if duration is not None else None,
            "transcript": state.get("transcript") or None,
            "summary": state.get("summary") if isinstance(state.get("summary"), dict) else None,
            "report": report or None,
            "keyframes": state.get("keyframes") if isinstance(state.get("keyframes"), list) else None,
            "cost": state.get("cost") if isinstance(state.get("cost"), dict) else None,
            "events": state.get("events") if isinstance(state.get("events"), list) else None,
            "qa_history": state.get("qa_history") if isinstance(state.get("qa_history"), list) else None,
            "video_path": state.get("video_path") or None,
            "audio_path": state.get("audio_path") or None,
            "content_type": str(state.get("content_type") or "video"),
            "post_text": state.get("post_text") or None,
            "author": state.get("author") or None,
            "hashtags": state.get("hashtags") if isinstance(state.get("hashtags"), list) else None,
            "publish_time": state.get("publish_time") or None,
            "post_images": state.get("post_images") if isinstance(state.get("post_images"), list) else None,
            "image_captions": state.get("image_captions") if isinstance(state.get("image_captions"), dict) else None,
        }

    async def upsert_state(self, state: dict[str, Any]) -> None:
        values = self._row_values(state)
        if not values["task_id"]:
            return
        record = await self._session.scalar(
            select(VideoTaskRecord).where(VideoTaskRecord.task_id == values["task_id"])
        )
        if record is None:
            self._session.add(VideoTaskRecord(**values))
        else:
            for key, value in values.items():
                if key != "task_id":
                    setattr(record, key, value)
        await self._session.commit()

    @staticmethod
    def record_to_state(record: VideoTaskRecord) -> dict[str, Any]:
        return {
            "task_id": record.task_id,
            "source": record.source,
            "kind": record.kind,
            "status": record.status,
            "stage": record.stage,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
            "output_dir": record.output_dir,
            "error": record.error,
            "frames_requested": record.frames_requested,
            "transcript_source": record.transcript_source,
            "transcript": record.transcript,
            "summary": record.summary,
            "report": record.report,
            "keyframes": record.keyframes,
            "cost": record.cost,
            "events": record.events,
            "qa_history": record.qa_history,
            "video_path": record.video_path,
            "audio_path": record.audio_path,
            "content_type": record.content_type or "video",
            "post_text": record.post_text,
            "author": record.author,
            "hashtags": record.hashtags,
            "publish_time": record.publish_time,
            "post_images": record.post_images,
            "image_captions": record.image_captions,
            "cache_manifest": None,
            "pid": None,
        }

    async def get(self, task_id: str) -> VideoTaskRecord | None:
        return await self._session.scalar(
            select(VideoTaskRecord).where(VideoTaskRecord.task_id == task_id)
        )

    async def list(self, limit: int = 100) -> list[VideoTaskRecord]:
        result = await self._session.execute(
            select(VideoTaskRecord)
            .order_by(VideoTaskRecord.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def delete(self, task_id: str) -> None:
        record = await self.get(task_id)
        if record is not None:
            await self._session.delete(record)
            await self._session.commit()

