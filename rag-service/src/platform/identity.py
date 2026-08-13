from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


class DataScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["ALL", "RESTRICTED"]
    departmentIds: list[StrictStr] = Field(default_factory=list)
    userIds: list[StrictStr] = Field(default_factory=list)

    @field_validator("departmentIds", "userIds")
    @classmethod
    def identifiers_are_nonempty_strings(cls, values: list[str]) -> list[str]:
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError("identity identifiers must be non-empty strings")
        return values

    def permits(self, department_id: str, owner_user_id: str) -> bool:
        if self.mode == "ALL":
            return True
        return department_id in self.departmentIds or owner_user_id in self.userIds


class PlatformIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenantId: StrictStr = Field(min_length=1)
    userId: StrictStr = Field(min_length=1)
    username: str = Field(min_length=1)
    displayName: str = Field(min_length=1)
    departmentId: StrictStr = Field(min_length=1)
    departmentName: str
    departmentCategory: str = ""
    roles: list[str]
    permissions: list[str]
    dataScope: DataScope
    issuedAt: str

    def require_permission(self, permission: str) -> None:
        from src.shared.errors import AppError

        if permission not in self.permissions:
            raise AppError("FORBIDDEN", "没有执行此操作的权限", status_code=403)
