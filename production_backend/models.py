"""野生动物故事摄制后台的领域模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class RecordType(str, Enum):
    """与公开领域资料约定一致的记录类型。"""

    INTERVIEW = "事实访谈"
    SCRIPT_SCENE = "剧本场次"
    CONSENT = "人物授权"
    FILMING_PERMIT = "取景许可"
    FOOTAGE = "拍摄素材"
    RELEASE = "发布版本"


class WorkflowState(str, Enum):
    """摄制主流程状态，顺序固定、逐级推进。"""

    VERIFICATION = "资料核实"
    SCRIPT_REVIEW = "剧本审签"
    PRE_PRODUCTION = "待拍摄"
    SHOOTING = "摄制中"
    EDITING = "剪辑中"
    PUBLISHED = "已发布"


WORKFLOW_ORDER = [
    WorkflowState.VERIFICATION,
    WorkflowState.SCRIPT_REVIEW,
    WorkflowState.PRE_PRODUCTION,
    WorkflowState.SHOOTING,
    WorkflowState.EDITING,
    WorkflowState.PUBLISHED,
]


class ReviewStatus(str, Enum):
    PENDING = "待审"
    APPROVED = "通过"
    REJECTED = "驳回"


class SourceKind(str, Enum):
    INTERVIEW = "访谈"
    RESCUE_ARCHIVE = "救助档案"


class AdaptationLevel(str, Enum):
    """剧本场次与史实的边界标注。"""

    FAITHFUL = "史实还原"
    DETAIL_ADAPTED = "细节改编"
    FICTIONALIZED = "情节虚构"


class ConsentScope(str, Enum):
    PORTRAIT = "肖像使用"
    NAME = "姓名使用"
    INTERVIEW_QUOTE = "访谈引用"
    STORY_ADAPTATION = "经历改编"


class ConsentStatus(str, Enum):
    ACTIVE = "有效"
    WITHDRAWN = "已撤回"


class PermitStatus(str, Enum):
    ACTIVE = "有效"
    SUSPENDED = "暂停"
    REVOKED = "撤销"


@dataclass(frozen=True)
class Person:
    """被拍摄或被引用的当事人（样例中均为虚构人物）。"""

    person_id: str
    display_name: str
    role_desc: str


@dataclass
class SourceRecord:
    """事实访谈或救助档案，为剧本场次提供来源。"""

    source_id: str
    kind: SourceKind
    title: str
    narrator_id: str | None
    recorded_at: date
    claims: list[str]
    verified: bool = False
    verifier: str = ""
    verification_note: str = ""


@dataclass
class Consent:
    """人物授权：限定当事人素材可以怎样被使用，可撤回。"""

    consent_id: str
    person_id: str
    scopes: set[ConsentScope]
    granted_at: date
    status: ConsentStatus = ConsentStatus.ACTIVE
    withdrawn_at: date | None = None


@dataclass(frozen=True)
class FilmingArea:
    """取景区域。对外只暴露到 public_precision 精度；

    精确坐标另行存放于 SecureLocationVault，不随区域对象流转。
    """

    area_id: str
    name: str
    public_precision: str = "县级"


@dataclass
class EcoAssessment:
    """生态影响评估：批准后方可拍摄，并附带行为约束。"""

    assessment_id: str
    area_id: str
    approved: bool
    conditions: list[str]
    valid_from: date
    valid_to: date

    def covers(self, day: date) -> bool:
        return self.approved and self.valid_from <= day <= self.valid_to


@dataclass
class FilmingPermit:
    """拍摄许可：限定区域、期限与允许/禁止行为。"""

    permit_id: str
    area_id: str
    valid_from: date
    valid_to: date
    allowed_behaviors: list[str]
    forbidden_behaviors: list[str]
    status: PermitStatus = PermitStatus.ACTIVE

    def covers(self, day: date) -> bool:
        return self.valid_from <= day <= self.valid_to


@dataclass
class AreaClosure:
    """保护区临时关闭。"""

    area_id: str
    start: date
    end: date
    reason: str
    active: bool = True

    def covers(self, day: date) -> bool:
        return self.active and self.start <= day <= self.end


@dataclass
class ScriptScene:
    """剧本场次：挂接来源、标注改编边界。"""

    scene_id: str
    title: str
    summary: str
    source_ids: list[str]
    adaptation: AdaptationLevel
    adaptation_note: str = ""
    key_plot: bool = False
    person_ids: list[str] = field(default_factory=list)
    review: ReviewStatus = ReviewStatus.PENDING
    version: int = 1


@dataclass
class Footage:
    """拍摄素材：关联场次、区域与许可，可被隔离。"""

    footage_id: str
    scene_id: str
    shot_at: date
    area_id: str
    permit_id: str
    person_ids: list[str] = field(default_factory=list)
    quarantined: bool = False
    contains_precise_location: bool = False


@dataclass
class ReleaseVersion:
    """发布版本：由场次与素材组成的成片候选。"""

    release_id: str
    title: str
    scene_ids: list[str]
    footage_ids: list[str]
    locales: list[str]
    narration_text: str = ""
    certified: bool = False


@dataclass
class CallSheetEntry:
    """单日单区域的进入结论与行为边界（不含精确坐标）。"""

    area_id: str
    area_name: str
    location_precision: str
    allowed: bool
    reasons: list[str]
    allowed_behaviors: list[str]
    forbidden_behaviors: list[str]
    eco_conditions: list[str]


@dataclass
class CallSheet:
    """每日开机前的拍摄通告。"""

    day: date
    entries: list[CallSheetEntry]
    generated_by: str

    def entry_for(self, area_id: str) -> CallSheetEntry | None:
        return next((e for e in self.entries if e.area_id == area_id), None)


@dataclass
class TraceReport:
    """剪辑溯源报告：一个场次的全部依据与异常。"""

    scene_id: str
    title: str
    adaptation: AdaptationLevel
    adaptation_note: str
    sources: list[SourceRecord]
    consents: list[Consent]
    footage: list[Footage]
    issues: list[str]


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass
class ReleaseDecision:
    """发布审签结论：逐项核查结果，供发布负责人确认。"""

    release_id: str
    approved: bool
    checks: list[CheckResult]
    decided_by: str
    decided_at: datetime
