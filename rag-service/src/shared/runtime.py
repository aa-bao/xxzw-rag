"""运行时可变 model_relay 配置。

初始值来自 config.yaml（FrozenModel），支持运行时热更新（PUT /api/settings/models）。
DB 持久化与响应序列化都基于这个类的 to_dict()。
"""
from __future__ import annotations

from pydantic import SecretStr

from src.shared.config import ModelRelaySettings


class _ClientRelayView:
    """ModelRelayClient/ChatClient 视角的实时视图。

    客户端内部只读 settings.model_relay.<attr>，其中 api_key 需要
    .get_secret_value()。视图属性全部透传 runtime 当前值，热更新即时可见；
    api_key 每次调用包装为 SecretStr，保证拿到最新明文。
    """

    def __init__(self, runtime: "RuntimeModelRelay") -> None:
        self._r = runtime

    @property
    def base_url(self) -> str:
        return self._r.base_url

    @property
    def api_key(self) -> SecretStr:
        return SecretStr(self._r.api_key)

    @property
    def embedding_model(self) -> str:
        return self._r.embedding_model

    @property
    def chat_model(self) -> str:
        return self._r.chat_model

    @property
    def embedding_base_url(self) -> str:
        return self._r.embedding_base_url

    @property
    def embedding_api_key(self) -> SecretStr:
        return SecretStr(self._r.embedding_api_key)

    @property
    def timeout_seconds(self) -> float:
        return self._r.timeout_seconds

    @property
    def embedding_max_retries(self) -> int:
        return self._r.embedding_max_retries

    @property
    def chat_pre_stream_max_retries(self) -> int:
        return self._r.chat_pre_stream_max_retries

    @property
    def retry_base_delay_seconds(self) -> float:
        return self._r.retry_base_delay_seconds


class RuntimeModelRelay:
    """可变版 model_relay 配置，字段语义与 ModelRelaySettings 保持一致。

    api_key / embedding_api_key 存明文（内部系统可接受），但任何 API 响应
    （to_dict）都不会回显，只暴露 has_chat_api_key / has_embedding_api_key。

    embedding_base_url / embedding_api_key 为空串 = 与 chat 共用
    （ModelRelayClient 内部有 or 回退；与 config.yaml 的缺省语义一致）。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        embedding_model: str,
        chat_model: str,
        embedding_base_url: str = "",
        embedding_api_key: str = "",
        timeout_seconds: float = 60.0,
        embedding_max_retries: int = 3,
        chat_pre_stream_max_retries: int = 1,
        retry_base_delay_seconds: float = 1.0,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.embedding_model = embedding_model
        self.chat_model = chat_model
        self.embedding_base_url = embedding_base_url
        self.embedding_api_key = embedding_api_key
        self.timeout_seconds = timeout_seconds
        self.embedding_max_retries = embedding_max_retries
        self.chat_pre_stream_max_retries = chat_pre_stream_max_retries
        self.retry_base_delay_seconds = retry_base_delay_seconds

    @classmethod
    def from_frozen(cls, frozen: ModelRelaySettings) -> "RuntimeModelRelay":
        """从 config.yaml 的只读配置初始化。

        ModelRelaySettings.model_post_init 会把空的 embedding_base_url /
        embedding_api_key 规范化为 chat 的值，这里按"与 chat 相同即视为共用"
        还原原始形态，保证响应/持久化能区分"共用"与"独立配置"。
        """
        embedding_base_url = frozen.embedding_base_url
        if embedding_base_url == frozen.base_url:
            embedding_base_url = ""
        embedding_api_key = frozen.embedding_api_key.get_secret_value()
        if embedding_api_key == frozen.api_key.get_secret_value():
            embedding_api_key = ""
        return cls(
            base_url=frozen.base_url,
            api_key=frozen.api_key.get_secret_value(),
            embedding_model=frozen.embedding_model,
            chat_model=frozen.chat_model,
            embedding_base_url=embedding_base_url,
            embedding_api_key=embedding_api_key,
            timeout_seconds=frozen.timeout_seconds,
            embedding_max_retries=frozen.embedding_max_retries,
            chat_pre_stream_max_retries=frozen.chat_pre_stream_max_retries,
            retry_base_delay_seconds=frozen.retry_base_delay_seconds,
        )

    def to_dict(self) -> dict[str, str | bool | None]:
        """响应结构：绝不包含 api_key 明文，只暴露是否已配置。

        embedding_base_url 为空或与 base_url 相同 → null（与 chat 共用）。
        """
        embedding_base_url = self.embedding_base_url
        if not embedding_base_url or embedding_base_url == self.base_url:
            embedding_base_url = None
        return {
            "base_url": self.base_url,
            "chat_model": self.chat_model,
            "embedding_model": self.embedding_model,
            # null = 与 chat 共用
            "embedding_base_url": embedding_base_url,
            "has_chat_api_key": bool(self.api_key),
            # embedding 未单独配 key 时沿用 chat 的 key
            "has_embedding_api_key": bool(self.embedding_api_key or self.api_key),
        }

    def client_view(self) -> _ClientRelayView:
        """给 ModelRelayClient/ChatClient 的可变壳对象（热更新即时可见）。"""
        return _ClientRelayView(self)

    def copy(self) -> "RuntimeModelRelay":
        """浅拷贝：用于 test 端点构造临时配置，不污染运行时值。"""
        return RuntimeModelRelay(
            base_url=self.base_url,
            api_key=self.api_key,
            embedding_model=self.embedding_model,
            chat_model=self.chat_model,
            embedding_base_url=self.embedding_base_url,
            embedding_api_key=self.embedding_api_key,
            timeout_seconds=self.timeout_seconds,
            embedding_max_retries=self.embedding_max_retries,
            chat_pre_stream_max_retries=self.chat_pre_stream_max_retries,
            retry_base_delay_seconds=self.retry_base_delay_seconds,
        )
