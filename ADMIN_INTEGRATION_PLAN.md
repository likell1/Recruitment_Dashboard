# 채용 대시보드 관리자 페이지 통합 기획안

> 작성일: 2026-06-12  
> 현재 스택: Python Flask (대시보드) + Spring Boot (메인 서버) + MySQL

---

## 1. 목표

채용 현황 대시보드를 웹사이트의 **관리자 전용 페이지**로 통합한다.  
외부에 노출되지 않는 내부 서비스로 운영하며, 높은 수준의 보안을 유지한다.

---

## 2. 아키텍처 설계

### 전체 구조

```
인터넷 (HTTPS)
    │
    ▼
[nginx]  ← TLS 종단, HTTP → HTTPS 강제 리다이렉트
    │
    ├─ /api/*, /*, ...   →  Spring Boot (메인 서버 :8080)
    │
    └─ /admin/*          →  Spring Boot (인증 게이트웨이)
                                  │
                          인증 통과 시 내부 프록시
                                  │
                                  ▼
                         [Flask 대시보드 :5000]
                         (127.0.0.1 바인딩, 외부 노출 X)
                                  │
                                  ▼
                              [MySQL DB]
```

### 핵심 원칙

- Flask 대시보드는 `127.0.0.1:5000`에만 바인딩 — 외부 직접 접근 불가
- 모든 `/admin/*` 요청은 Spring Boot가 먼저 인증 검증
- 인증 통과 후에만 Flask로 내부 프록시

---

## 3. 마이그레이션 방침

### Java 재작성 여부: **하지 않는다**

| 항목 | 이유 |
|------|------|
| Flask 코드 유지 | 재작성 공수 대비 이득 없음 |
| DB 연결 유지 | SSH 터널 + MySQL 연결 구조 그대로 사용 |
| 역할 분리 | Spring Boot = 인증/인가, Flask = 데이터 시각화 |

### Flask 측 변경사항 (최소)

- 바인딩 주소를 `0.0.0.0` → `127.0.0.1`로 변경
- Spring Boot 프록시 요청에 내부 인증 헤더(`X-Internal-Token`) 검증 추가
- 정적 파일 경로 `/admin/` prefix 대응

---

## 4. 인증/보안 설계

### 4-1. 관리자 계정 분리

- 일반 사용자 테이블과 **완전히 분리**된 `admin_users` 테이블 사용
- 비밀번호: bcrypt 해싱, 최소 12자, 대소문자+숫자+특수문자 조합 필수

```sql
CREATE TABLE admin_users (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    username    VARCHAR(50) UNIQUE NOT NULL,
    password    VARCHAR(255) NOT NULL,  -- bcrypt
    totp_secret VARCHAR(100) NOT NULL,  -- TOTP 시크릿 키
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_login  DATETIME
);
```

### 4-2. 로그인 플로우 (2FA 필수)

```
Step 1. /admin/login
        └→ username + password 입력

Step 2. Spring Security 1차 검증
        └→ admin_users 테이블 조회 + bcrypt 비교

Step 3. /admin/login/totp
        └→ Google Authenticator 6자리 코드 입력

Step 4. TOTP 코드 서버 검증 (30초 유효 윈도우)
        └→ 성공 시 JWT 발급

Step 5. JWT → HttpOnly + Secure + SameSite=Strict Cookie 저장
        └→ /admin/ 진입
```

### 4-3. JWT 설정

| 항목 | 설정값 | 이유 |
|------|--------|------|
| 만료시간 | 30분 | 탈취 시 피해 최소화 |
| Refresh Token | 미사용 | 관리자 페이지 특성상 재로그인 강제 |
| 저장 방식 | HttpOnly Secure Cookie | XSS로 탈취 불가 |
| 알고리즘 | RS256 (비대칭키) | 서버 측 검증 강화 |
| Payload | username, role, ip, iat, exp | IP 변경 시 무효화용 |

### 4-4. 세션 강화 정책

- **IP 고정**: JWT 발급 시 클라이언트 IP 포함, 요청마다 IP 불일치 시 즉시 만료
- **동시 로그인 차단**: 새 로그인 시 기존 세션 강제 만료
- **Idle Timeout**: 15분 비활성 시 자동 로그아웃 (프론트 타이머)
- **HTTPS 전용**: Spring Security `requiresSecureChannel()` 설정

### 4-5. 브루트포스 방어

- 로그인 실패 **5회** → 해당 IP **30분 잠금** (Redis 또는 인메모리 캐시)
- 잠금 해제는 관리자만 수동으로 가능
- 실패 시도 로그 DB 기록

### 4-6. 감사 로그 (Audit Log)

모든 관리자 행동을 `admin_audit_log` 테이블에 기록:

```sql
CREATE TABLE admin_audit_log (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    admin_id    BIGINT NOT NULL,
    action      VARCHAR(100) NOT NULL,  -- LOGIN, LOGOUT, VIEW_DASHBOARD 등
    ip_address  VARCHAR(45),
    user_agent  VARCHAR(255),
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

이상 접근 감지 시 **이메일 알림**:
- 새로운 IP에서 로그인 성공
- 새벽 시간대(00:00~06:00) 접근
- 로그인 실패 3회 이상

---

## 5. 기술 스택

| 레이어 | 기술 | 용도 |
|--------|------|------|
| 웹서버 | nginx | TLS 종단, 리버스 프록시 |
| 인증 서버 | Spring Boot + Spring Security | 로그인, JWT, 2FA |
| TOTP | warrenstrange/googleauth | Google Authenticator 연동 |
| JWT | jjwt (Java) | 토큰 발급/검증 |
| 대시보드 | Flask (Python) | 데이터 조회/시각화 |
| DB | MySQL | 채용 데이터 + 관리자 계정 |
| 캐시 | Redis (선택) | 로그인 실패 카운트, 세션 블랙리스트 |

---

## 6. 구현 단계

### Phase 1 — 인프라 준비
- [ ] nginx HTTPS 설정 (Let's Encrypt 또는 사설 인증서)
- [ ] Flask 바인딩 주소 `127.0.0.1`로 변경
- [ ] `admin_users`, `admin_audit_log` 테이블 생성
- [ ] 최초 관리자 계정 TOTP 시크릿 발급 및 등록

### Phase 2 — Spring Boot 인증 구현
- [ ] Spring Security 관리자 필터 체인 구성
- [ ] 로그인 API (`/admin/login`, `/admin/login/totp`)
- [ ] JWT 발급/검증 모듈
- [ ] IP 기반 세션 검증
- [ ] 로그인 실패 카운터 + IP 잠금
- [ ] 감사 로그 기록 AOP

### Phase 3 — Flask 프록시 연동
- [ ] Spring Boot → Flask 내부 프록시 설정
- [ ] Flask `X-Internal-Token` 헤더 검증 미들웨어 추가
- [ ] URL prefix `/admin/dashboard` 대응

### Phase 4 — 보안 점검
- [ ] OWASP Top 10 기준 점검
- [ ] 세션 탈취 시나리오 테스트
- [ ] 브루트포스 방어 동작 확인
- [ ] HTTPS 설정 점검 (SSL Labs A+ 목표)

---

## 7. nginx 설정 예시

```nginx
server {
    listen 443 ssl;
    server_name yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    # 관리자 경로 — Spring Boot가 인증 후 Flask로 프록시
    location /admin/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # 메인 서버
    location / {
        proxy_pass http://127.0.0.1:8080;
    }
}

# HTTP → HTTPS 강제
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}
```

---

## 8. 보안 체크리스트

- [ ] HTTPS 강제 적용
- [ ] 관리자 계정 2FA (TOTP) 필수
- [ ] JWT HttpOnly + Secure + SameSite=Strict
- [ ] Flask 외부 노출 차단 (127.0.0.1 바인딩)
- [ ] 로그인 실패 IP 잠금
- [ ] 감사 로그 전체 기록
- [ ] 이상 접근 이메일 알림
- [ ] IP 고정 세션
- [ ] SQL Injection 방어 (Prepared Statement)
- [ ] CSRF 토큰 (폼 전송 시)

---

## 9. 미결 사항 (추후 결정)

- Redis 도입 여부 (인메모리 vs Redis 캐시로 실패 카운트 관리)
- 관리자 계정 수 (단일 계정 vs 다중 계정 + 권한 분리)
- 도메인 분리 여부 (`admin.yourdomain.com` vs `/admin/` 경로)
- 사내망 IP 화이트리스트 제한 추가 여부
