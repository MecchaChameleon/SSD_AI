from pathlib import Path
import json
from PIL import Image, ImageDraw, ImageFont
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.shared import Inches, Pt, RGBColor

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'simulation'/'results-v7'/'20260830T193054-0e7fea7e'
OUT=ROOT/'simulation'/'합성_사용자_기반_가격정책_비교실험_보고서_폐기손실반영본.docx'
CHARTS=ROOT/'simulation'/'report-charts'; CHARTS.mkdir(exist_ok=True)
DATA=json.loads((RUN/'summary.json').read_text(encoding='utf-8'))['strategies']
FONT='C:/Windows/Fonts/malgun.ttf'; BOLD='C:/Windows/Fonts/malgunbd.ttf'
BLUE=(37,99,235); ORANGE=(249,115,22); NAVY=(15,23,42); GRAY=(100,116,139)

def font(size,bold=False): return ImageFont.truetype(BOLD if bold else FONT,size)
def chart(metric,title,subtitle,fmt,name):
    im=Image.new('RGB',(1600,900),'white'); d=ImageDraw.Draw(im)
    d.text((80,55),title,font=font(48,True),fill=NAVY); d.text((80,125),subtitle,font=font(25),fill=(71,85,105))
    goals=[('profit','수익 우선'),('balanced','균형'),('sell_through','재고 처리 우선')]
    vals=[DATA[f'{kind}_{g}'][metric] for g,_ in goals for kind in ('rule','bandit')]; top=max(vals)*1.12
    baseline=700
    for i,(g,label) in enumerate(goals):
        base=220+i*470
        for j,(kind,color) in enumerate((('rule',BLUE),('bandit',ORANGE))):
            v=DATA[f'{kind}_{g}'][metric]; h=int(v/top*470); x=base+j*130
            d.rounded_rectangle((x,baseline-h,x+100,baseline),radius=7,fill=color)
            value=fmt(v); box=d.textbbox((0,0),value,font=font(22,True)); d.text((x+50-(box[2]-box[0])/2,baseline-h-34),value,font=font(22,True),fill=NAVY)
        box=d.textbbox((0,0),label,font=font(25,True)); d.text((base+115-(box[2]-box[0])/2,735),label,font=font(25,True),fill=NAVY)
    d.rectangle((1110,180,1138,199),fill=BLUE); d.text((1150,174),'기존 규칙',font=font(20),fill=NAVY)
    d.rectangle((1300,180,1328,199),fill=ORANGE); d.text((1340,174),'Bandit',font=font(20),fill=NAVY)
    d.text((80,840),'전략별 합성 에이전트 후보 54,623명 · 180일 · 상품 1,800개 · seed 42',font=font(18),fill=GRAY)
    path=CHARTS/name; im.save(path,quality=95); return path

charts=[
 chart('net_profit_after_waste','당일 미판매 손실 반영 순이익','잔여 재고 원가를 전액 손실로 차감한 최종 비교',lambda v:f'{v/1e8:.2f}억원','04_net_profit_after_waste.png'),
 chart('gross_profit','목표별 매출총이익 비교','수익 우선 비교에서 Bandit이 기존 규칙의 이익을 상회',lambda v:f'{v/1e8:.1f}억원','01_gross_profit.png'),
 chart('revenue','목표별 매출 비교','Bandit 수익 우선이 전체 실험군 중 최고 매출',lambda v:f'{v/1e8:.1f}억원','02_revenue.png'),
 chart('sell_through_rate','목표별 재고 소진율 비교','재고 처리 우선 Bandit이 가장 높은 소진율을 기록',lambda v:f'{v*100:.1f}%','03_sell_through.png')]

def baseline_chart():
    im=Image.new('RGB',(1600,900),'white'); dr=ImageDraw.Draw(im)
    dr.text((80,55),'고정가 기준선 대비 대표 정책',font=font(48,True),fill=NAVY)
    keys=[('fixed','고정가',GRAY),('rule_balanced','기존 규칙\n균형',BLUE),('bandit_profit','Bandit\n수익 우선',ORANGE)]
    vals=[DATA[k]['gross_profit'] for k,_,_ in keys]; top=max(vals)*1.12; baseline=700
    for i,((key,label,color),v) in enumerate(zip(keys,vals)):
        x=300+i*420; h=int(v/top*470); dr.rounded_rectangle((x,baseline-h,x+180,baseline),radius=8,fill=color)
        value=f'{v/1e8:.2f}억'; box=dr.textbbox((0,0),value,font=font(25,True)); dr.text((x+90-(box[2]-box[0])/2,baseline-h-40),value,font=font(25,True),fill=NAVY)
        for line_no,line in enumerate(label.split('\n')):
            box=dr.textbbox((0,0),line,font=font(24,True)); dr.text((x+90-(box[2]-box[0])/2,735+line_no*32),line,font=font(24,True),fill=NAVY)
    dr.text((80,840),'평가지표: 매출총이익 · 공통 평가 사용자 22,003명',font=font(18),fill=GRAY)
    path=CHARTS/'00_baseline_comparison.png'; im.save(path,quality=95); return path

charts.insert(0,baseline_chart())

d=Document(); sec=d.sections[0]; sec.top_margin=Inches(.65); sec.bottom_margin=Inches(.65); sec.left_margin=Inches(.72); sec.right_margin=Inches(.72)
styles=d.styles; styles['Normal'].font.name='맑은 고딕'; styles['Normal'].font.size=Pt(10.5)
for s,size,color in [('Title',26,NAVY),('Heading 1',18,BLUE),('Heading 2',14,NAVY)]:
    styles[s].font.name='맑은 고딕'; styles[s].font.size=Pt(size); styles[s].font.color.rgb=RGBColor(*color)

def p(text='',style=None,bold=False):
    x=d.add_paragraph(style=style); r=x.add_run(text); r.bold=bold; return x
def table(headers,rows,widths=None):
    t=d.add_table(rows=1,cols=len(headers)); t.alignment=WD_TABLE_ALIGNMENT.CENTER; t.style='Table Grid'
    for i,h in enumerate(headers):
        c=t.rows[0].cells[i]; c.text=str(h); c.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
        for r in c.paragraphs[0].runs: r.bold=True; r.font.color.rgb=RGBColor(255,255,255)
        c._tc.get_or_add_tcPr().append(__import__('docx').oxml.parse_xml('<w:shd {} w:fill="2563EB"/>'.format(__import__('docx').oxml.ns.nsdecls('w'))))
    for row in rows:
        cells=t.add_row().cells
        for i,v in enumerate(row): cells[i].text=str(v); cells[i].vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
    return t
def picture(path,caption):
    x=d.add_paragraph(); x.alignment=WD_ALIGN_PARAGRAPH.CENTER; x.add_run().add_picture(str(path),width=Inches(7.0))
    c=d.add_paragraph(caption); c.alignment=WD_ALIGN_PARAGRAPH.CENTER; c.runs[0].italic=True; c.runs[0].font.color.rgb=RGBColor(*GRAY)

title=p('합성 사용자 기반 가격 정책 비교 실험 보고서','Title'); title.alignment=WD_ALIGN_PARAGRAPH.CENTER
sub=p('대규모 에이전트 시뮬레이션 결과 및 구매확률 모델'); sub.alignment=WD_ALIGN_PARAGRAPH.CENTER
p('실험 범위: 2024-01-01~2026-08-29 · 무작위 180일 · 상품 1,800개 · 전략별 합성 사용자 후보 54,623명')

p('요약','Heading 1')
p('가격 결정 방식(기존 규칙/Bandit)과 운영 목표(수익 우선/균형/재고 처리 우선)를 분리한 2×3 실험을 수행했다. Bandit은 시간순 앞 60%에서 학습하고 뒤 40%에서는 탐색과 업데이트를 중단했다. 수익 우선 비교에서 Bandit이 기존 규칙보다 매출과 매출총이익을 모두 높였고, 최대 재고 소진율은 Bandit 재고 처리 우선에서 나타났다. 합성 결과는 정책 간 상대 비교이며 실제 고객 수요의 증거는 아니다.')

p('1. 실험 설계','Heading 1'); p('1.1 실험군','Heading 2')
table(['가격 결정 방식','수익 우선','균형','재고 처리 우선'],[['고정가 기준선','fixed','fixed','fixed'],['기존 규칙','rule_profit','rule_balanced','rule_sell_through'],['Contextual Bandit','bandit_profit','bandit_balanced','bandit_sell_through']])
p('1.2 실행 규모','Heading 2')
table(['항목','설정'],[['날짜 범위','2024-01-01~2026-08-29'],['무작위 추출 날짜','180일'],['상품 시나리오','1,800개'],['전체 합성 에이전트 후보','전략별 54,623명'],['Bandit 학습 사용자','32,620명(시간순 앞 60%)'],['공통 평가 사용자','22,003명(시간순 뒤 40%)'],['평가 가격 노출','전략별 19,189~20,941건'],['재현성','seed 42'],['저장 결과','JSON, CSV, SQLite']])

p('2. 합성 사용자 구매 행동 모델','Heading 1'); p('2.1 사용자 도착과 시장 수요','Heading 2')
p('날짜 d의 수요 점수 D는 다음과 같이 생성하고 [0.05, 1.0] 범위로 제한한다.')
p('D = clip(0.48 + 0.18·sin((day_of_year−170)/365·2π) + 0.16·I(주말) + εD, 0.05, 1.0)',bold=True)
p('여기서 εD ~ Normal(0, 0.14²)이다. 상품별 사용자 도착 수 N은 재고 Q에 따라 N ~ Poisson(Q·(0.55 + 1.15D))로 생성한다. 따라서 수요가 높거나 재고가 많을수록 더 많은 사용자 에이전트가 유입된다.')
p('2.2 페르소나 계수','Heading 2')
table(['페르소나','지불의사 배수 α','가격 민감도 β','해석'],[['출장형','1.18','0.65','높은 지불의사, 낮은 가격 민감도'],['가족형','1.05','0.85','평균 이상 지불의사'],['가성비형','0.82','1.45','낮은 지불의사, 높은 민감도'],['충동형','0.95','1.05','중간 수준'],['재방문형','1.08','0.75','비교적 높은 지불의사']])
p('현재 페르소나는 동일 확률로 추출한다. 실제 사용자 비중이 확보되면 운영 데이터의 구성비로 교체해야 한다.')
p('2.3 지불의사와 구매확률','Heading 2')
p('사용자 i의 지불의사금액 WTP는 상품 정상가 P0, 수요 D, 페르소나 배수 αi 및 개인차 노이즈 εi로 계산한다.')
p('WTPᵢ = P₀·αᵢ·(0.72 + 0.48D + εᵢ),   εᵢ ~ Normal(0, 0.09²)',bold=True)
p('제안가격 P에 대한 구매 logit과 구매확률은 다음과 같다. 야외 상품은 날씨 위험 W∈[0,1]에 따라 구매확률이 감소한다.')
p('zᵢ = 7·((WTPᵢ−P)/P₀)/βᵢ + 1.2·(D−0.5) − 1.1·W·I(야외상품)',bold=True)
p('Pr(구매ᵢ=1) = σ(clip(zᵢ,−20,20)) = 1/(1+e^(−zᵢ))',bold=True)
p('마지막으로 uᵢ ~ Uniform(0,1)을 추출하고 uᵢ < Pr(구매)일 때 구매로 기록한다. 즉 같은 페르소나와 가격이라도 개인차 Gaussian 노이즈와 Bernoulli 추출 때문에 결과가 달라진다. 현재 모델에는 취소·재구매·경쟁 상품 이동은 포함하지 않았다.')

p('3. 가격 정책 및 Bandit 학습','Heading 1'); p('3.1 기존 규칙','Heading 2')
p('기존 방식은 수요, 잔여 재고, 판매 속도, 경과 시간, 날씨, 주말 및 피크 시간에 사람이 정한 가중치를 적용한다. 동일 조건에서는 동일 가격이 나오며 구매 결과가 다음 가격 결정식을 변경하지 않는다. 엔진이 반환하는 PROFIT, BALANCED, SELL_THROUGH 가격안을 각 실험군에서 선택했다.')
p('3.2 Contextual Bandit','Heading 2')
p('Bandit은 연속형 문맥을 일반화하는 LinUCB 방식이다. 입력에는 수요, 재고율, 경과시간, 원가율, 정상가~최저가 허용폭, 날씨 위험, 주말, 야외 상품 여부, 최근 판매 속도, 학습 구간에서 관찰한 페르소나 구성비, arm별 누적 구매율, 후보 할인율과 후보 마진을 사용한다. 개인별 가격 차등은 수행하지 않는다. 행동은 각 상품의 정상가~최저가 구간 내 상대 위치 {0%, 10%, 20%, 35%, 50%, 70%, 100%}이며 실제 가격은 상품마다 동적으로 달라진다. LinUCB의 불확실성 보너스로 학습 구간을 탐색하고 평가 구간에서는 보너스와 업데이트를 모두 중단한다.')
table(['목표','Bandit 보상','최소 공헌마진'],[['수익 우선','구매×공헌이익 100%','정상가의 20%'],['균형','구매×(공헌이익 55% + 매출 35% + 판매 성공 10%)','정상가의 15%'],['재고 처리 우선','구매×(공헌이익 40% + 매출 35% + 판매 성공 25%)','정상가의 12%']])
p('합성 원가는 최저 판매가×Uniform(0.30,0.50)로 생성했다. Bandit은 시간순 앞 60%의 합성 구매 결과로 학습한 후 정책을 동결하고, 나머지 40%에서 기존 규칙과 비교했다. 실사용자 데이터로 사전 학습한 모델은 아니다.')

p('4. 실험 결과','Heading 1'); p('4.1 전체 성과','Heading 2')
rows=[]
for key,label in [('fixed','고정가 기준선'),('rule_profit','기존 규칙·수익'),('rule_balanced','기존 규칙·균형'),('rule_sell_through','기존 규칙·재고'),('bandit_profit','Bandit·수익'),('bandit_balanced','Bandit·균형'),('bandit_sell_through','Bandit·재고')]:
    x=DATA[key]; rows.append([label,f"{x['revenue']/1e8:.2f}억",f"{x['gross_profit']/1e8:.2f}억",f"{x['conversion_rate']*100:.1f}%",f"{x['sell_through_rate']*100:.1f}%",f"{x['avg_price']:,.0f}원"])
table(['실험군','매출','매출총이익','전환율','소진율','평균 판매가'],rows)
p('4.2 당일 미판매 손실 반영','Heading 2')
p('당일 판매되지 않은 재고는 마감 시 가치가 0이 되는 소멸성 상품으로 가정한다. 미판매 손실은 잔여 수량×단위원가이며, 폐기 반영 순이익은 매출총이익−미판매 손실로 계산한다. 회수 또는 이월 가능한 상품에는 잔존가치를 별도로 반영해야 한다.')
waste_rows=[]
for key,label in [('fixed','고정가 기준선'),('rule_profit','기존 규칙·수익'),('rule_balanced','기존 규칙·균형'),('rule_sell_through','기존 규칙·재고'),('bandit_profit','Bandit·수익'),('bandit_balanced','Bandit·균형'),('bandit_sell_through','Bandit·재고')]:
    x=DATA[key]; waste_rows.append([label,f"{x['unsold_units']:,}개",f"{x['unsold_loss']/1e8:.2f}억",f"{x['net_profit_after_waste']/1e8:.2f}억"])
table(['실험군','미판매 수량','미판매 손실','폐기 반영 순이익'],waste_rows)
picture(charts[0],'그림 1. 고정가 기준선 대비 대표 정책의 매출총이익')
picture(charts[1],'그림 2. 당일 미판매 손실을 반영한 순이익')
picture(charts[2],'그림 3. 목표별 매출총이익 비교')
picture(charts[3],'그림 4. 목표별 매출 비교')
picture(charts[4],'그림 5. 목표별 재고 소진율 비교')
p('4.3 결과 해석','Heading 2')
for s in ['고정가 기준선은 매출 7.57억 원, 매출총이익 5.74억 원, 재고 소진율 56.6%다.','수익 우선 Bandit은 매출 8.56억 원, 매출총이익 6.17억 원으로 전체 실험군 중 두 지표가 모두 최고다.','수익 우선 Bandit은 기존 수익 규칙보다 이익 약 2.7%, 고정가보다 약 7.6% 높고 소진율도 74.3%를 기록했다.','균형 Bandit은 기존 균형 규칙보다 매출 2.3%, 소진율 12.3%p 높지만 이익은 약 3.4% 낮다.','재고 처리 Bandit은 기존 재고 규칙보다 매출 0.8%, 소진율 4.2%p 높으며 이익 차이는 약 −1.4%로 축소됐다.']:
    p(s,'List Bullet')
p('폐기 반영 순이익은 Bandit 수익 우선이 5.35억 원으로 가장 높다. 고정가 4.36억 원 대비 약 22.7%, 기존 수익 규칙 4.84억 원 대비 약 10.5% 높은 결과다.','List Bullet')

p('5. 한계 및 다음 실험','Heading 1')
for s in ['구매확률과 페르소나 계수는 합성 가정이며 실제 전환 데이터로 calibration해야 한다.','현재 시간순 60/40 분리는 적용했으며, 다음에는 seed 10개 이상으로 평균·표준편차·신뢰구간을 보고해야 한다.','기존 규칙과 Bandit에 동일한 수요 신호를 제공해 정보 비대칭을 제거해야 한다.','취소, 경쟁상품 이동, 반복 방문, 채널 수수료 및 실제 공헌원가를 추가해야 한다.','서버 부하 테스트는 배포 서버 URL과 허가 범위 확정 후 별도로 수행한다.']:
    p(s,'List Bullet')

d.save(OUT); print(OUT)
