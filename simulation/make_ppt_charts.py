from __future__ import annotations
import argparse, html, json
from pathlib import Path

BLUE, ORANGE = "#2563EB", "#F97316"
OBJECTIVES = [("profit", "수익 우선"), ("balanced", "균형"), ("sell_through", "재고 처리 우선")]

def t(x,y,s,size=28,weight=400,anchor="start",color="#0F172A"):
    return f'<text x="{x}" y="{y}" font-family="Malgun Gothic,Arial,sans-serif" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" fill="{color}">{html.escape(str(s))}</text>'

def chart(path, data, metric, title, subtitle, formatter):
    vals=[data[f"{kind}_{goal}"][metric] for goal,_ in OBJECTIVES for kind in ("rule","bandit")]; top=max(vals)
    body=[t(80,85,title,44,700),t(80,130,subtitle,24,color="#475569")]
    for i,(goal,label) in enumerate(OBJECTIVES):
        base=230+i*465; body.append(t(base+105,750,label,25,700,"middle"))
        for j,(kind,color,name) in enumerate((("rule",BLUE,"기존 규칙"),("bandit",ORANGE,"Bandit"))):
            value=data[f"{kind}_{goal}"][metric]; x=base+j*125; height=value/top*470; y=690-height
            body += [f'<rect x="{x}" y="{y}" width="95" height="{height}" rx="5" fill="{color}"/>',t(x+47,y-16,formatter(value),20,700,"middle")]
    body += [f'<rect x="1190" y="175" width="25" height="17" fill="{BLUE}"/>',t(1225,191,"기존 규칙",19),f'<rect x="1360" y="175" width="25" height="17" fill="{ORANGE}"/>',t(1395,191,"Bandit",19)]
    body.append(t(1530,855,"전략별 합성 에이전트 54,623명 · 180일 · 1,800개 상품 · seed 42",18,anchor="end",color="#64748B"))
    svg='<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900"><rect width="1600" height="900" fill="white"/>'+''.join(body)+'</svg>'
    path.write_text(svg,encoding="utf-8")

def main():
    p=argparse.ArgumentParser(); p.add_argument("run_dir",type=Path); p.add_argument("--output",type=Path,default=Path("simulation/ppt-charts-large")); a=p.parse_args(); a.output.mkdir(parents=True,exist_ok=True)
    data=json.loads((a.run_dir/"summary.json").read_text(encoding="utf-8"))["strategies"]
    chart(a.output/"01_gross_profit.svg",data,"gross_profit","목표별 매출총이익 비교","수익 우선에서는 기존 규칙이 가장 높은 이익을 기록",lambda v:f"{v/1e8:.1f}억원")
    chart(a.output/"02_revenue.svg",data,"revenue","목표별 매출 비교","Bandit 수익 우선이 전체 실험군 중 최고 매출",lambda v:f"{v/1e8:.1f}억원")
    chart(a.output/"03_sell_through.svg",data,"sell_through_rate","목표별 재고 소진율 비교","재고 처리 우선 Bandit이 가장 높은 소진율을 기록",lambda v:f"{v*100:.1f}%")
    print(a.output.resolve())

if __name__=="__main__": main()
