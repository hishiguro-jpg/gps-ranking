"""在庫発注計算ツール（画面版）

受発注生データCSVをアップロードし、画面上でコスト・在庫情報を入力するだけで
発注量・在庫消化見込み・損益分岐点のレポートを作成できるツール。

起動:
    streamlit run inventory_tool/streamlit_app.py
"""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from raw_data_import import build_sales_history, read_csv_any_encoding  # noqa: E402
from estimate_selling_price import estimate_per_unit_revenue  # noqa: E402
from order_calculator import (  # noqa: E402
    analyze_product,
    build_chart,
    fmt_date,
    fmt_days,
)

st.set_page_config(page_title="在庫発注計算ツール", page_icon="📦", layout="wide")

st.title("📦 在庫発注計算ツール")
st.caption("受発注生データから、需要傾向・発注量・在庫消化見込み・損益分岐点を算出します。")

with st.sidebar:
    st.header("① 集計条件")
    date_key = st.radio(
        "消化ペースの基準日",
        options=["shukka", "kifu"],
        format_func=lambda k: "出荷日（在庫消費の実態に近い・推奨）" if k == "shukka" else "寄附日（本来の需要タイミング）",
    )
    window_days = st.number_input("消化ペースの算出に使う直近日数", min_value=7, value=60, step=7)
    target_days = st.number_input("リードタイムに加えて確保する日数", min_value=0, value=30, step=5)
    service_level = st.selectbox(
        "サービス率（欠品許容度）", options=[0.80, 0.85, 0.90, 0.95, 0.975, 0.99], index=3,
        format_func=lambda v: f"{v*100:.1f}%",
    )

    st.header("② コスト条件")
    coordination_rate_pct = st.number_input(
        "コーディネート報酬率（寄附金額に対する%）", min_value=0.0, max_value=100.0, value=13.0, step=0.5,
    )
    pallets_per_truck = st.number_input("トラック1台に積めるパレット数", min_value=1, value=16, step=1)
    truck_cost = st.number_input("トラック1台あたりの搬入費用(円)", min_value=0.0, value=40000.0, step=1000.0)
    holding_cost_per_unit = st.number_input("在庫1個あたりの保管費用(円)", min_value=0.0, value=1.0, step=0.5)

uploaded = st.file_uploader("受発注生データCSV（1行=1件の受発注明細）をアップロード", type=["csv"])

if uploaded is None:
    st.info("受発注管理システムからエクスポートしたCSVをアップロードしてください。文字コードは自動判定します。")
    st.stop()

raw_df = read_csv_any_encoding(uploaded)

try:
    daily_df, stats = build_sales_history(raw_df, date_key)
except ValueError as e:
    st.error(str(e))
    st.stop()

st.success(
    f"入力{stats['input_count']}件中、対象商品として{stats['input_count'] - stats['unmatched_count']}件を集計しました"
    f"（対象外: {stats['unmatched_count']}件）"
)

products = sorted(daily_df["product"].unique())
if not products:
    st.warning(
        "対象商品が見つかりませんでした。raw_data_import.py の classify_product() に"
        "商品名の判定ルールが定義されているか確認してください。"
    )
    st.stop()

revenue_estimate = estimate_per_unit_revenue(raw_df, coordination_rate_pct / 100)

st.subheader("③ 商品ごとのコスト・在庫情報を入力")
st.caption("「推定コーディネート報酬」は寄附金額から自動計算した参考値です。売価は自由に編集できます。")

default_rows = []
for p in products:
    estimated_fee = float(revenue_estimate.loc[p, "推定売価_1個あたり"]) if p in revenue_estimate.index else 0.0
    default_rows.append({
        "product": p,
        "unit_cost": 0.0,
        "推定コーディネート報酬(参考)": estimated_fee,
        "selling_price": estimated_fee,
        "current_stock": 0,
        "on_order_qty": 0,
        "lead_time_days": 21,
        "units_per_pallet": 0,
        "fixed_cost_extra": 0.0,
    })
default_df = pd.DataFrame(default_rows)

edited_df = st.data_editor(
    default_df,
    column_config={
        "product": st.column_config.TextColumn("商品名", disabled=True),
        "unit_cost": st.column_config.NumberColumn("原価(円/個)", min_value=0.0, step=1.0),
        "推定コーディネート報酬(参考)": st.column_config.NumberColumn("推定報酬(参考,円/個)", disabled=True),
        "selling_price": st.column_config.NumberColumn("売価(円/個)", min_value=0.0, step=1.0),
        "current_stock": st.column_config.NumberColumn("現在庫(個)", min_value=0, step=1),
        "on_order_qty": st.column_config.NumberColumn("発注済み未入荷(個)", min_value=0, step=1),
        "lead_time_days": st.column_config.NumberColumn("リードタイム(日)", min_value=0, step=1),
        "units_per_pallet": st.column_config.NumberColumn("1パレットあたり個数(0=非対応)", min_value=0, step=1),
        "fixed_cost_extra": st.column_config.NumberColumn("その他固定費(円)", min_value=0.0, step=1000.0),
    },
    hide_index=True,
    use_container_width=True,
)

run = st.button("📊 分析を実行", type="primary")

if not run:
    st.stop()

cost_df = edited_df.set_index("product").rename(columns={"fixed_cost_extra": "fixed_cost"})

results = []
for product in products:
    cost_row = cost_df.loc[product] if product in cost_df.index else None
    result = analyze_product(
        product=product,
        sales_df=daily_df,
        cost_row=cost_row,
        window_days=int(window_days),
        service_level=service_level,
        target_days=int(target_days),
        today=pd.Timestamp.now(),
        pallets_per_truck=int(pallets_per_truck),
        truck_cost=truck_cost,
        holding_cost_per_unit=holding_cost_per_unit,
    )
    results.append(result)

st.header("④ 分析結果")

for r in results:
    st.subheader(r["product"])
    col1, col2, col3 = st.columns(3)
    col1.metric("直近平均消化ペース", f"{r['avg_daily']:.2f} 個/日")
    if r["trend_ratio"] is not None:
        col2.metric("全期間平均比", f"{r['trend_ratio']*100:.0f}%")
    if r.get("has_cost_data"):
        col3.metric("推奨発注量", f"{r['recommended_order_qty']:.0f} 個")

    fig = build_chart(r)
    if fig is not None:
        st.pyplot(fig)

    if not r.get("has_cost_data"):
        st.info("コスト情報が未入力のため、発注計画・損益分岐点は未算出です。")
        continue

    with st.container(border=True):
        st.markdown("**発注計画**")
        st.write(f"- リードタイム: {r['lead_time_days']:.0f}日 / 安全在庫: {r['safety_stock']:.1f}個")
        st.write(f"- 発注点(ROP): {r['reorder_point']:.1f}個 / 現在庫: {r['current_stock']:.0f}個")
        if r["units_per_pallet"] > 0 and r["recommended_order_qty"] > 0:
            st.write(
                f"- **推奨発注量: {r['recommended_order_qty']:.0f}個**"
                f"（{r['pallets_needed']:.0f}パレット分）"
                + (f" / トラック{r['trucks_needed']:.0f}台分" if r["trucks_needed"] > 1 else "")
            )
        else:
            st.write(f"- **推奨発注量: {r['recommended_order_qty']:.0f}個**")
        st.write(
            f"- 現在庫のみでの消化見込み: {fmt_days(r['days_to_stockout'])}（{fmt_date(r['stockout_date'])}頃）"
        )
        st.write(
            "- 発注後の合計在庫での消化見込み: "
            f"{fmt_days(r['days_to_deplete_after_order'])}（{fmt_date(r['depletion_date_after_order'])}頃）"
        )

        st.markdown("**損益分岐点**")
        st.write(
            f"- 原価: {r['unit_cost']:.0f}円 / 売価: {r['selling_price']:.0f}円 / "
            f"1個あたり粗利: {r['margin']:.0f}円 / 固定費(合計): {r['fixed_cost']:.0f}円"
        )
        if r["breakeven_units"] is not None:
            st.write(
                f"- 損益分岐に必要な販売数量: {r['breakeven_units']:.0f}個 / "
                f"到達見込み: {fmt_days(r['days_to_breakeven'])}（{fmt_date(r['breakeven_date'])}頃）"
            )
        else:
            st.warning("粗利がゼロ以下のため損益分岐点を算出できません（原価・売価をご確認ください）")

        st.markdown("**製造費回収（資金繰りの目安）**")
        st.write(
            f"- 対象数量(現在庫+推奨発注量): {r['units_for_cost_recovery']:.0f}個 / "
            f"製造費総額: {r['manufacturing_cost_outlay']:.0f}円"
        )
        st.write(
            f"- 回収見込み: {fmt_days(r['days_to_recover_cost'])}（{fmt_date(r['cost_recovery_date'])}頃）"
        )
        st.caption(
            "原価は売れるたびに全額戻ってくるため、この日を過ぎた分の売上はすべて実質利益という考え方です。"
            "固定費(トラック代・保管費等)の回収とは別の指標です。"
        )

    st.divider()
