from __future__ import annotations

from typing import TypeAlias

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.sql.elements import ColumnElement

from src.db.models import User
from src.platform.principal import ProjectPrincipal

ScopeCondition: TypeAlias = ColumnElement[bool] | None


def _col(table, name: str):
    """兼容 ORM 类属性（InstrumentedAttribute）与 Core Table（table.c[name]）。"""
    if hasattr(table, "c"):
        return table.c[name]
    return getattr(table, name)


def tenant_condition(table, principal: ProjectPrincipal) -> ColumnElement[bool]:
    """租户隔离：平台模式按 tenant_id 精确匹配；LOCAL（tenant 为空）匹配 NULL/空串旧数据。"""
    tenant_column = _col(table, "tenant_id")
    if principal.tenant_id:
        return tenant_column == principal.tenant_id
    return or_(tenant_column.is_(None), tenant_column == "")


def scope_condition(table, principal: ProjectPrincipal) -> ScopeCondition:
    """租户 + dataScope 过滤条件。

    - RESTRICTED：行可见当且仅当部门在 departmentIds 或归属人在 userIds；
      userIds 是平台字符串 ID，需经 rag_user.platform_user_id 映射内部 owner。
    - RESTRICTED 且两数组皆空 → 恒假（空集，失败关闭，禁止回退全量/本部门）。
    - ALL 只做租户过滤。
    """
    parts = [tenant_condition(table, principal)]
    if principal.data_scope_mode == "RESTRICTED":
        department_ids = principal.data_scope_department_ids
        user_ids = principal.data_scope_user_ids
        if not department_ids and not user_ids:
            return and_(False)
        allowed: list[ColumnElement[bool]] = []
        if department_ids:
            allowed.append(_col(table, "department_id").in_(department_ids))
        if user_ids:
            allowed.append(
                exists(
                    select(1).where(
                        User.platform_user_id.in_(user_ids),
                        User.id == _col(table, "owner_user_id"),
                    )
                )
            )
        parts.append(or_(*allowed))
    return and_(*parts)
