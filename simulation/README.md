# 가격 정책 시뮬레이션 및 부하 테스트

이 폴더는 실제 고객 데이터가 없는 단계에서 가격 정책을 상대 비교하기 위한 도구다. 합성 사용자 결과는 시장 수요 검증이 아니며, 실제 전환 데이터가 생기면 구매확률을 재보정해야 한다.

## 백테스트

프로젝트 루트(`SSD_AI`)에서 실행한다.

```powershell
python -m simulation.backtest --start-date 2024-01-01 --end-date 2026-08-29 --sampled-days 180 --products-per-day 10 --seed 42 --minimum-agents 30000 --minimum-evaluation-agents 20000
```

지정 범위에서 날짜를 비복원 무작위 추출한다. 같은 seed는 같은 날짜와 사용자 행동을 만든다. 기본 실행은 전략별 합성 사용자 후보가 3만 명 미만이면 중단된다.

실험군은 가격 결정 방식과 운영 목표를 분리한 2×3 구조다.

| 가격 결정 방식 | 수익 우선 | 균형 | 재고 처리 우선 |
|---|---|---|---|
| 기존 규칙 | `rule_profit` | `rule_balanced` | `rule_sell_through` |
| 온라인 학습 Bandit | `bandit_profit` | `bandit_balanced` | `bandit_sell_through` |

추가 기준선 `fixed`는 정상가를 모든 사용자에게 그대로 제시한다.

수익 우선 Bandit은 매출총이익, 균형 Bandit은 마진과 판매 성공의 혼합값, 재고 처리 Bandit은 판매 성공과 마감 임박 가중치를 보상으로 학습한다. Bandit의 arm은 고정 할인율이 아니라 각 상품의 `정상가~최저가` 허용 구간 내 상대 위치 `0%, 10%, 20%, 35%, 50%, 70%, 100%`다. 따라서 실제 할인율과 가격 후보는 상품별 정상가·최저가에 따라 동적으로 달라진다.

현재 Bandit은 연속형 문맥을 상태 버킷보다 효율적으로 일반화하는 LinUCB다. 입력에는 수요, 재고율, 경과시간, 원가율, 허용 가격폭, 날씨 위험, 주말, 야외 상품 여부, 최근 판매 속도, 지금까지 관찰한 페르소나 구성비, arm별 누적 구매율, 후보 할인율과 후보 마진이 포함된다. 개인별 가격 차등이 아니라 집계된 과거 고객 구성만 사용한다. 평가 구간에서는 탐색과 학습을 모두 중단한다.

목표별 보상은 다음과 같다.

- 수익 우선: 공헌이익 100%
- 균형: 공헌이익 55% + 매출 35% + 판매 성공 10%
- 재고 처리 우선: 공헌이익 40% + 매출 35% + 판매 성공 25% (마감 가중치 포함)

과도한 할인 방지를 위해 정상가 대비 최소 공헌마진을 수익 20%, 균형 15%, 재고 처리 12%로 제한한다.

합성 원가는 기본적으로 `최저판매가 × Uniform(0.30, 0.50)`로 생성한다. 이 값은 시스템 실험용 가정일 뿐이며 사업 판단 전에는 실제 변동원가·결제수수료·판매수수료·이행비를 합한 공헌원가로 교체해야 한다. 민감도 실험은 `--cost-ratio-min`, `--cost-ratio-max`로 범위를 바꿔 실행한다.

당일 미판매 재고는 마감 시 전량 소멸한다고 가정한다. `미판매 손실 = 잔여 수량 × 단위원가`, `폐기 반영 순이익 = 매출총이익 - 미판매 손실`을 별도 지표로 저장한다. 회수 가능한 상품이라면 실제 잔존가치를 미판매 손실에서 차감해야 한다.

시나리오는 시간순 60% 학습/40% 평가로 분리한다. Bandit은 학습 구간에서만 탐색·업데이트하고 평가 구간에서는 정책을 동결한다. 기존 규칙과 Bandit의 최종 지표는 동일한 평가 구간에서만 계산한다. 비율은 `--train-ratio`로 변경할 수 있다.

결과는 `simulation/results/<run-id>/`에 저장된다.

- `summary.json`: 설정, 에이전트 수, 추출 날짜, 전략별 매출·매출총이익·미판매 손실·폐기 반영 순이익·전환율·판매율
- `events.csv`: 사용자 제안 및 구매 이벤트
- `backtest.sqlite3`: 이벤트와 요약을 조회할 수 있는 원본 DB

권장 검증 범위:

1. 최소 4계절을 포함한 90~180개 무작위 날짜, seed 10개 이상
2. 고정가 대비 매출, 전환율, 재고 소진율 및 평균 판매가
3. 페르소나별 전환 격차와 특정 집단의 과도한 가격 불이익
4. 비·성수기, 주말, 악천후, 저수요 및 재고 과잉 극단 상황
5. 실제 데이터 확보 후 예측 구매확률과 실제 전환율의 calibration

## 배포 서버 부하 테스트

먼저 읽기 전용 health endpoint로 램프업한 뒤 가격 API를 측정한다. 자신이 운영하거나 테스트 허가를 받은 서버에서만 실행한다.

```powershell
python -m simulation.load_test --base-url http://localhost:9000 --endpoint health
python -m simulation.load_test --base-url https://ssd-be.onrender.com --endpoint actuator-health --concurrency 1,3,5,10 --confirm-external
python -m simulation.load_test --base-url https://YOUR-SERVER --endpoint pricing --api-key YOUR_KEY --concurrency 1,5,10,25,50 --requests-per-stage 100 --confirm-external
```

결과는 `simulation/load-results/<timestamp>/`에 JSON과 CSV로 저장된다. 기본 합격 기준은 성공률 99% 이상, p95 1초 이하이며 이를 만족하는 마지막 단계의 동시성을 보수적 수용치로 기록한다. 실제 한계 판단은 10~30분 지속 부하, autoscaling/cold start, CPU·메모리·외부 날씨 API rate limit을 함께 관찰해야 한다.
