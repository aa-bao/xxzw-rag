from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from src.platform.identity import PlatformIdentity
from src.shared.errors import AppError

# 平台权限字符（与 rpa-application.yaml 声明一致）
PERMISSION_PROJECT_VIEW = "rag-database:project:view"
PERMISSION_KB_MANAGE = "rag-database:knowledge-base:manage"
PERMISSION_CHAT_USE = "rag-database:chat:use"
PERMISSION_SETTINGS_MANAGE = "rag-database:settings:manage"
ALL_PERMISSIONS = frozenset(
    {
        PERMISSION_PROJECT_VIEW,
        PERMISSION_KB_MANAGE,
        PERMISSION_CHAT_USE,
        PERMISSION_SETTINGS_MANAGE,
    }
)

DataScopeMode = Literal["ALL", "RESTRICTED"]


@dataclass(frozen=True)
class ProjectPrincipal:
    """请求级平台身份视图。

    平台身份字段一律为字符串（平台雪花 ID 超过 JS Number.MAX_SAFE_INTEGER）；
    internal_user_id 仅作为本仓库数据库的兼容主键，不得外传或参与权限判断。
    """

    internal_user_id: int
    tenant_id: str
    external_user_id: str
    department_id: str
    display_name: str
    roles: frozenset[str] = frozenset()
    permissions: frozenset[str] = field(default_factory=frozenset)
    data_scope_mode: DataScopeMode = "ALL"
    data_scope_department_ids: frozenset[str] = frozenset()
    data_scope_user_ids: frozenset[str] = frozenset()

    # ---- 权限 ----

    def require_permission(self, permission: str) -> None:
        if permission not in self.permissions:
            raise AppError("FORBIDDEN", "没有执行此操作的权限", status_code=403)

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

    @property
    def is_admin(self) -> bool:
        return bool(self.roles & {"superadmin", "admin"})

    # ---- 数据范围（规范 10 §9.2）----

    def scope_permits(self, row_department_id: str, row_owner_user_id: str) -> bool:
        """RESTRICTED：行可见当且仅当部门在 departmentIds 或归属人在 userIds；
        两数组皆空则什么都不可见（失败关闭，禁止回退看全部/本部门）。"""
        if self.data_scope_mode == "ALL":
            return True
        return (
            row_department_id in self.data_scope_department_ids
            or row_owner_user_id in self.data_scope_user_ids
        )

    def scope_permits_by_owner(self, row_owner_user_id: str) -> bool:
        return self.scope_permits(self.department_id, row_owner_user_id)

    # ---- 构造 ----

    @classmethod
    def from_platform_identity(
        cls, identity: PlatformIdentity, *, internal_user_id: int
    ) -> "ProjectPrincipal":
        return cls(
            internal_user_id=internal_user_id,
            tenant_id=identity.tenantId,
            external_user_id=identity.userId,
            department_id=identity.departmentId,
            display_name=identity.displayName,
            roles=frozenset(identity.roles),
            permissions=frozenset(identity.permissions),
            data_scope_mode=identity.dataScope.mode,
            data_scope_department_ids=frozenset(identity.dataScope.departmentIds),
            data_scope_user_ids=frozenset(identity.dataScope.userIds),
        )

    @classmethod
    def for_local_user(cls, user_id: int, *, role: str = "user") -> "ProjectPrincipal":
        """LOCAL 模式兼容：本地账号按角色映射平台权限字符。

        account_admin 拥有全部权限；普通 user 仅可查看与对话（与原有
        require_admin/require_user 语义一致，管理操作仍要求管理员）。
        """
        if role == "account_admin":
            permissions = ALL_PERMISSIONS
            roles = frozenset({"account_admin"})
        else:
            permissions = frozenset({PERMISSION_PROJECT_VIEW, PERMISSION_CHAT_USE})
            roles = frozenset({"user"})
        return cls(
            internal_user_id=user_id,
            tenant_id="",
            external_user_id="",
            department_id="",
            display_name="",
            roles=roles,
            permissions=permissions,
            data_scope_mode="ALL",
        )
