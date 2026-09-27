"""岗位与敏感坐标访问控制。"""

from __future__ import annotations

from enum import Enum

from .audit import AuditLog


class Role(str, Enum):
    PRODUCER = "制片人"
    WRITER = "编剧"
    DIRECTOR = "导演"
    EDITOR = "剪辑师"
    LOCATION_MANAGER = "外联制片"
    ECO_ADVISOR = "生态顾问"
    RELEASE_MANAGER = "发布负责人"
    FIELD_CREW = "现场人员"


#: 精确坐标只向这些必要岗位开放。
COORDINATE_CLEARED_ROLES = frozenset(
    {Role.PRODUCER, Role.LOCATION_MANAGER, Role.ECO_ADVISOR}
)


class SecureLocationVault:
    """精确坐标保险库：按岗位放行，访问与被拒全程留痕。"""

    def __init__(self, audit: AuditLog) -> None:
        self._coordinates: dict[str, str] = {}
        self._audit = audit

    def store(self, area_id: str, coordinates: str) -> None:
        self._coordinates[area_id] = coordinates

    def has(self, area_id: str) -> bool:
        return area_id in self._coordinates

    def reveal(self, area_id: str, *, role: Role, actor: str) -> str:
        if role not in COORDINATE_CLEARED_ROLES:
            self._audit.record(
                actor, "坐标访问被拒", area_id, f"岗位「{role.value}」无权查看精确坐标"
            )
            raise PermissionError(f"岗位「{role.value}」无权查看 {area_id} 的精确坐标")
        if area_id not in self._coordinates:
            raise KeyError(f"区域无精确坐标记录：{area_id}")
        self._audit.record(actor, "坐标访问", area_id, f"岗位「{role.value}」查看精确坐标")
        return self._coordinates[area_id]
