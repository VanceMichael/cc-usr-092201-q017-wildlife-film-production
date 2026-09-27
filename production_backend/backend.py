"""摄制后台核心服务：资料核实、授权许可、通告、溯源与发布审签。"""

from __future__ import annotations

import re
from datetime import date, datetime

from .access import SecureLocationVault
from .audit import AuditLog
from .models import (
    AdaptationLevel,
    AreaClosure,
    CallSheet,
    CallSheetEntry,
    CheckResult,
    Consent,
    ConsentScope,
    ConsentStatus,
    Footage,
    PermitStatus,
    ReleaseDecision,
    ReleaseVersion,
    ReviewStatus,
    ScriptScene,
    SourceKind,
    TraceReport,
    WorkflowState,
    WORKFLOW_ORDER,
)
from .reviews import ReviewBoard, ReviewKind, ReviewTicket

#: 疑似精确坐标的文本特征（度分秒、十进制度数、经纬度标注、GPS 标注）。
_COORD_PATTERNS = (
    re.compile(r"\d{1,3}°\s*\d{1,2}[′']"),
    re.compile(r"(?<!\d)\d{2,3}\.\d{4,}(?!\d)"),
    re.compile(r"[经纬]度\s*[:：]?\s*\d"),
    re.compile(r"GPS\s*[:：]?\s*\d", re.IGNORECASE),
)


def location_leaks(text: str) -> list[str]:
    """返回文本中疑似精确坐标的片段。"""
    hits: list[str] = []
    for pattern in _COORD_PATTERNS:
        hits.extend(m.group(0) for m in pattern.finditer(text))
    return hits


class ProductionBackend:
    """覆盖资料核实到成片发行的摄制后台。

    所有状态变更写入 audit；剧本修改、撤回授权、保护区临时关闭、
    疑似素材泄露、海外翻译变化都会在审签工单板（reviews）上重新开单。
    """

    def __init__(
        self,
        *,
        audit: AuditLog | None = None,
        state: WorkflowState = WorkflowState.VERIFICATION,
    ) -> None:
        self.audit = audit or AuditLog()
        self.state = state
        self.persons: dict = {}
        self.sources: dict[str, SourceRecord] = {}
        self.consents: dict[str, Consent] = {}
        self.areas: dict = {}
        self.assessments: dict = {}
        self.permits: dict = {}
        self.closures: list[AreaClosure] = []
        self.scenes: dict[str, ScriptScene] = {}
        self.footage: dict[str, Footage] = {}
        self.releases: dict[str, ReleaseVersion] = {}
        self.reviews = ReviewBoard()
        self.vault = SecureLocationVault(self.audit)
        self._release_decisions: dict[str, ReleaseDecision] = {}

    # ------------------------------------------------------------------
    # 登记
    # ------------------------------------------------------------------
    def add_person(self, person) -> None:
        self.persons[person.person_id] = person

    def add_source(self, source: SourceRecord) -> None:
        self.sources[source.source_id] = source

    def add_consent(self, consent: Consent) -> None:
        self.consents[consent.consent_id] = consent

    def add_area(self, area) -> None:
        self.areas[area.area_id] = area

    def add_assessment(self, assessment) -> None:
        self.assessments[assessment.assessment_id] = assessment

    def add_permit(self, permit) -> None:
        self.permits[permit.permit_id] = permit

    def add_scene(self, scene: ScriptScene) -> None:
        self.scenes[scene.scene_id] = scene

    def add_footage(self, footage: Footage) -> None:
        self.footage[footage.footage_id] = footage

    def add_release(self, release: ReleaseVersion) -> None:
        self.releases[release.release_id] = release

    # ------------------------------------------------------------------
    # 资料核实与剧本审签
    # ------------------------------------------------------------------
    def verify_source(self, source_id: str, *, verifier: str, note: str = "") -> SourceRecord:
        source = self._get(self.sources, source_id, "来源")
        source.verified = True
        source.verifier = verifier
        source.verification_note = note
        self.audit.record(verifier, "资料核实", source_id, note or "来源已核实")
        return source

    def approve_scene(self, scene_id: str, *, approver: str) -> ScriptScene:
        scene = self._get(self.scenes, scene_id, "场次")
        unverified = [sid for sid in scene.source_ids if not self.sources[sid].verified]
        if unverified:
            raise ValueError(f"来源未核实，场次不得通过审签：{', '.join(unverified)}")
        if scene.adaptation is not AdaptationLevel.FAITHFUL and not scene.adaptation_note:
            raise ValueError("艺术改编场次必须填写与史实的边界说明")
        scene.review = ReviewStatus.APPROVED
        for ticket in self.reviews.pending(kind=ReviewKind.SCRIPT, target_id=scene_id):
            self.reviews.resolve(ticket.ticket_id, actor=approver, resolution="场次审签通过")
        self.audit.record(approver, "剧本审签", scene_id, f"第 {scene.version} 版通过")
        return scene

    def change_script(
        self,
        scene_id: str,
        *,
        actor: str,
        summary: str | None = None,
        adaptation: AdaptationLevel | None = None,
        adaptation_note: str | None = None,
        source_ids: list[str] | None = None,
    ) -> ReviewTicket:
        """剧本修改：场次退回待审并重新进入剧本审签。"""
        scene = self._get(self.scenes, scene_id, "场次")
        if summary is not None:
            scene.summary = summary
        if adaptation is not None:
            scene.adaptation = adaptation
        if adaptation_note is not None:
            scene.adaptation_note = adaptation_note
        if source_ids is not None:
            scene.source_ids = list(source_ids)
        scene.version += 1
        scene.review = ReviewStatus.PENDING
        affected = [f.footage_id for f in self.footage.values() if f.scene_id == scene_id]
        reason = f"剧本第 {scene.version} 版修改，重新进入剧本审签"
        if affected:
            reason += f"；已拍素材待复核：{', '.join(affected)}"
        ticket = self.reviews.open(ReviewKind.SCRIPT, scene_id, reason, actor=actor)
        self.audit.record(actor, "剧本修改", scene_id, reason)
        return ticket

    # ------------------------------------------------------------------
    # 人物授权
    # ------------------------------------------------------------------
    def withdraw_consent(self, consent_id: str, *, actor: str) -> dict:
        """当事人撤回授权：涉及场次与素材被标记，重新进入授权复核。"""
        consent = self._get(self.consents, consent_id, "授权")
        if consent.status is ConsentStatus.WITHDRAWN:
            raise ValueError(f"授权已撤回：{consent_id}")
        consent.status = ConsentStatus.WITHDRAWN
        consent.withdrawn_at = date.today()
        scenes = [s.scene_id for s in self.scenes.values() if consent.person_id in s.person_ids]
        footage = [
            f.footage_id for f in self.footage.values() if consent.person_id in f.person_ids
        ]
        reason = (
            f"当事人撤回授权；涉及场次：{', '.join(scenes) or '无'}；"
            f"涉及素材：{', '.join(footage) or '无'}"
        )
        ticket = self.reviews.open(ReviewKind.CONSENT, consent_id, reason, actor=actor)
        self.audit.record(actor, "撤回授权", consent_id, reason)
        return {"ticket": ticket, "scenes": scenes, "footage": footage}

    # ------------------------------------------------------------------
    # 取景区域、许可与生态
    # ------------------------------------------------------------------
    def close_area(
        self, area_id: str, *, start: date, end: date, reason: str, actor: str
    ) -> ReviewTicket:
        """保护区临时关闭：暂停重叠许可，重新进入许可复核。"""
        self._get(self.areas, area_id, "区域")
        self.closures.append(AreaClosure(area_id, start, end, reason))
        suspended = []
        for permit in self.permits.values():
            if (
                permit.area_id == area_id
                and permit.status is PermitStatus.ACTIVE
                and not (permit.valid_to < start or permit.valid_from > end)
            ):
                permit.status = PermitStatus.SUSPENDED
                suspended.append(permit.permit_id)
        detail = f"保护区临时关闭（{reason}），暂停许可：{', '.join(suspended) or '无'}"
        ticket = self.reviews.open(ReviewKind.PERMIT, area_id, detail, actor=actor)
        self.audit.record(actor, "保护区临时关闭", area_id, detail)
        return ticket

    def reopen_area(self, area_id: str, *, actor: str, resolution: str = "关闭解除，许可恢复") -> None:
        self._get(self.areas, area_id, "区域")
        for closure in self.closures:
            if closure.area_id == area_id:
                closure.active = False
        for permit in self.permits.values():
            if permit.area_id == area_id and permit.status is PermitStatus.SUSPENDED:
                permit.status = PermitStatus.ACTIVE
        for ticket in self.reviews.pending(kind=ReviewKind.PERMIT, target_id=area_id):
            self.reviews.resolve(ticket.ticket_id, actor=actor, resolution=resolution)
        self.audit.record(actor, "保护区重新开放", area_id, resolution)

    # ------------------------------------------------------------------
    # 素材泄露
    # ------------------------------------------------------------------
    def report_leak(self, footage_id: str, *, actor: str, detail: str = "") -> ReviewTicket:
        """疑似素材泄露：立即隔离并进入泄露安全审查。"""
        footage = self._get(self.footage, footage_id, "素材")
        footage.quarantined = True
        reason = f"疑似素材泄露，已隔离。{detail}".strip()
        ticket = self.reviews.open(ReviewKind.SECURITY, footage_id, reason, actor=actor)
        self.audit.record(actor, "疑似素材泄露", footage_id, detail or "素材已隔离")
        return ticket

    def resolve_leak(self, footage_id: str, *, actor: str, confirmed: bool) -> None:
        """办结泄露审查：确认泄露则保持隔离，排除嫌疑则解除。"""
        footage = self._get(self.footage, footage_id, "素材")
        if not confirmed:
            footage.quarantined = False
        resolution = "确认泄露，素材保持隔离，禁止使用" if confirmed else "排除泄露嫌疑，解除隔离"
        for ticket in self.reviews.pending(kind=ReviewKind.SECURITY, target_id=footage_id):
            self.reviews.resolve(ticket.ticket_id, actor=actor, resolution=resolution)
        self.audit.record(actor, "泄露审查办结", footage_id, resolution)

    # ------------------------------------------------------------------
    # 海外翻译
    # ------------------------------------------------------------------
    def change_translation(
        self, release_id: str, *, locale: str, actor: str, note: str = ""
    ) -> ReviewTicket:
        """海外翻译变化：发布版本重新进入翻译审签。"""
        self._get(self.releases, release_id, "发布版本")
        reason = f"海外翻译变化（{locale}），重新审签。{note}".strip()
        ticket = self.reviews.open(ReviewKind.TRANSLATION, release_id, reason, actor=actor)
        self.audit.record(actor, "翻译变化", release_id, f"语言版本 {locale}")
        return ticket

    # ------------------------------------------------------------------
    # 每日拍摄通告
    # ------------------------------------------------------------------
    def call_sheet(self, day: date, *, requester: str) -> CallSheet:
        """开机前确认当天允许进入的区域和行为（不含精确坐标）。"""
        entries: list[CallSheetEntry] = []
        for area in self.areas.values():
            permits = [p for p in self.permits.values() if p.area_id == area.area_id]
            if not permits:
                continue
            covering = [p for p in permits if p.covers(day)]
            permit = covering[0] if covering else None
            reasons: list[str] = []
            if permit is None:
                reasons.append("当日无有效取景许可")
            elif permit.status is PermitStatus.SUSPENDED:
                reasons.append(f"许可 {permit.permit_id} 已暂停")
            elif permit.status is PermitStatus.REVOKED:
                reasons.append(f"许可 {permit.permit_id} 已撤销")
            closure = next(
                (c for c in self.closures if c.area_id == area.area_id and c.covers(day)),
                None,
            )
            if closure:
                reasons.append(f"保护区临时关闭：{closure.reason}")
            assessment = next(
                (a for a in self.assessments.values() if a.area_id == area.area_id), None
            )
            conditions = assessment.conditions if assessment else []
            if assessment is None or not assessment.covers(day):
                reasons.append("生态影响评估未获批或未覆盖当日")
                conditions = []
            if self.reviews.pending(kind=ReviewKind.PERMIT, target_id=area.area_id):
                reasons.append("许可复核未完成")
            allowed = not reasons
            entries.append(
                CallSheetEntry(
                    area_id=area.area_id,
                    area_name=area.name,
                    location_precision=area.public_precision,
                    allowed=allowed,
                    reasons=reasons,
                    allowed_behaviors=permit.allowed_behaviors if (allowed and permit) else [],
                    forbidden_behaviors=permit.forbidden_behaviors if permit else [],
                    eco_conditions=conditions if allowed else [],
                )
            )
        sheet = CallSheet(day=day, entries=entries, generated_by=requester)
        summary = "；".join(
            f"{e.area_name}:{'可进入' if e.allowed else '禁止'}" for e in entries
        )
        self.audit.record(requester, "生成拍摄通告", day.isoformat(), summary)
        return sheet

    # ------------------------------------------------------------------
    # 剪辑溯源
    # ------------------------------------------------------------------
    def trace_scene(self, scene_id: str) -> TraceReport:
        """剪辑人员追查一个场次的来源、授权、素材与异常。"""
        scene = self._get(self.scenes, scene_id, "场次")
        sources = [self.sources[sid] for sid in scene.source_ids]
        consents = [c for c in self.consents.values() if c.person_id in scene.person_ids]
        footage = [f for f in self.footage.values() if f.scene_id == scene_id]
        issues: list[str] = []
        for s in sources:
            if not s.verified:
                issues.append(f"来源未核实：{s.source_id}《{s.title}》")
        if scene.adaptation is not AdaptationLevel.FAITHFUL and not scene.adaptation_note:
            issues.append("艺术改编未标注与史实的边界")
        for c in consents:
            if c.status is ConsentStatus.WITHDRAWN:
                issues.append(f"授权已撤回：{c.person_id}")
        for f in footage:
            if f.quarantined:
                issues.append(f"素材被隔离：{f.footage_id}")
        return TraceReport(
            scene_id=scene.scene_id,
            title=scene.title,
            adaptation=scene.adaptation,
            adaptation_note=scene.adaptation_note,
            sources=sources,
            consents=consents,
            footage=footage,
            issues=issues,
        )

    # ------------------------------------------------------------------
    # 素材位置脱敏
    # ------------------------------------------------------------------
    def scrub_location(self, footage_id: str, *, actor: str) -> Footage:
        footage = self._get(self.footage, footage_id, "素材")
        footage.contains_precise_location = False
        self.audit.record(actor, "素材位置脱敏", footage_id, "精确位置信息已清除")
        return footage

    # ------------------------------------------------------------------
    # 发布审签
    # ------------------------------------------------------------------
    def certify_release(self, release_id: str, *, manager: str) -> ReleaseDecision:
        """发布负责人对成片做逐项核查；结论与依据全部留痕。"""
        release = self._get(self.releases, release_id, "发布版本")
        checks = [
            self._check_scenes_approved(release),
            self._check_key_plot(release),
            self._check_adaptation_marks(release),
            self._check_consents(release),
            self._check_footage_clear(release),
            self._check_location_safety(release),
            self._check_permit_coverage(release),
            self._check_pending_reviews(release),
        ]
        approved = all(c.passed for c in checks)
        decision = ReleaseDecision(release_id, approved, checks, manager, datetime.now())
        self._release_decisions[release_id] = decision
        release.certified = approved
        if approved:
            detail = "通过"
        else:
            failed = "、".join(c.name for c in checks if not c.passed)
            detail = f"驳回：{failed}"
        self.audit.record(manager, "发布审签", release_id, detail)
        return decision

    def release_dossier(self, release_id: str) -> dict:
        """发布负责人调取的完整记录：审签结论 + 全程留痕。"""
        self._get(self.releases, release_id, "发布版本")
        return {
            "decision": self._release_decisions.get(release_id),
            "audit_trail": self.audit.entries(),
        }

    def _scenes_of(self, release: ReleaseVersion) -> list[ScriptScene]:
        return [self.scenes[sid] for sid in release.scene_ids]

    def _footage_of(self, release: ReleaseVersion) -> list[Footage]:
        return [self.footage[fid] for fid in release.footage_ids]

    def _check_scenes_approved(self, release: ReleaseVersion) -> CheckResult:
        bad = [s.scene_id for s in self._scenes_of(release) if s.review is not ReviewStatus.APPROVED]
        return CheckResult(
            "场次审签完成", not bad, "全部场次已通过审签" if not bad else f"未通过审签：{', '.join(bad)}"
        )

    def _check_key_plot(self, release: ReleaseVersion) -> CheckResult:
        problems = []
        for s in self._scenes_of(release):
            if not s.key_plot:
                continue
            if s.adaptation is AdaptationLevel.FICTIONALIZED:
                problems.append(f"{s.scene_id} 关键情节不得为虚构")
            elif not any(self.sources[sid].verified for sid in s.source_ids):
                problems.append(f"{s.scene_id} 关键情节缺少已核实来源")
        return CheckResult(
            "关键情节有据可查",
            not problems,
            "；".join(problems) if problems else "关键情节均有核实来源",
        )

    def _check_adaptation_marks(self, release: ReleaseVersion) -> CheckResult:
        bad = [
            s.scene_id
            for s in self._scenes_of(release)
            if s.adaptation is not AdaptationLevel.FAITHFUL and not s.adaptation_note
        ]
        return CheckResult(
            "改编边界已标注",
            not bad,
            "改编处均已标明与史实的边界" if not bad else f"缺少边界说明：{', '.join(bad)}",
        )

    def _check_consents(self, release: ReleaseVersion) -> CheckResult:
        needed: dict[str, set[ConsentScope]] = {}
        for f in self._footage_of(release):
            for pid in f.person_ids:
                needed.setdefault(pid, set()).add(ConsentScope.PORTRAIT)
        for s in self._scenes_of(release):
            if s.adaptation is not AdaptationLevel.FAITHFUL:
                for pid in s.person_ids:
                    needed.setdefault(pid, set()).add(ConsentScope.STORY_ADAPTATION)
            for sid in s.source_ids:
                src = self.sources[sid]
                if src.kind is SourceKind.INTERVIEW and src.narrator_id:
                    needed.setdefault(src.narrator_id, set()).add(ConsentScope.INTERVIEW_QUOTE)
        problems = []
        for pid, scopes in sorted(needed.items()):
            name = self.persons[pid].display_name if pid in self.persons else pid
            consent = self._consent_for(pid)
            if consent is None:
                problems.append(f"{name} 缺少授权")
            elif consent.status is ConsentStatus.WITHDRAWN:
                problems.append(f"{name} 授权已撤回")
            else:
                missing = scopes - consent.scopes
                if missing:
                    lacks = "、".join(sorted(m.value for m in missing))
                    problems.append(f"{name} 授权范围缺少：{lacks}")
        return CheckResult(
            "人物授权有效",
            not problems,
            "；".join(problems) if problems else "授权覆盖全部使用",
        )

    def _check_footage_clear(self, release: ReleaseVersion) -> CheckResult:
        bad = [f.footage_id for f in self._footage_of(release) if f.quarantined]
        return CheckResult(
            "素材未被隔离",
            not bad,
            "素材均可使用" if not bad else f"被隔离素材：{', '.join(bad)}",
        )

    def _check_location_safety(self, release: ReleaseVersion) -> CheckResult:
        problems = []
        hits = location_leaks(release.narration_text)
        if hits:
            problems.append(f"解说词疑似含精确坐标：{', '.join(hits)}")
        bad = [f.footage_id for f in self._footage_of(release) if f.contains_precise_location]
        if bad:
            problems.append(f"素材仍含精确位置：{', '.join(bad)}")
        return CheckResult(
            "未暴露精确位置",
            not problems,
            "；".join(problems) if problems else "成片不含精确位置信息",
        )

    def _check_permit_coverage(self, release: ReleaseVersion) -> CheckResult:
        problems = []
        for f in self._footage_of(release):
            permit = self.permits.get(f.permit_id)
            if permit is None:
                problems.append(f"{f.footage_id} 许可不存在：{f.permit_id}")
                continue
            if permit.area_id != f.area_id:
                problems.append(f"{f.footage_id} 拍摄区域超出许可范围")
            if not permit.covers(f.shot_at):
                problems.append(f"{f.footage_id} 拍摄日期不在许可有效期")
            if permit.status is PermitStatus.REVOKED:
                problems.append(f"{f.footage_id} 许可已撤销")
        return CheckResult(
            "许可覆盖拍摄",
            not problems,
            "；".join(problems) if problems else "全部素材在许可范围内拍摄",
        )

    def _check_pending_reviews(self, release: ReleaseVersion) -> CheckResult:
        pending = self.reviews.pending()
        detail = (
            "无未完成审签"
            if not pending
            else "；".join(f"{t.ticket_id} {t.kind.value}:{t.target_id}" for t in pending)
        )
        return CheckResult("无未完成审签", not pending, detail)

    # ------------------------------------------------------------------
    # 主流程推进
    # ------------------------------------------------------------------
    def advance_state(self, *, actor: str) -> WorkflowState:
        idx = WORKFLOW_ORDER.index(self.state)
        if idx == len(WORKFLOW_ORDER) - 1:
            raise RuntimeError("已处于最终状态「已发布」")
        pending = self.reviews.pending()
        if pending:
            raise RuntimeError(f"存在 {len(pending)} 项未完成审签，不得推进流程")
        nxt = WORKFLOW_ORDER[idx + 1]
        if nxt is WorkflowState.PUBLISHED and not any(
            r.certified for r in self.releases.values()
        ):
            raise RuntimeError("尚无通过发布审签的版本，不得进入「已发布」")
        self.state = nxt
        self.audit.record(actor, "流程推进", nxt.value, f"进入「{nxt.value}」")
        return self.state

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _consent_for(self, person_id: str) -> Consent | None:
        return next(
            (c for c in self.consents.values() if c.person_id == person_id), None
        )

    @staticmethod
    def _get(mapping: dict, key: str, label: str):
        try:
            return mapping[key]
        except KeyError:
            raise KeyError(f"{label}不存在：{key}") from None
