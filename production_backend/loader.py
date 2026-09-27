"""从样例资料构建摄制后台。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .backend import ProductionBackend
from .models import (
    AdaptationLevel,
    Consent,
    ConsentScope,
    EcoAssessment,
    FilmingArea,
    FilmingPermit,
    Footage,
    Person,
    ReleaseVersion,
    ReviewStatus,
    ScriptScene,
    SourceKind,
    SourceRecord,
    WorkflowState,
)


def _day(value: str) -> date:
    return date.fromisoformat(value)


def production_from_dict(data: dict) -> ProductionBackend:
    backend = ProductionBackend(state=WorkflowState(data.get("state", "资料核实")))
    for p in data.get("persons", []):
        backend.add_person(Person(p["person_id"], p["display_name"], p["role_desc"]))
    for s in data.get("sources", []):
        backend.add_source(
            SourceRecord(
                source_id=s["source_id"],
                kind=SourceKind(s["kind"]),
                title=s["title"],
                narrator_id=s.get("narrator_id"),
                recorded_at=_day(s["recorded_at"]),
                claims=list(s.get("claims", [])),
                verified=s.get("verified", False),
                verifier=s.get("verifier", ""),
                verification_note=s.get("verification_note", ""),
            )
        )
    for c in data.get("consents", []):
        backend.add_consent(
            Consent(
                consent_id=c["consent_id"],
                person_id=c["person_id"],
                scopes={ConsentScope(x) for x in c["scopes"]},
                granted_at=_day(c["granted_at"]),
            )
        )
    for a in data.get("areas", []):
        backend.add_area(
            FilmingArea(a["area_id"], a["name"], a.get("public_precision", "县级"))
        )
        # 精确坐标只进保险库，不随区域对象流转。
        if a.get("coordinates"):
            backend.vault.store(a["area_id"], a["coordinates"])
    for a in data.get("assessments", []):
        backend.add_assessment(
            EcoAssessment(
                assessment_id=a["assessment_id"],
                area_id=a["area_id"],
                approved=a.get("approved", False),
                conditions=list(a.get("conditions", [])),
                valid_from=_day(a["valid_from"]),
                valid_to=_day(a["valid_to"]),
            )
        )
    for p in data.get("permits", []):
        backend.add_permit(
            FilmingPermit(
                permit_id=p["permit_id"],
                area_id=p["area_id"],
                valid_from=_day(p["valid_from"]),
                valid_to=_day(p["valid_to"]),
                allowed_behaviors=list(p.get("allowed_behaviors", [])),
                forbidden_behaviors=list(p.get("forbidden_behaviors", [])),
            )
        )
    for s in data.get("scenes", []):
        backend.add_scene(
            ScriptScene(
                scene_id=s["scene_id"],
                title=s["title"],
                summary=s["summary"],
                source_ids=list(s.get("source_ids", [])),
                adaptation=AdaptationLevel(s["adaptation"]),
                adaptation_note=s.get("adaptation_note", ""),
                key_plot=s.get("key_plot", False),
                person_ids=list(s.get("person_ids", [])),
                review=ReviewStatus(s.get("review", "待审")),
            )
        )
    for f in data.get("footage", []):
        backend.add_footage(
            Footage(
                footage_id=f["footage_id"],
                scene_id=f["scene_id"],
                shot_at=_day(f["shot_at"]),
                area_id=f["area_id"],
                permit_id=f["permit_id"],
                person_ids=list(f.get("person_ids", [])),
                contains_precise_location=f.get("contains_precise_location", False),
            )
        )
    for r in data.get("releases", []):
        backend.add_release(
            ReleaseVersion(
                release_id=r["release_id"],
                title=r["title"],
                scene_ids=list(r.get("scene_ids", [])),
                footage_ids=list(r.get("footage_ids", [])),
                locales=list(r.get("locales", [])),
                narration_text=r.get("narration_text", ""),
            )
        )
    return backend


def load_production(path: str | Path) -> ProductionBackend:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return production_from_dict(data)
