# 인프라 사용량 및 3개년 예상비용 최종보고서

## 결론

운영 권장 최소 구성(Render Starter 2대 + PostgreSQL Basic-256mb) 기준 예상 비용은 1년차 월 27,056원, 2년차 월 27,205원, 3년차 월 27,604원이다. 현재 free 구성의 청구액은 0달러일 수 있으나 운영용으로 권장하지 않는다.

## 가격 기준

- Render Starter: $7/service-month, Backend와 AI 각 1대
- Render PostgreSQL Basic-256mb: $6/month
- AWS S3 서울: Storage $0.025/GB-month, GET $0.0035/10,000, PUT $0.0045/1,000; 월 100GB 이내 인터넷 전송 무료 적용
- GPT-5.6 Luna: OpenAI 공식 단가 Input $0.20/M, Cached Input $0.02/M, Output $1.20/M. 코드에 prompt caching이 없어 일반 Input 적용
- 환율: 1 USD = 1,350.35 KRW (Frankfurter/ECB, 2026-09-04)
- 조회일: 2026-09-06. Render https://render.com/pricing, AWS https://aws.amazon.com/s3/pricing/, OpenAI https://developers.openai.com/api/docs/pricing

## 예상 비용

| 연차 | USD/월 | KRW/월 | KRW/년 | 매출 대비 | 인프라 BEP |
|---|---:|---:|---:|---:|---:|
| 1 | $20.04 | 27,056원 | 324,676원 | 4.008% | 37건 |
| 2 | $20.15 | 27,205원 | 326,464원 | 1.008% | 37건 |
| 3 | $20.44 | 27,604원 | 331,244원 | 0.341% | 37건 |

GPT actual API calls = 0. 구매 생성 = 0. S3 객체 변경/삭제 = 0.
