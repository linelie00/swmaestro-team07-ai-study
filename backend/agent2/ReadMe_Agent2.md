# ReadMe: 에이전트 1 (Profile Diagnosis Agent) 개발 및 연동 가이드

본 문서는 **CareerMate 프로젝트**의 **에이전트 1 (프로필 진단 에이전트)**의 구현 사양, API 규격, 로컬 테스트 방법 및 메인 통합(`main.py`) 마이그레이션 방안을 정리한 개발자 가이드입니다.

---

## 1. 개요 및 제공 기능
* **역할**: 온보딩 입력값(선택박스 기반 정형 데이터)을 기반으로 현재 사용자의 상황을 객관적으로 분석하여 **프로필 진단서**를 발행합니다.
* **핵심 지침 (추론 및 가짜 데이터 도출 금지)**: LLM이 사용자가 명시하지 않은 자격증, 스킬, 프로젝트 등을 멋대로 유추하거나 지어내는 환각(Hallucination) 현상을 원천적으로 차단합니다.
* **에이전트 3(로드맵 설계)과의 연동성 극대화**:
  * 온보딩 시 사용자가 제출한 원본 입력 정보들을 최종 출력 구조에 그대로 보존하여 이월합니다.
  * 사용자가 입력한 보유 스킬(`owned_skills`) 목록을 이월하여, 에이전트 3이 에이전트 2(공고 분석)의 요구 스택과 직접 1:1 차집합을 내어 정밀한 '역량 갭'을 계산할 수 있도록 지원합니다.

---

## 2. 로컬 환경 세팅 및 단독 테스트 서버 구동법

### A. 의존성 패키지 설치
프로젝트 루트에서 가상환경을 활성화한 뒤, 새롭게 추가된 라이브러리(`fastapi`, `uvicorn` 등)를 한 번에 설치합니다.
```bash
# 1. 가상환경 활성화
source .venv/bin/activate

# 2. requirements.txt를 통한 의존성 일괄 설치
pip install -r requirements.txt
```

### B. 로컬 단독(샌드박스) 데모 서버 구동
메인 통합 프로젝트 가동 전, 에이전트 1의 동작만을 고유 포트에서 샌드박스로 띄워 대화식으로 테스트할 수 있습니다.
```bash
# Upstage API 키를 주입하여 단독 서버 실행
UPSTAGE_API_KEY="실제_업스테이지_API_키" python test/agent2_test/api_test_profile_agent.py
```
* **로컬 데모 서버 주소**: `http://127.0.0.1:8000`
* **Swagger UI (대화식 API 테스트)**: `http://127.0.0.1:8000/docs` 페이지에 접속하여 직접 마우스 클릭으로 온보딩 데이터를 채워 넣고 API 응답을 실시간으로 테스트해 볼 수 있습니다.

---

## 3. API 명세 (API Contracts)

* **HTTP Method**: `POST`
* **API Endpoint**: `/api/v1/profile/diagnose`
* **Content-Type**: `application/json`

### A. 요청 바디 (OnboardingInput)
사용자가 온보딩 폼에서 선택박스로 고른 정형 정보입니다.
| 필드명 | 타입 | 설명 |
| :--- | :--- | :--- |
| `major` | `str` | 전공 / 학년 정보 |
| `current_status` | `str` | 현재 상태 (학생, 취업준비생 등) |
| `interests` | `list[str]` | 관심사 분야 목록 (복수 선택) |
| `owned_skills` | `list[str]` | 사용자가 생각하는 본인의 보유 역량 목록 |
| `target_role` | `str` | 목표 직무 |
| `company_type` | `str \| null` | 희망 회사 유형 (선택 사항) |
| `weekly_hours` | `int` | 주당 학습 가용 시간 |
| `concern` | `list[str]` | 현재 취업/학습 고민 목록 (복수 선택) |

### B. 응답 바디 (ProfileDiagnosis)
원본 보존 데이터와 LLM의 객관적인 정성 진단 결과가 통합되어 반환됩니다.
| 분류 | 필드명 | 타입 | 설명 |
| :--- | :--- | :--- | :--- |
| **원본 보존** | `major` ~ `concern` | 위와 동일 | 입력받은 온보딩 데이터 8개 필드를 그대로 복사 |
| **LLM 진단** | `summary` | `str` | 사용자의 상황과 고민을 종합한 2~3문장의 맥락 요약본 |
| **LLM 진단** | `strengths` | `list[str]` | 전공 및 보유 기술 기준 사용자의 상대적 강점 명칭 목록 |
| **LLM 진단** | `weaknesses` | `list[str]` | 목표 직무 및 고민 사항 기준 상대적 보완 약점 명칭 목록 |
| **LLM 진단** | `evidence` | `dict[str, str]` | strengths, weaknesses 등의 판단 근거가 된 입력 매핑 정보 |

---

## 4. main.py 마이그레이션 및 비동기 병렬화 가이드

### A. 메인 서버에 라우터 등록 방식
백엔드 팀원이 작성할 `main.py`에 라우터를 등록하여 하나의 포트(예: 8000) 내에서 통합 가동할 수 있습니다.
```python
# backend/main.py
from fastapi import FastAPI
from backend.agent2.profile_agent import router as profile_router

app = FastAPI(title="CareerMate Integration Server")

# APIRouter 연결
app.include_router(profile_router)
```

### B. 비동기 병렬 호출 방식 (`asyncio.gather`)
에이전트 3(로드맵 생성)을 띄우기 전에 에이전트 1(프로필 진단)과 에이전트 2(공고 서칭)를 병렬로 동시에 호출하여 실행 속도를 획기적으로 줄이는 통합 비즈니스 로직 설계 예시입니다.
```python
import asyncio
from fastapi import FastAPI
from backend.agent2.profile_agent import diagnose_profile_async, OnboardingInput

# (가정) 에이전트 2 및 3 비동기 핸들러 임포트
# from backend.agent2.job_agent import search_jobs_async
# from backend.agent3.roadmap_agent import generate_roadmap_async

app = FastAPI()

@app.post("/api/v1/roadmap/generate")
async def generate_integrated_roadmap(payload: OnboardingInput):
    # 1. 에이전트 1과 2를 비동기 병렬로 가동 (동시 대기)
    task_agent1 = diagnose_profile_async(payload)
    task_agent2 = search_jobs_async(payload)
    
    # 두 에이전트가 동시에 실행되며, 가장 늦게 끝나는 작업 시간에 맞춰 동시에 취합됨
    profile_result, job_result = await asyncio.gather(task_agent1, task_agent2)
    
    # 2. 취합된 결과를 최종 조립 노드(에이전트 3)에 전달하여 로드맵 발행
    final_output = await generate_roadmap_async(profile_result, job_result)
    
    return final_output
```

---

## 부록. 테스트 코드 소스 코드

### 1) 로컬 검증 테스트 코드 (`test/agent2_test/test_profile_agent.py`)
로컬에서 Mock 데이터를 바로 주입하여 Upstage API 호출 및 Pydantic 유효성 검증 결과를 터미널에 프린트하는 테스트 코드입니다.
```python
import os
import json
from pydantic import BaseModel, Field
from typing import Optional

# 1. 입력 및 출력 스키마 정의 (선택형 입력 사양 반영)
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

# 2. 시스템 프롬프트 (정형 입력 기반의 심층 진단 가이드라인 정의)
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

# 3. Solar v1 Chat Completions 실행 및 검증 함수
def run_solar_profile_test(user_input: OnboardingInput):
    api_key = os.getenv("UPSTAGE_API_KEY")
    if not api_key:
        raise ValueError("환경 변수 UPSTAGE_API_KEY가 설정되지 않았습니다.")
        
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.upstage.ai/v1"
    )
    
    user_prompt = f"User Onboarding Profile:\n{user_input.model_dump_json(indent=2)}"
    
    print("\n--- [Step 1] API 호출 중... ---")
    response = client.chat.completions.create(
        model="solar-1-mini-chat",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.1
    )
    
    output_text = response.choices[0].message.content
    print("\n--- [Step 2] 수신된 Raw JSON Output: ---")
    print(output_text)
    
    print("\n--- [Step 3] Pydantic 모델 검증 시작 ---")
    try:
        parsed_data = json.loads(output_text)
        profile = ProfileDiagnosis.model_validate(parsed_data)
        print("🎉 검증 성공! Pydantic 모델 파싱 완료:")
        print(profile.model_dump_json(indent=2))
        
        has_warning = False

        # 강점 근거 검증
        for strength in profile.strengths:
            if strength not in profile.evidence:
                print(f"⚠️ 경고: '{strength}' 강점에 대한 근거(evidence)가 누락되었습니다.")
                has_warning = True
        
        # 약점 근거 검증
        for weakness in profile.weaknesses:
            if weakness not in profile.evidence:
                print(f"⚠️ 경고: '{weakness}' 약점에 대한 근거(evidence)가 누락되었습니다.")
                has_warning = True

        if not has_warning:
            print("✅ 모든 진단 및 강점/약점에 대한 근거(evidence)가 정상 매핑되었습니다.")
            
    except Exception as e:
        print("❌ Pydantic 검증 실패: 출력이 기대 데이터 모델과 일치하지 않습니다.")
        print(f"상세 에러: {e}")

# 4. 모의 테스트 데이터 실행
if __name__ == "__main__":
    from openai import OpenAI
    
    mock_input = OnboardingInput(
        major="컴퓨터공학과 4학년 재학",
        current_status="재학생",
        interests=["프론트엔드 개발", "UI/UX"],
        owned_skills=["HTML/CSS", "JavaScript"],
        target_role="프론트엔드 엔지니어",
        company_type="스타트업",
        weekly_hours=30,
        concern=[
            "실무 프로젝트 협업 경험이 부족합니다.",
            "포트폴리오 작성이 걱정입니다."
        ]
    )
    
    try:
        run_solar_profile_test(mock_input)
    except Exception as e:
        print(f"실행 중 오류 발생: {e}")
        print("💡 UPSTAGE_API_KEY 환경 변수를 설정했는지 확인해 주세요.")
```

### 2) API 단독 서버 가동 테스트 코드 (`test/agent2_test/api_test_profile_agent.py`)
로컬 포트 8000에 단독 Uvicorn 서버를 구동시켜 Swagger UI 나 Postman 등으로 외부 POST 요청을 직접 쏴서 테스트할 수 있는 샌드박스 검증 코드입니다.
```python
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
```
