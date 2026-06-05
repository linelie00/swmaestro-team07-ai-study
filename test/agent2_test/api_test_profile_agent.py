import os
import json
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional, Literal
from openai import AsyncOpenAI

# FastAPI 라우터 인스턴스 생성
router = APIRouter(
    prefix="/api/v1/profile",
    tags=["Profile Diagnosis"]
)

# 1. 입력 및 출력 데이터 스키마 정의
class OnboardingInput(BaseModel):
    major: str = Field(description="전공/학년 (선택박스 입력)")
    current_status: str = Field(description="현재 상태 - 학생, 취업준비 등 (선택박스 입력)")
    interests: list[str] = Field(description="관심분야 (복수 선택 사항)")
    owned_skills: list[str] = Field(description="사용자 보유 역량 리스트 (선택박스 입력)")
    target_role: str = Field(description="목표 직무 (선택박스 입력)")
    company_type: Optional[str] = Field(default=None, description="희망 회사 유형 (선택박스 입력)")
    weekly_hours: int = Field(description="준비 가능 기간/주당 시간")
    concern: list[str] = Field(description="현재 고민 (복수 선택 사항)")

class ProfileDiagnosis(BaseModel):
    # 1) 온보딩 원본 데이터 보존 필드
    major: str = Field(description="전공/학년 (입력값 그대로 복사)")
    current_status: str = Field(description="현재 상태 (입력값 그대로 복사)")
    interests: list[str] = Field(description="관심분야 목록 (입력값 그대로 복사)")
    owned_skills: list[str] = Field(description="사용자 보유 역량 리스트 (입력값 그대로 복사)")
    target_role: str = Field(description="목표 직무 (입력값 그대로 복사)")
    company_type: Optional[str] = Field(default=None, description="희망 회사 유형 (입력값 그대로 복사)")
    weekly_hours: int = Field(description="주당 가용 시간 (입력값 그대로 복사)")
    concern: list[str] = Field(description="현재 고민 목록 (입력값 그대로 복사)")

    # 2) LLM 분석 진단 필드
    summary: str = Field(description="현재 사용자의 전공, 상태, 고민 등을 종합하여 취업 준비 상태와 배경 맥락을 2-3문장으로 간결하게 정리한 요약본")
    strengths: list[str] = Field(description="온보딩 정보를 기반으로 추출된 사용자의 상대적 강점/유리한 조건")
    weaknesses: list[str] = Field(description="목표 직무 대비 보완해야 할 상대적 약점/불리한 조건 (예: 비전공자 장벽, 주당 학습시간 부족 등)")
    evidence: dict[str, str] = Field(description="strengths, weaknesses 등의 판단 근거가 된 온보딩 입력 문항 매핑")

# 2. 시스템 프롬프트 정의
SYSTEM_PROMPT = """
You are the CareerMate Profile Diagnosis Agent.
Your task is to analyze the user's structured onboarding profile and output a comprehensive, unified diagnosis JSON matching the schema.

You must copy the raw onboarding input values and append the diagnostic analysis (summary, strengths, and weaknesses).

[CORE CONSTRAINTS]
1. ABSOLUTELY NO HALLUCINATION: DO NOT extrapolate, assume, or fabricate any skills, projects, certifications, or experiences that are not explicitly provided in the input. Prevent any guessing-based fake data.
2. You MUST copy the user's raw onboarding fields (`major`, `current_status`, `interests`, `owned_skills`, `target_role`, `company_type`, `weekly_hours`, `concern`) character-for-character into the output JSON. Do not modify these values in any way.
3. `summary` Generation Rule:
   - Create a dense 2-3 sentence context that summarizes:
     a) The user's current academic/career background.
     b) Their specific target job and interest domains.
     c) Key constraints (e.g., weekly available hours) and major concerns.
4. `strengths` & `weaknesses` Rule:
   - Identify strengths based on major relevance (e.g., CS major targeting developer roles), high weekly study hours (>= 20h), or interest alignment.
   - Identify weaknesses based on lack of major relevance (non-CS transitioning to IT), extremely low study hours (< 15h), or key concerns selected (e.g., "개발 경험 없음").
   - NEVER append source texts, colons, brackets, or code to items in the `strengths` and `weaknesses` lists. Keep them as pure nominal concepts (e.g., "전공 일치도", "가용 시간 부족", "개발 경험 부족").
5. `evidence` Rule:
   - For every key listed in `strengths` and `weaknesses`, map the exact strength/weakness name to the relevant raw select-box value from the input.

[VALID MATCHING EXAMPLE]
{
  "major": "경영학과 졸업",
  "current_status": "취업 준비 중",
  "interests": ["백엔드 개발", "클라우드"],
  "owned_skills": ["Excel", "SQL 기초"],
  "target_role": "백엔드 엔지니어",
  "company_type": "스타트업",
  "weekly_hours": 10,
  "concern": ["IT로 이직하고 싶은데 개발 경험이 아예 없습니다.", "어디서부터 시작해야 할지 모르겠습니다."],
  "summary": "경영학 전공의 취업 준비생으로, 백엔드 및 클라우드 개발에 관심이 있으나 개발 경험이 전무한 상태입니다. 주당 10시간의 다소 한정된 준비 시간과 개발 기초 부족에 대한 고민을 안고 취업을 준비하고 있습니다.",
  "strengths": ["경영학적 비즈니스 도메인 지식"],
  "weaknesses": ["개발 경험 부족", "주당 가용 시간 부족"],
  "evidence": {
    "경영학적 비즈니스 도메인 지식": "major: '경영학과 졸업'",
    "개발 경험 부족": "concern: ['IT로 이직하고 싶은데 개발 경험이 아예 없습니다.']",
    "주당 가용 시간 부족": "weekly_hours: 10"
  }
}

[JSON SCHEMA]
Your output MUST be a valid JSON object matching the ProfileDiagnosis schema.
"""

# 3. 비동기식 Upstage API 호출 핵심 비즈니스 로직
async def diagnose_profile_async(user_input: OnboardingInput) -> ProfileDiagnosis:
    api_key = os.getenv("UPSTAGE_API_KEY")
    if not api_key:
        raise ValueError("환경 변수 UPSTAGE_API_KEY가 설정되지 않았습니다.")
    
    # AsyncOpenAI 클라이언트 생성 (I/O 논블로킹 지원)
    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.upstage.ai/v1"
    )
    
    user_prompt = f"User Onboarding Profile:\n{user_input.model_dump_json(indent=2)}"
    
    try:
        # 비동기(await) Chat Completions API 호출
        response = await client.chat.completions.create(
            model="solar-1-mini-chat",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        
        output_text = response.choices[0].message.content
        parsed_data = json.loads(output_text)
        
        # Pydantic 데이터 모델 검증 및 복제본 빌드
        profile_diagnosis = ProfileDiagnosis.model_validate(parsed_data)
        return profile_diagnosis
        
    except Exception as e:
        print(f"[ProfileAgent Error] 진단 중 예외 발생: {e}")
        raise e

# 4. FastAPI 엔드포인트 노출
@router.post("/diagnose", response_model=ProfileDiagnosis, status_code=status.HTTP_200_OK)
async def api_diagnose_profile(payload: OnboardingInput):
    """
    온보딩 정형 입력을 받아 강점/약점/맥락적 요약본을 비동기로 분석하여 반환하는 API입니다.
    """
    try:
        result = await diagnose_profile_async(payload)
        return result
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"환경변수 구성 오류: {str(ve)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upstage API 통신 또는 파싱 실패: {str(e)}"
        )

# 5. 로컬 단독 테스트 서버 구동부 (백엔드 통합 전 개별 검증용)
if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    
    app = FastAPI(title="CareerMate Agent 1 (Profile Diagnosis) Sandbox Server")
    app.include_router(router)
    
    print("\n" + "="*60)
    print("🚀 CareerMate 에이전트 1 샌드박스 데모 서버 가동 준비 완료")
    print("👉 Swagger UI (대화식 API 테스트): http://127.0.0.1:8000/docs")
    print("="*60 + "\n")
    
    uvicorn.run(app, host="127.0.0.1", port=8000)
