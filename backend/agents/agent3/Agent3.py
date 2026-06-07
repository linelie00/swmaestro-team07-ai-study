# 보라님 agent — 갭 분석 + 로드맵 생성 + 자가 검증
"""Agent3 호출 래퍼.

backend/main.py가 agent1·agent2 결과(dict)와 온보딩 request를 넘기면,
내부 파이프라인(run_agent3)을 돌려 프론트가 쓰는 flat 로드맵 dict를 반환한다.

- async 메서드(default)에서 내부 비동기 파이프라인(run_agent3)을 await.
- 응답은 RoadmapResponse 형태: recommendedPath / skillGaps[] / roadmap(week1To2..week7To8).
- Agent3의 4단계(phases)가 week1To2/3To4/5To6/7To8에 1:1로 매핑된다.
- 보유 스킬은 온보딩 ownedSkills + 에이전트1 이력서 owned_skills를 병합해 갭에서 제외(정확도↑).
- 로깅은 Agent1 방식 차용: [Agent3] 접두 + 이모지 + 박스형 JSON 덤프(입력/trace/최종출력).
"""

import json
import re
import sys

from .models import JobRequirement, ProfileDiagnosis
from .pipeline import run_agent3

# 콘솔 로깅에 이모지/한글이 섞이므로 stdout을 UTF-8로 (Windows cp949 콘솔 대비). Docker는 이미 UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_DEFAULT_WEEKLY_HOURS = 10
_WEEK_KEYS = ["week1To2", "week3To4", "week5To6", "week7To8"]


class Agent3:
    async def default(self, request, agent1_result: dict, agent2_result: dict) -> dict:
        """request(온보딩) + agent1_result + agent2_result → flat 로드맵 dict.

        Args:
            request: RoadmapRequest (majorAndYear, currentStatus, interests, targetJob,
                     preferredCompanyType, availableTime, concerns, ownedSkills).
            agent1_result: {summary, strengths, weaknesses, owned_skills, evidence}.
            agent2_result: {required_skills, preferred_skills, required_experience, keywords, ...}.
        """
        agent1_result = agent1_result or {}
        agent2_result = agent2_result or {}

        weekly_hours = _parse_weekly_hours(getattr(request, "availableTime", None))
        target_role = getattr(request, "targetJob", "") or ""

        # 보유 스킬: 온보딩 입력(ownedSkills) + 에이전트1이 이력서에서 추출한 owned_skills 병합.
        # 이력서 기반 실제 보유 기술을 갭에서 제외 → 더 정확·개인화된 로드맵.
        onboarding_skills = getattr(request, "ownedSkills", []) or []
        resume_skills = agent1_result.get("owned_skills", []) or []
        owned_skills = list(dict.fromkeys([*onboarding_skills, *resume_skills]))

        # ── 입력 로깅 (Agent1 스타일 차용) ──
        print("\n================ [Agent3 Input Verification] ================")
        print(f"ℹ️ [Agent3] 목표직무={target_role} / 주당 가용시간={weekly_hours}h")
        print(f"ℹ️ [Agent3] 에이전트1 정성 진단 수신:")
        print(f"   - 요약(summary): {str(agent1_result.get('summary', ''))[:60]}...")
        print(f"   - 강점(strengths): {agent1_result.get('strengths', [])}")
        print(f"   - 보완점(weaknesses): {agent1_result.get('weaknesses', [])}")
        print(f"ℹ️ [Agent3] 보유 스킬 병합: 온보딩 {onboarding_skills} + 이력서 {resume_skills}")
        print(f"   → 최종 owned_skills(갭 제외 기준): {owned_skills}")
        print(f"ℹ️ [Agent3] 에이전트2 직무 키워드: {agent2_result.get('keywords', [])}")
        print("=============================================================\n")

        profile = ProfileDiagnosis(
            major=getattr(request, "majorAndYear", "") or "",
            current_status=getattr(request, "currentStatus", "") or "",
            interests=getattr(request, "interests", []) or [],
            owned_skills=owned_skills,
            target_role=target_role,
            company_type=getattr(request, "preferredCompanyType", None),
            weekly_hours=weekly_hours,
            concern=getattr(request, "concerns", []) or [],
            summary=agent1_result.get("summary", ""),
            strengths=agent1_result.get("strengths", []) or [],
            weaknesses=agent1_result.get("weaknesses", []) or [],
            evidence=agent1_result.get("evidence", {}) or {},
        )

        job = JobRequirement(
            required_skills=agent2_result.get("required_skills", []) or [],
            preferred_skills=agent2_result.get("preferred_skills", []) or [],
            required_experience=agent2_result.get("required_experience", []) or [],
            keywords=agent2_result.get("keywords", []) or [],
            # evidence_strength는 에이전트2가 주지 않음 → Agent3가 데이터로 추론
        )

        print(f"💡 [Agent3] 갭 분석 및 주차별 로드맵 생성 중...")
        state = await run_agent3(profile, job, weekly_hours=weekly_hours)

        _log_trace(state)
        result = _to_roadmap_response(state, target_role)
        _log_final_output(result)
        return result


def _parse_weekly_hours(available_time, default: int = _DEFAULT_WEEKLY_HOURS) -> int:
    """availableTime 문자열에서 주당 시간(정수)을 추출. 실패 시 기본값.

    예: "주 15시간" → 15, "20시간 이상" → 20, "15" → 15, None → default.
    """
    if available_time is None:
        return default
    if isinstance(available_time, (int, float)):
        return max(1, int(available_time))
    m = re.search(r"\d+", str(available_time))
    return int(m.group()) if m else default


def _log_trace(state) -> None:
    """파이프라인 노드별 결정(state.trace)을 'why-this-path' 타임라인으로 출력."""
    trace = getattr(state, "trace", None) or []
    print("\n================ [Agent3 Trace] (why-this-path) ================")
    for t in trace:
        decision = f" → {t.decision}" if t.decision else ""
        tool = f" [tool:{t.tool_called}]" if t.tool_called else ""
        print(f"   · {t.node}{decision}{tool}: {t.output_summary}")
    fo = getattr(state, "final_output", None)
    if fo is not None:
        print(f"ℹ️ [Agent3] verified={fo.verified}, disclaimer={fo.disclaimer}")
    print("================================================================\n")


def _log_final_output(result: dict) -> None:
    """프론트 반환 dict를 Agent1 스타일 박스형 JSON으로 출력."""
    print("\n================ [Agent3 Final Output] ================")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("=======================================================\n")


def _to_roadmap_response(state, target_role: str) -> dict:
    """Agent3 FinalOutput → 프론트 flat 로드맵 dict."""
    fo = state.final_output
    gaps = fo.gap_analysis.gaps if fo and fo.gap_analysis else []
    phases = fo.roadmap.phases if fo and fo.roadmap else []

    roadmap = {key: [] for key in _WEEK_KEYS}
    for i, key in enumerate(_WEEK_KEYS):
        if i < len(phases):
            roadmap[key] = [item.label for item in phases[i].items]

    return {
        "recommendedPath": f"{target_role} 로드맵" if target_role else "맞춤 학습 로드맵",
        "skillGaps": [f"{g.skill} 학습이 필요합니다." for g in gaps],
        "roadmap": roadmap,
    }
