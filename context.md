# 주의사항
깃허브 커밋 시, co-authorized by cursor 등의 멘트 금지

# 지원자 대시보드 — 작업 컨텍스트

## 목적
동아리 웹사이트의 지원 폼 데이터를 MySQL DB에서 읽어와, 지원서를 편하게 읽고 평가할 수 있는 로컬 대시보드.

---

## 기술 스택
- **백엔드**: Python + Flask
- **DB 연결**: pymysql + sshtunnel (SSH 터널 자동 연결)
- **평가 저장**: 로컬 SQLite (`evaluations.db`) — 서버 DB는 읽기만 함
- **프론트**: 바닐라 JS + CSS (외부 라이브러리 없음, 단일 HTML 파일)

---

## DB 스키마 (서버 MySQL)

### `applicant` 테이블
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | BIGINT PK | |
| recruitment_id | BIGINT FK | 지원 공고 ID |
| user_id | BIGINT FK | 사용자 ID (not null) |
| status | ENUM(DRAFT, SUBMITTED) | 기본값 SUBMITTED |
| track | VARCHAR(50) | 분석 / 시각화 / 엔지니어링 |
| name | VARCHAR(100) | 성명 |
| email | VARCHAR(255) | |
| phone | VARCHAR(20) | |
| university | VARCHAR(100) | |
| major | VARCHAR(100) | 본전공 |
| minor_double_major | JSON | 복수/부전공 (여러 개 가능) |
| last_semester | INT | 마지막 재학 학기 |
| military_status | VARCHAR(50) | 필_또는_면제 / 미필 |
| birth_date | DATE | |
| graduation_date | VARCHAR(7) | YYYY-MM 형식 |
| grad_school_plan | BOOLEAN | 대학원 진학 여부 |
| created_at | DATETIME | |
| updated_at | DATETIME | |

### `applicant_answer` 테이블
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | BIGINT PK | |
| applicant_id | BIGINT FK | applicant.id 참조 |
| question_id | BIGINT FK | 질문 ID (question 테이블 참조 추정) |
| answer_text | TEXT | 서술형 답변 |
| answer_json | JSON | 표 형태 답변 (예: `{"Python":"프로젝트 경험", "Java":"수업 수강"}`) |
| created_at | DATETIME | |
| updated_at | DATETIME | |

> **참고**: `question` 테이블 스키마 미확인. 현재 `question_id` 값을 그대로 레이블로 표시함.

---

## 파일 구조
```
웹사이트 지원 폼 대시보드/
├── app.py                  # Flask 백엔드 (API + SSH 터널)
├── config.yaml             # 실제 설정 파일 (gitignore 대상)
├── config.yaml.example     # 설정 템플릿
├── requirements.txt        # Python 패키지
├── start.sh                # 실행 스크립트
├── evaluations.db          # 로컬 평가 저장 (자동 생성)
└── templates/
    └── index.html          # 대시보드 UI (CSS + JS 포함)
```

---

## API 엔드포인트

| Method | URL | 설명 |
|--------|-----|------|
| GET | `/` | 대시보드 HTML |
| GET | `/api/recruitments` | 공고 ID 목록 |
| GET | `/api/stats?recruitment_id=` | 전체/트랙별 지원자 수 |
| GET | `/api/applicants` | 지원자 목록 (필터/검색/정렬) |
| GET | `/api/applicants/<id>` | 지원자 상세 + 답변 + 평가 |
| POST | `/api/evaluations/<id>` | 평가 저장 (SQLite) |

### `/api/applicants` 쿼리 파라미터
- `recruitment_id`, `track`, `status` — MySQL WHERE 필터
- `decision` — 로컬 SQLite 평가 결정 필터 (Python 레벨)
- `search` — name, email, university, major LIKE 검색
- `sort` — name / created_at / track / university / major
- `order` — asc / desc

---

## 로컬 SQLite 스키마 (`evaluations.db`)
```sql
CREATE TABLE evaluations (
    applicant_id INTEGER PRIMARY KEY,
    decision     TEXT    DEFAULT 'pending',  -- pending/pass/fail/hold
    score        REAL,                        -- 1~10
    memo         TEXT,
    updated_at   TEXT
);
```

---

## 설정 파일 형식 (`config.yaml`)
```yaml
ssh:
  host: your-server.com
  port: 22
  username: ubuntu
  pkey: ~/.ssh/id_rsa       # 키 인증
  # password: yourpassword  # 비밀번호 인증 시

database:
  host: 127.0.0.1           # 서버 내 MySQL 바인딩 주소
  port: 3306
  user: root
  password: your_db_password
  database: your_db_name
```

SSH 터널: `config.yaml`에 `ssh:` 섹션이 있으면 앱 시작 시 자동으로 `localhost:13306 → 서버:3306` 터널 생성.

---

## 실행 방법
```bash
cd "웹사이트 지원 폼 대시보드"

# 최초 실행 시
cp config.yaml.example config.yaml
# config.yaml 편집 후

bash start.sh
# → http://localhost:5050
```

또는 수동:
```bash
pip install -r requirements.txt
python app.py
```

---

## 미완성 / 추후 개선 가능 항목
- `question` 테이블 스키마 확인 후 질문 텍스트 표시 (현재는 question_id 숫자만 표시)
- 평가 결과 CSV 내보내기
- 팀원 공유용 배포 (현재 로컬 전용)
- 지원자 간 비교 뷰