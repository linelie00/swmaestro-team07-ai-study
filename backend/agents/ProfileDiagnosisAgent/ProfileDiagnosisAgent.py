import os
import json
from pydantic import BaseModel, Field
from typing import Optional, List
from openai import OpenAI
from dotenv import load_dotenv

# 로컬 .env 파일로부터 환경 변수 자동 로드
load_dotenv()

# 1. 입력 및 출력 데이터 스키마 정의
class OnboardingInput(BaseModel):
    majorAndYear: str = Field(description="전공/학년")
    currentStatus: str = Field(description="현재 상태")
    interests: List[str] = Field(description="관심분야")
    targetJob: str = Field(description="목표 직무")
    preferredCompanyType: str = Field(description="희망 회사 유형")
    availableTime: str = Field(description="준비 가능 기간/시간")
    concerns: List[str] = Field(description="현재 고민")
    resumeText: Optional[str] = Field(default="", description="사용자 이력서 PDF 파싱 텍스트")

class ProfileDiagnosis(BaseModel):
    summary: str = Field(description="사용자의 전공, 상태, 고민 및 이력서 내용을 종합하여 취업 준비 현황을 요약한 2-3문장의 맥락 요약본")
    strengths: List[str] = Field(description="기준 대비 사용자가 지닌 상대적 강점 명칭 목록")
    weaknesses: List[str] = Field(description="기준 대비 사용자가 보완해야 할 상대적 약점/불리한 조건 목록")
    owned_skills: List[str] = Field(description="사용자의 이력서 및 온보딩 정보에서 추출된 현재 보유 중인 구체적인 기술 스택 및 역량 목록")
    evidence: dict[str, str] = Field(description="strengths, weaknesses 판단의 근거가 된 입력 및 이력서 팩트 매핑 정보")

# 2. 1단계 시스템 프롬프트: 기준 이력서(들)의 공통 강점/보완점 기준 분석
SYSTEM_PROMPT_STAGE1 = """
You are the CareerMate Senior Tech Recruiter and AI Benchmark Analyst.
Your task is to analyze the high-quality reference resume(s) provided for a specific IT role (it could be 1 global resume or 3 local resumes). These resumes represent successful candidates in this target field.

Analyze the profile(s) deeply and extract:
1. `common_strengths`: The core, high-priority skills, project accomplishments, or technical capabilities commonly possessed in the benchmark. These represent the "must-have" standards for this role.
2. `common_weaknesses`: Core areas, advanced technologies, or soft spots that are generally challenging or less expected for a junior in this role, but represent the boundary of what they need to watch out for or supplement (e.g., lack of large-scale system production experience, complex cost optimization).

[CORE CONSTRAINTS]
1. ABSOLUTELY NO HALLUCINATION: Extract only features, tools, and methodologies that are explicitly stated in the reference resumes. Do not invent any non-existent skills.
2. Nominal Phrases Only: Keep every item in `common_strengths` and `common_weaknesses` short and concise (under 25 characters, e.g., "RAG 파이프라인 최적화", "대규모 트래픽 분산 제어").
3. Your output MUST be a valid JSON matching the schema.
"""

# 3. 2단계 시스템 프롬프트: 1단계 기준 대비 사용자 맞춤 채점 및 owned_skills 추출
SYSTEM_PROMPT_STAGE2 = """
You are the CareerMate Career Coach and Profile Diagnostician.
Your task is to compare the user's onboarding profile and their resume text against the "Common Benchmark Criteria" (extracted from 100-point resumes in the target field).

You must analyze what the user is missing (weaknesses) compared to the benchmark, what they already satisfy (strengths), and list their current technical competencies (owned_skills).

[EVALUATION BIAS BY COMPANY TYPE]
{company_evaluation_bias}

[CROSS-LINGUAL SEMANTIC MAPPING RULE]
- The Reference Benchmark Criteria may be written in English (especially for global/foreign company targets), while the User's input (Onboarding/Resume) may be in Korean.
- You MUST map the semantic meaning across Korean and English languages intelligently. 
- For example, if the benchmark requires "Test-Driven Development (TDD)", and the user's Korean resume mentions "테스트 코드 작성" or "TDD 구현", you must recognize them as the exact same capability and classify it as a strength rather than a weakness.
- Never generate a weakness simply because the terminology language differs (e.g., matching 'CI/CD 구축' with 'CI/CD pipeline implementation').

[CORE CONSTRAINTS]
1. STRICTLY NO HALLUCINATION (CRITICAL):
   - Under no circumstances should you assume, guess, extrapolate, or fabricate any skills, programming languages, tools, certificates, or projects for the user.
   - If a technology, tool, or project is NOT explicitly mentioned in the user's onboarding profile or resume text, it MUST NEVER be listed in `owned_skills` or `strengths`.
   - If the user's resume is blank or lacks details, you must reflect that objectively (e.g., 'Insufficent project experience', 'Technical stacks missing') as a weakness.
2. `owned_skills` Extraction Rule:
   - Identify and list only the programming languages, libraries, frameworks, databases, and DevOps tools that the user has explicitly used in their listed projects or has directly declared in their skills section. Do not list generic concepts.
3. Detailed and Actionable `strengths` & `weaknesses` for Agent 3 Input:
   - The `strengths` and `weaknesses` lists will serve as direct inputs for Agent 3 to generate a personalized study roadmap.
   - Therefore, do not output vague or generic items. They must represent specific technical stacks or project experience gaps relative to the benchmark (e.g., instead of '개발 지식 부족', output 'Spring Boot 개발 경험 미비' or 'RAG 평가 프레임워크 사용 미경험').
   - Keep them nominal (under 25 characters) but highly specific and actionable.
4. `evidence`:
   - Map each strength and weakness to the exact source text from the user's onboarding or resume.

[OUTPUT FORMAT]
Your output MUST be a valid JSON object matching the ProfileDiagnosis schema.
It must contain exactly these 5 keys (do not miss any of them):
{{
  "summary": "현재 사용자의 상황(전공, 이력서 바탕)과 고민을 종합한 2-3문장의 정성 요약",
  "strengths": ["강점 명칭 1", "강점 명칭 2"],
  "weaknesses": ["보완점 명칭 1", "보완점 명칭 2"],
  "owned_skills": ["사용자 이력서에서 추출된 실제 보유 기술 1", "실제 보유 기술 2"],
  "evidence": {{
    "강점 명칭 1": "판단 근거가 된 이력서 또는 온보딩 원본 텍스트",
    "보완점 명칭 1": "판단 근거가 된 이력서 또는 온보딩 원본 텍스트"
  }}
}}
"""

# 4. 직무별 기준 이력서 매핑 헬퍼 함수
def get_reference_resume(target_job: str) -> str:
    cleaned_job = target_job.lower().replace(" ", "").replace("_", "")
    
    # 기본 fallback 파일명
    filename = "ref_BackEndEngineer.md"
    
    if "aiproduct" in cleaned_job:
        filename = "ref_AIProductEngineer.md"
    elif "backend" in cleaned_job:
        filename = "ref_BackEndEngineer.md"
    elif "frontend" in cleaned_job:
        filename = "ref_FrontEndEngineer.md"
    elif "data" in cleaned_job:
        filename = "ref_DataEngineer.md"
    elif "ml" in cleaned_job or "machine" in cleaned_job:
        filename = "ref_MLEngineer.md"
    elif "devops" in cleaned_job or "infra" in cleaned_job:
        filename = "ref_DevOpsEngineer.md"
        
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, "reference_resumes", filename)
    
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"기준 이력서 파일을 찾을 수 없습니다: {file_path}")
        
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()

# 5. 회사 유형별 동적 가중치 프롬프트 매핑 헬퍼
def get_company_evaluation_bias(company_type: str) -> str:
    cleaned_company = company_type.replace(" ", "")
    
    if "대기업" in cleaned_company or "중견기업" in cleaned_company or "공공기관" in cleaned_company:
        return """- The user prefers enterprise-level companies.
- Evaluate heavily on CS core fundamentals (Data Structures, Algorithms, OS, Databases), standard software engineering principles, testing coverage, scalability, and handling large-scale traffic or robust production systems.
- Highlight gaps in these areas as weaknesses if missing from the user's profile."""
    elif "스타트업" in cleaned_company or "테크스타트업" in cleaned_company:
        return """- The user prefers tech startups.
- Evaluate heavily on rapid prototyping capability, ownership of feature releases, integration of trendy tech stacks (e.g., FastAPI, Docker, Supabase, Next.js), lean development style, and user feedback-driven features.
- Highlight gaps in practical web/app building or active project execution as weaknesses if missing."""
    elif "외국계" in cleaned_company or "global" in cleaned_company.lower():
        return """- The user prefers global / foreign companies.
- Evaluate heavily on international communication capability (or English documentation skills), contribution to global open-source projects, active community participation, technical autonomy, and clean code/TDD principles.
- Highlight gaps in autonomy or global collaboration experience as weaknesses if missing."""
    else:
        return """- General company target.
- Balance both software engineering fundamentals and practical prototyping experiences."""


# 6. 백엔드 호출 동기식 클래스 인터페이스 정의
class Agent1:
    def default(
        self,
        major: str,
        currentStatus: str,
        interests: list[str],
        targetJob: str,
        preferredCompanyType: str,
        availableTime: str,
        concerns: list[str],
        resumeText: str = "" # PDF 텍스트 수용 (디폴트 빈 값 처리하여 TypeError 방지)
    ) -> dict:
        """
        백엔드 팀원의 main.py 호출 규격과 호환되도록 구성된 2단계 실시간 체이닝 분석 메서드입니다.
        """
        # 1. 입력 유효성 검증
        user_input = OnboardingInput(
            majorAndYear=major,
            currentStatus=currentStatus,
            interests=interests,
            targetJob=targetJob,
            preferredCompanyType=preferredCompanyType,
            availableTime=availableTime,
            concerns=concerns,
            resumeText=resumeText
        )
        
        api_key = os.getenv("UPSTAGE_API_KEY")
        if not api_key:
            raise ValueError("환경 변수 UPSTAGE_API_KEY가 설정되지 않았습니다.")
            
        client = OpenAI(
            api_key=api_key,
            base_url="https://api.upstage.ai/v1"
        )
        
        # 2. 직무 및 회사 유형에 맞는 100점짜리 기준 이력서 파일 읽어오기
        try:
            cleaned_job = user_input.targetJob.lower().replace(" ", "").replace("_", "")
            filename = "ref_BackEndEngineer.md" # fallback 기본
            if "aiproduct" in cleaned_job:
                filename = "ref_AIProductEngineer.md"
            elif "backend" in cleaned_job:
                filename = "ref_BackEndEngineer.md"
            elif "frontend" in cleaned_job:
                filename = "ref_FrontEndEngineer.md"
            elif "data" in cleaned_job:
                filename = "ref_DataEngineer.md"
            elif "ml" in cleaned_job or "machine" in cleaned_job:
                filename = "ref_MLEngineer.md"
            elif "devops" in cleaned_job or "infra" in cleaned_job:
                filename = "ref_DevOpsEngineer.md"
            
            # 사용자가 외국계 기업을 타겟팅했는지 여부 체크
            is_foreign_target = "외국계" in user_input.preferredCompanyType or "global" in user_input.preferredCompanyType.lower()
            
            if is_foreign_target:
                filename = "ref_foreign_" + filename.replace("ref_", "")
                print(f"ℹ️ [Agent1] 외국계 전용 기준 이력서 단독 로드: {filename}")
            else:
                print(f"ℹ️ [Agent1] 일반 기준 이력서 로드: {filename}")
                
            current_dir = os.path.dirname(os.path.abspath(__file__))
            file_path = os.path.join(current_dir, "reference_resumes", filename)
            
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    reference_resume_content = f.read()
            else:
                raise FileNotFoundError(f"기준 이력서 파일이 존재하지 않습니다: {file_path}")
        except Exception as e:
            print(f"[Agent1 Error] 기준 이력서 로딩 실패: {e}")
            # 로딩 실패 시 백업용으로 빈 텍스트 지정하여 빌드 유지
            reference_resume_content = "No reference resumes available."
        
        # 3. [1단계 API 호출] 100점짜리 이력서 공통 특징 분석
        print("💡 [Agent1 - Stage 1] 100점짜리 기준 이력서 분석 중...")
        stage1_prompt = f"Role: {user_input.targetJob}\n\nReference Resumes:\n{reference_resume_content}"
        
        response_stage1 = client.chat.completions.create(
            model="solar-pro3",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_STAGE1},
                {"role": "user", "content": stage1_prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            timeout=90.0 # 90초 타임아웃으로 연장
        )
        
        stage1_output_text = response_stage1.choices[0].message.content
        stage1_data = json.loads(stage1_output_text)
        
        print(f"✅ [Agent1 - Stage 1 성공] 공통 강점 {len(stage1_data.get('common_strengths', []))}개 / 공통 보완점 {len(stage1_data.get('common_weaknesses', []))}개 추출 완료.")
        print("\n================ [Agent1 Stage 1 Criteria Output] ================")
        print(json.dumps(stage1_data, ensure_ascii=False, indent=2))
        print("===================================================================\n")
        
        # 4. [2단계 API 호출 준비] 회사 유형 가중치 주입 및 프롬프트 조립
        company_bias = get_company_evaluation_bias(user_input.preferredCompanyType)
        formatted_stage2_system = SYSTEM_PROMPT_STAGE2.format(company_evaluation_bias=company_bias)
        
        stage2_user_prompt = f"""Target IT Role: {user_input.targetJob}

[Common Benchmark Criteria (from 100-point Resumes)]
{json.dumps(stage1_data, ensure_ascii=False, indent=2)}

[User Onboarding Profile]
- majorAndYear: {user_input.majorAndYear}
- currentStatus: {user_input.currentStatus}
- interests: {", ".join(user_input.interests)}
- availableTime: {user_input.availableTime}
- concerns: {", ".join(user_input.concerns)}

[User Resume Text (extracted from PDF)]
{user_input.resumeText if user_input.resumeText else "No resume text provided."}
"""
        
        # 5. [2단계 API 호출 진행] 기준 대비 사용자 개별 맞춤 채점 및 owned_skills 추출
        print(f"💡 [Agent1 - Stage 2] 기준 대비 사용자 프로필/이력서 정밀 분석 중... (입력 데이터 약 {len(stage2_user_prompt)}자)")
        response_stage2 = client.chat.completions.create(
            model="solar-pro3",
            messages=[
                {"role": "system", "content": formatted_stage2_system},
                {"role": "user", "content": stage2_user_prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            timeout=90.0 # 90초 타임아웃으로 연장
        )
        
        stage2_output_text = response_stage2.choices[0].message.content
        parsed_diagnosis = json.loads(stage2_output_text)
        
        # 6. 최종 스키마 정합성 검증 후 딕셔너리로 반환 (summary, strengths, weaknesses, owned_skills, evidence)
        profile_diagnosis = ProfileDiagnosis.model_validate(parsed_diagnosis)
        
        print("\n================ [Agent1 Final Output Diagnosis] ================")
        print(json.dumps(profile_diagnosis.model_dump(), ensure_ascii=False, indent=2))
        print("=================================================================\n")
        
        return profile_diagnosis.model_dump()