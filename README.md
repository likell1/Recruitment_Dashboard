# BOAZ 채용 심사 대시보드

BOAZ 지원자 데이터를 조회·심사하기 위한 내부 관리 도구입니다.

---

## 백엔드 구조

```
로컬 머신
│
├── Flask 서버 (app.py, port 5050)
│   ├── SSH 터널 (subprocess)  ──▶  EC2 (15.165.102.5:22)
│   │                                    │
│   │   localhost:13306 ◀─────────────── └──▶ AWS RDS MySQL (boaz)
│   │
│   └── SQLite (evaluations.db)  ← 심사 결과만 로컬 저장
│
└── 브라우저 (index.html, 단일 페이지)
```

### 구성 요소

| 구성 | 역할 |
|------|------|
| **Flask** | REST API 서버. 모든 요청마다 MySQL 커넥션을 새로 열고 닫음 (커넥션 풀 없음) |
| **SSH 터널** | 앱 시작 시 `subprocess`로 `ssh -L 13306:RDS:3306 EC2` 실행. 이후 MySQL 접속은 `localhost:13306`으로 투명하게 처리 |
| **MySQL (RDS)** | 지원자(`applicants`), 질문(`application_question`), 답변(`applicant_answer`) 원본 데이터 |
| **SQLite** | 심사 결과(`decision`, `score`, `memo`)를 로컬에 저장. MySQL에는 쓰지 않음 |

### 데이터베이스 테이블

```
applicants
├── id, name, email, phone, birth_date
├── track (ANALYSIS / VISUALIZATION / ENGINEERING)
├── status (SUBMITTED / DRAFT)
├── university, major, minor_double_major
├── last_semester, graduation_date, grad_school_plan
├── military_status
├── recruitment_id
└── created_at

application_question
├── id, recruitment_id
├── category (COMMON / ANALYSIS / VISUALIZATION / ENGINEERING)
├── label, content, order_num

applicant_answer
├── applicant_id, question_id
├── answer_text, answer_json

evaluations (SQLite, 로컬)
├── applicant_id (PK)
├── decision (pending / pass / fail / hold)
├── score
├── memo
└── updated_at
```

---

## API 구조

### 엔드포인트 목록

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/` | 대시보드 HTML 반환 |
| `GET` | `/api/recruitments` | 공고 ID 목록 |
| `GET` | `/api/stats` | 전체/트랙별/상태별 지원자 수 |
| `GET` | `/api/applicants` | 지원자 목록 (필터·정렬 포함) |
| `GET` | `/api/applicants/<id>` | 지원자 상세 + 질문/답변 + 심사 결과 |
| `GET` | `/api/applicants/export` | 지원자 목록 CSV 다운로드 |
| `POST` | `/api/evaluations/<id>` | 심사 결과 저장 (SQLite) |
| `GET` | `/_debug/tables` | DB 테이블 스키마 확인 (개발용) |

---

### `GET /api/recruitments`

공고 ID 목록을 내림차순으로 반환합니다.

```json
[25, 24, 23]
```

---

### `GET /api/stats`

**Query params**

| 파라미터 | 설명 |
|----------|------|
| `recruitment_id` | 공고 ID (없으면 전체) |

**Response**

```json
{
  "total": 120,
  "by_track": [
    {"track": "ANALYSIS", "cnt": 45},
    {"track": "VISUALIZATION", "cnt": 30},
    {"track": "ENGINEERING", "cnt": 45}
  ],
  "by_status": [
    {"status": "SUBMITTED", "cnt": 110},
    {"status": "DRAFT", "cnt": 10}
  ]
}
```

---

### `GET /api/applicants`

**Query params**

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `recruitment_id` | - | 공고 ID |
| `track` | - | `ANALYSIS` / `VISUALIZATION` / `ENGINEERING` |
| `status` | - | `SUBMITTED` / `DRAFT` |
| `search` | - | 이름·이메일·대학·전공 부분 검색 |
| `decision` | - | `pending` / `pass` / `fail` / `hold` (SQLite 필터, Python에서 처리) |
| `sort` | `created_at` | `name` / `created_at` / `track` / `university` / `major` |
| `order` | `desc` | `asc` / `desc` |

**Response** — 배열, 각 항목에 `eval` 필드(SQLite) 포함

```json
[
  {
    "id": 1,
    "name": "홍길동",
    "track": "ANALYSIS",
    "status": "SUBMITTED",
    "university": "서울대학교",
    "major": "통계학과",
    "email": "hong@example.com",
    "created_at": "2025-03-01T12:00:00",
    "eval": {
      "decision": "pass",
      "score": 85.0,
      "memo": "우수한 지원자"
    }
  }
]
```

> `decision` 필터는 MySQL WHERE가 아닌 Python에서 처리됩니다. MySQL은 전체 결과를 반환하고, 이후 Python에서 SQLite eval과 조인해 필터링합니다.

---

### `GET /api/applicants/<id>`

지원자 상세 정보. 질문·답변은 `COMMON` 문항과 해당 트랙 문항만 반환합니다.

**Response**

```json
{
  "id": 1,
  "name": "홍길동",
  "track": "ANALYSIS",
  "answers": [
    {
      "question_id": 10,
      "q_label": "지원동기",
      "q_content": "지원 동기를 작성해 주세요.",
      "q_order": 1,
      "answer_text": "...",
      "answer_json": null
    }
  ],
  "evaluation": {
    "decision": "pass",
    "score": 85.0,
    "memo": "우수한 지원자",
    "updated_at": "2025-03-10T15:30:00"
  }
}
```

---

### `GET /api/applicants/export`

`/api/applicants`와 동일한 필터 파라미터를 받아 CSV 파일로 반환합니다.

- 인코딩: UTF-8 BOM (Excel 호환)
- 파일명: `지원자_YYYYMMDD.csv`
- 컬럼: 기본 정보 18개 + 질문 답변 컬럼 (question_id 순)

---

### `POST /api/evaluations/<id>`

심사 결과를 SQLite에 저장합니다 (UPSERT). MySQL에는 기록하지 않습니다.

**Request body**

```json
{
  "decision": "pass",
  "score": 85.0,
  "memo": "우수한 지원자"
}
```

**Response**

```json
{"ok": true}
```

---

## 프론트엔드 API 호출 흐름

```
페이지 로드
│
├── loadRecruitments()  →  GET /api/recruitments
│
└── (병렬)
    ├── loadStats()       →  GET /api/stats?recruitment_id=...
    └── loadApplicants()  →  GET /api/applicants?...

공고 변경 / 필터 변경 / 검색
└── loadStats() + loadApplicants()  (동시 호출)

지원자 클릭
└── openPanel(id)  →  GET /api/applicants/:id

심사 결과 저장
└── saveEval()  →  POST /api/evaluations/:id
               →  GET /api/applicants/:id  (패널 갱신)

CSV 다운로드
└── downloadCSV()  →  GET /api/applicants/export?...  (현재 필터 그대로)
```

> 모든 API 호출은 `fetch`를 직접 사용하며, 별도 상태관리 라이브러리 없이 전역 상태 객체 `S`로 관리합니다.

---

## 설치 및 실행

### 요구사항

- Python 3.9+
- SSH 키 (`~/.ssh/boaz_codedeploy.pem`)
- `config.yaml` (아래 예시 참고)

### 설정

```bash
cp config.yaml.example config.yaml
# config.yaml 편집 후 저장
```

### 실행

```bash
pip install -r requirements.txt
bash start.sh
# 또는
python app.py
```

브라우저에서 `http://localhost:5050` 접속

---

## 주의사항

- `config.yaml`은 DB 비밀번호와 서버 IP를 포함하므로 **절대 커밋하지 마세요** (`.gitignore` 처리됨)
- `evaluations.db`는 로컬 심사 결과 DB입니다. 여러 명이 사용할 경우 동기화되지 않습니다
- 매 API 요청마다 MySQL 커넥션을 새로 열므로, 필터 변경이 잦으면 응답이 느릴 수 있습니다
