#!/usr/bin/env python3
"""在庫発注計算ツール

過去の販売実績と原価・売価マスターから、以下を算出して
社内報告用のMarkdownレポートと商品ごとのグラフを出力する。

- 平均消化ペース(需要)とその傾向
- 安全在庫・発注点(ROP)
- 推奨発注量
- 在庫消化にかかる見込み期間
- 損益分岐点(必要販売数量・到達見込み時期)

使い方:
    python order_calculator.py --sales sales_history.csv --costs cost_master.csv

入力CSVの形式は templates/ 以下のサンプルを参照。
実データ(*.csv)はリポジトリの.gitignoreで除外されるため、
このフォルダに置いてもGit管理には含まれない。
"""

import argparse
import math
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

REPO_ROOT = Path(__file__).resolve().parent.parent


def _find_jp_font():
    bundled = REPO_ROOT / "fonts" / "NotoSansJP-Regular.ttf"
    if bundled.exists():
        return str(bundled)
    candidates = [
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None


JP_FONT = _find_jp_font()
if JP_FONT:
    try:
        fm.fontManager.addfont(JP_FONT)
        plt.rcParams["font.family"] = fm.FontProperties(fname=JP_FONT).get_name()
    except Exception:
        pass

# サービス率(欠品許容度)ごとの安全係数(z値)
Z_SCORE_TABLE = {
    0.80: 0.84,
    0.85: 1.04,
    0.90: 1.28,
    0.95: 1.65,
    0.975: 1.96,
    0.99: 2.33,
}

CSV_ENCODINGS = ["utf-8-sig", "utf-8", "shift_jis", "cp932"]


def read_csv_any_encoding(path):
    last_error = None
    for enc in CSV_ENCODINGS:
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception as e:
            last_error = e
            continue
    raise ValueError(f"文字コードを認識できませんでした: {path} ({last_error})")


def nearest_z_score(service_level):
    closest = min(Z_SCORE_TABLE, key=lambda level: abs(level - service_level))
    return Z_SCORE_TABLE[closest]


def load_sales_history(path):
    df = read_csv_any_encoding(path)
    df.columns = [c.strip() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    df["product"] = df["product"].astype(str).str.strip()
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
    return df


def load_cost_master(path):
    df = read_csv_any_encoding(path)
    df.columns = [c.strip() for c in df.columns]
    df["product"] = df["product"].astype(str).str.strip()
    df = df.set_index("product")
    for col in ["unit_cost", "selling_price", "fixed_cost", "lead_time_days", "current_stock", "on_order_qty"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df


def daily_series(sales_df, product, window_days):
    product_df = sales_df[sales_df["product"] == product]
    if product_df.empty:
        return pd.Series(dtype=float)
    daily = product_df.groupby("date")["quantity"].sum()
    full_range = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    daily = daily.reindex(full_range, fill_value=0)
    if window_days and len(daily) > window_days:
        return daily.iloc[-window_days:]
    return daily


def analyze_product(product, sales_df, cost_row, window_days, service_level, target_days, today):
    series_all = daily_series(sales_df, product, window_days=None)
    series_window = daily_series(sales_df, product, window_days=window_days)

    avg_daily = float(series_window.mean()) if len(series_window) else 0.0
    std_daily = float(series_window.std(ddof=0)) if len(series_window) else 0.0
    avg_daily_overall = float(series_all.mean()) if len(series_all) else 0.0
    trend_ratio = (avg_daily / avg_daily_overall) if avg_daily_overall > 0 else None

    if cost_row is None:
        return {
            "product": product,
            "series_all": series_all,
            "avg_daily": avg_daily,
            "std_daily": std_daily,
            "avg_daily_overall": avg_daily_overall,
            "trend_ratio": trend_ratio,
            "has_cost_data": False,
        }

    lead_time_days = float(cost_row["lead_time_days"])
    current_stock = float(cost_row["current_stock"])
    on_order_qty = float(cost_row["on_order_qty"])
    unit_cost = float(cost_row["unit_cost"])
    selling_price = float(cost_row["selling_price"])
    fixed_cost = float(cost_row["fixed_cost"])

    z = nearest_z_score(service_level)
    safety_stock = z * std_daily * math.sqrt(max(lead_time_days, 0))
    reorder_point = avg_daily * lead_time_days + safety_stock

    order_up_to = avg_daily * (lead_time_days + target_days) + safety_stock
    recommended_order_qty = max(0.0, order_up_to - current_stock - on_order_qty)

    days_to_stockout = current_stock / avg_daily if avg_daily > 0 else float("inf")
    stockout_date = today + timedelta(days=days_to_stockout) if math.isfinite(days_to_stockout) else None

    total_after_order = current_stock + recommended_order_qty
    days_to_deplete_after_order = total_after_order / avg_daily if avg_daily > 0 else float("inf")
    depletion_date_after_order = (
        today + timedelta(days=days_to_deplete_after_order) if math.isfinite(days_to_deplete_after_order) else None
    )

    margin = selling_price - unit_cost
    if margin > 0:
        breakeven_units = fixed_cost / margin
        days_to_breakeven = breakeven_units / avg_daily if avg_daily > 0 else float("inf")
        breakeven_date = today + timedelta(days=days_to_breakeven) if math.isfinite(days_to_breakeven) else None
    else:
        breakeven_units = None
        days_to_breakeven = None
        breakeven_date = None

    return {
        "product": product,
        "series_all": series_all,
        "avg_daily": avg_daily,
        "std_daily": std_daily,
        "avg_daily_overall": avg_daily_overall,
        "trend_ratio": trend_ratio,
        "has_cost_data": True,
        "safety_stock": safety_stock,
        "reorder_point": reorder_point,
        "recommended_order_qty": recommended_order_qty,
        "current_stock": current_stock,
        "on_order_qty": on_order_qty,
        "lead_time_days": lead_time_days,
        "days_to_stockout": days_to_stockout,
        "stockout_date": stockout_date,
        "days_to_deplete_after_order": days_to_deplete_after_order,
        "depletion_date_after_order": depletion_date_after_order,
        "unit_cost": unit_cost,
        "selling_price": selling_price,
        "fixed_cost": fixed_cost,
        "margin": margin,
        "breakeven_units": breakeven_units,
        "days_to_breakeven": days_to_breakeven,
        "breakeven_date": breakeven_date,
    }


def fmt_days(value):
    if value is None or not math.isfinite(value):
        return "算出不可(消化ペースがゼロ)"
    return f"約{value:.0f}日"


def fmt_date(value):
    if value is None:
        return "算出不可"
    return value.strftime("%Y-%m-%d")


def render_report(results, service_level, target_days, window_days):
    lines = []
    lines.append("# 在庫発注分析レポート")
    lines.append("")
    lines.append(f"- 作成日: {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(f"- 消化ペースの算出に使った直近日数: {window_days}日")
    lines.append(f"- サービス率(欠品許容度): {service_level*100:.0f}%")
    lines.append(f"- 発注時に確保する目標日数(リードタイム後さらに何日分か): {target_days}日")
    lines.append("")

    for r in results:
        lines.append(f"## {r['product']}")
        lines.append("")
        lines.append("### 需要傾向")
        lines.append(f"- 直近平均消化ペース: {r['avg_daily']:.2f} 個/日（週あたり約{r['avg_daily']*7:.1f}個）")
        lines.append(f"- ばらつき(標準偏差): {r['std_daily']:.2f} 個/日")
        if r["trend_ratio"] is not None:
            if r["trend_ratio"] > 1.1:
                trend_desc = "上昇傾向"
            elif r["trend_ratio"] < 0.9:
                trend_desc = "下降傾向"
            else:
                trend_desc = "横ばい"
            lines.append(f"- 全期間平均との比較: 直近ペースは全期間平均の{r['trend_ratio']*100:.0f}%（{trend_desc}）")
        lines.append("")

        if not r["has_cost_data"]:
            lines.append("### 発注計画・損益分岐点")
            lines.append("- 原価・売価・現在庫・リードタイムが未入力のため未算出（コスト情報を入力すると算出されます）")
            lines.append("")
            continue

        lines.append("### 発注計画")
        lines.append(f"- リードタイム: {r['lead_time_days']:.0f}日")
        lines.append(f"- 安全在庫: {r['safety_stock']:.1f} 個")
        lines.append(f"- 発注点(在庫がこの数を下回ったら発注): {r['reorder_point']:.1f} 個")
        lines.append(f"- 現在庫: {r['current_stock']:.0f} 個 / 発注済(未入荷): {r['on_order_qty']:.0f} 個")
        lines.append(f"- **推奨発注量: {r['recommended_order_qty']:.0f} 個**")
        lines.append("")
        lines.append("### 在庫消化の見込み")
        lines.append(f"- 現在庫のみで消化しきるまで: {fmt_days(r['days_to_stockout'])}（{fmt_date(r['stockout_date'])}頃）")
        lines.append(
            "- 推奨量を発注した場合、合計在庫を消化しきるまで: "
            f"{fmt_days(r['days_to_deplete_after_order'])}（{fmt_date(r['depletion_date_after_order'])}頃）"
        )
        lines.append("")
        lines.append("### 損益分岐点")
        lines.append(
            f"- 原価: {r['unit_cost']:.0f}円 / 売価: {r['selling_price']:.0f}円 / 1個あたり粗利: {r['margin']:.0f}円"
        )
        lines.append(f"- 固定費: {r['fixed_cost']:.0f}円")
        if r["breakeven_units"] is not None:
            lines.append(f"- 損益分岐に必要な販売数量: {r['breakeven_units']:.0f} 個")
            lines.append(
                f"- 現在の消化ペースでの到達見込み: {fmt_days(r['days_to_breakeven'])}（{fmt_date(r['breakeven_date'])}頃）"
            )
        else:
            lines.append("- 粗利がゼロ以下のため損益分岐点を算出できません（原価・売価をご確認ください）")
        lines.append("")
    return "\n".join(lines)


def save_chart(result, output_dir):
    series = result["series_all"]
    if series.empty:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(series.index, series.values, label="実績(日次)")
    ax.axhline(result["avg_daily"], color="orange", linestyle="--", label="直近平均ペース")
    ax.set_title(f"{result['product']} 販売推移")
    ax.set_xlabel("日付")
    ax.set_ylabel("数量")
    ax.legend()
    ax.spines[["top", "right"]].set_visible(False)
    fig.autofmt_xdate()
    fig.tight_layout()
    safe_name = "".join(c if c.isalnum() else "_" for c in result["product"])
    path = output_dir / f"{safe_name}.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main():
    parser = argparse.ArgumentParser(description="過去実績から発注量・損益分岐点・在庫消化見込みを算出する")
    parser.add_argument("--sales", required=True, help="販売実績CSV (date, product, quantity)")
    parser.add_argument(
        "--costs", default=None,
        help="原価・売価・在庫等マスターCSV。省略時は需要傾向(消化ペース)のみ算出する",
    )
    parser.add_argument("--service-level", type=float, default=0.95, help="欠品許容度(サービス率) 例: 0.95")
    parser.add_argument("--window-days", type=int, default=60, help="直近何日間を消化ペースの算出に使うか")
    parser.add_argument("--target-days", type=int, default=30, help="リードタイムに加えて何日分の在庫を確保するか")
    parser.add_argument("--output", default=None, help="レポート(Markdown)の保存先。省略時は標準出力のみ")
    parser.add_argument(
        "--chart-dir", default=str(REPO_ROOT / "inventory_tool" / "output"), help="グラフ画像の保存先ディレクトリ"
    )
    args = parser.parse_args()

    sales_df = load_sales_history(args.sales)
    cost_df = load_cost_master(args.costs) if args.costs else None
    today = datetime.now()

    if cost_df is not None:
        products = list(cost_df.iterrows())
    else:
        products = [(p, None) for p in sorted(sales_df["product"].unique())]

    results = []
    for product, cost_row in products:
        result = analyze_product(
            product=product,
            sales_df=sales_df,
            cost_row=cost_row,
            window_days=args.window_days,
            service_level=args.service_level,
            target_days=args.target_days,
            today=today,
        )
        results.append(result)
        chart_path = save_chart(result, Path(args.chart_dir))
        if chart_path:
            print(f"[グラフ保存] {chart_path}")

    report = render_report(results, args.service_level, args.target_days, args.window_days)
    print()
    print(report)

    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"\n[レポート保存] {args.output}")


if __name__ == "__main__":
    main()
