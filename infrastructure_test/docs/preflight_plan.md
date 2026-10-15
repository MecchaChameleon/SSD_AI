# 인프라 사용량 측정 사전 분석 및 실행 계획

## 1. 프로젝트 구조

- `SSD_FE`: React Native/Expo 프론트엔드. 화면 진입과 사용자 행동별 API 호출 근거.
- `SSD_BE`: Spring Boot 3.3.4 Backend. PostgreSQL(JPA + JdbcTemplate), S3 업로드, OpenAI Responses API 호출 포함.
- `SSD_AI`: FastAPI 가격 추천 서비스와 기존 백테스트/부하테스트 도구. 이번 측정 산출물 저장 위치.

## 2. Backend endpoint 분류

- 인증: Kakao 로그인 시작/콜백/티켓 교환, 내 정보 조회·탈퇴
- 구매자: 상품 목록·상세·지도, 구매·구매내역, 찜, AI 상품 추천
- 판매자: 사업자 신청·프로필, 상품 CRUD·이미지, 가격 추천·자동가격·전략, 결제 승인·환불, 매출·정산
- 사용자: 프로필 이미지, 알림·알림 설정
- 운영: health/info

전체 endpoint 원본은 `raw/endpoint_inventory.csv`로 저장한다.

## 3. DB 연결 구조

- PostgreSQL JDBC + JPA/Hibernate, Flyway migration 사용.
- 운영 DB 자격증명은 Render 환경변수에 있으며 본 작업 환경에는 직접 DB 접속 권한이 없다.
- API별 SQL 종류는 소스코드 추적으로 기록할 수 있지만 `pg_stat_statements`, DB 크기, 실제 connection peak는 직접 측정 불가(D)다.

## 4. S3 구조

- `FILE_STORAGE_TYPE=s3`일 때 AWS SDK `PutObject`로 상품/프로필 이미지를 업로드한다.
- Backend는 공개 S3 URL을 DB와 API 응답에 저장하고, 이미지 GET은 앱/브라우저가 직접 수행한다.
- 운영 객체 PUT/DELETE는 금지한다. 응답에서 확인된 공개 이미지 URL의 안전한 GET만 측정한다.

## 5. GPT 호출 위치

- 유일하게 확인된 OpenAI 호출은 `POST /api/buyer/ai-recommendations` → `BuyerAiRecommendationService.callOpenAi` → `POST https://api.openai.com/v1/responses`다.
- 사용자 행동 1회당 OpenAI 호출은 후보 상품이 존재할 때 1회다.
- 사용자 query는 최대 300자이며, 최대 20개 후보 상품 facts와 날씨를 JSON input으로 포함한다.
- 코드상 max output token 설정과 GPT 응답 캐시는 없다. 동일 요청은 중복 호출될 수 있다.
- 본 테스트에서는 endpoint 자체를 호출하지 않고 actual GPT calls를 항상 0으로 유지한다.

## 6. 기존 테스트 코드

- `simulation/load_test.py`: health/가격 API 단계 부하
- `simulation/purchase_load_test.py`: 실제 구매 생성형 테스트(이번 실행 금지)
- `simulation/concurrent_users_load_test.py`: 조회 및 구매 혼합 테스트(쓰기 경로 때문에 이번 실행에 그대로 사용하지 않음)

## 7. 사용자 행동별 API 구조

- 홈/목록: `GET /api/products`
- 상품 상세: `GET /api/products/{id}`
- 지도: `GET /api/products/map` 후 선택 시 상품 상세
- 구매내역: `GET /api/buyer/purchases`
- 구매: `POST /api/buyer/purchases` 1회. Backend 내부에서 예약 INSERT, 재고 UPDATE, 알림 INSERT, 결과 SELECT 및 가격 재계산 발생.
- AI 구매 추천: `POST /api/buyer/ai-recommendations` 1회 + OpenAI 1회(후보 존재 시).
- 판매자 홈: dashboard, payments, sales report/history를 병렬 호출.

## 8. 측정 가능성

- A 직접 측정: HTTP status, request/response byte, latency, 공개 이미지 GET byte.
- B 직접 측정 기반 환산: 연차별 API traffic, S3 GET/전송량.
- C 사업 가정: 행동 비율, 거래 Case 기간, 피크 집중도, 신규 상품/이미지 업로드량, GPT output token.
- D 측정 불가: Render CPU/RAM/network 내부 계측, 운영 PostgreSQL 크기·connection·query latency(권한 없음).

## 9. 운영 데이터 위험

- 구매, 찜, 알림 읽음, 판매자 승인, 상품 CRUD, 이미지 PUT/DELETE는 운영 상태를 변경하므로 호출하지 않는다.
- GPT endpoint는 유료 호출을 발생시키므로 호출하지 않는다.
- 공개 GET endpoint와 인증된 조회 endpoint만 실행한다.

## 10. 실행 계획

1. Controller와 프론트 API를 기반으로 endpoint inventory 생성
2. 대표 조회 endpoint를 반복 호출해 raw HTTP 측정
3. 응답의 이미지 URL을 분석하고 공개 객체 GET을 제한적으로 측정
4. 구매·DB·GPT는 코드 추적 dry-run 카운터 생성
5. 일반 98.99%, 구매 1%, GPT 0.01%로 연차별 사용량 환산
6. 거래 Case를 연간으로 본 모델 A와 월간으로 본 모델 B 모두 생성
7. raw/summary CSV와 Markdown/DOCX 보고서 생성

