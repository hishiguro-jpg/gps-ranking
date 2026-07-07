#!/usr/bin/env python3
"""受発注生データから、コーディネート報酬率をもとに1個あたりの売価(報酬)を推定する

ふるさと納税の返礼品コーディネート報酬は「寄附金額 × 報酬率」で決まる想定。
ただし3ヶ月/6ヶ月/12ヶ月の定期便は、同一の寄附番号に対して出荷のたびに
明細行が作られる一方、寄附金額は毎回同じ(合計)金額が記録されているため、
単純に「行ごとに寄附金額×報酬率」を合計すると定期便の回数分だけ
過大計上してしまう。

そのため、同一寄附番号の出荷件数(=定期便の回数)で寄附金額を按分してから
報酬率を掛けることで、1個(1回の出荷)あたりの報酬を推定する。

使い方:
    python estimate_selling_price.py --input raw_export.csv --coordination-rate 0.13 \
        --output selling_price_estimate.csv
"""

import argparse

import pandas as pd

from raw_data_import import classify_product, read_csv_any_encoding


def main():
    parser = argparse.ArgumentParser(description="コーディネート報酬率から1個あたりの売価を推定する")
    parser.add_argument("--input", required=True, help="受発注生データCSV")
    parser.add_argument(
        "--coordination-rate", type=float, default=0.13,
        help="寄附金額に対するコーディネート報酬率(比率が変わったら指定し直す)",
    )
    parser.add_argument("--output", default=None, help="推定結果CSVの保存先")
    args = parser.parse_args()

    df = read_csv_any_encoding(args.input)
    df.columns = [c.strip() for c in df.columns]
    df["product"] = df["商品名"].map(classify_product)
    matched = df[df["product"].notna()].copy()

    matched["寄附金額"] = pd.to_numeric(matched["寄附金額"], errors="coerce").fillna(0)
    installment_counts = matched.groupby("寄附番号")["寄附番号"].transform("count")
    matched["per_unit_kifu_gaku"] = matched["寄附金額"] / installment_counts
    matched["per_unit_revenue"] = matched["per_unit_kifu_gaku"] * args.coordination_rate

    summary = matched.groupby("product").agg(
        件数=("per_unit_revenue", "size"),
        平均寄附金額_按分後=("per_unit_kifu_gaku", "mean"),
        推定売価_1個あたり=("per_unit_revenue", "mean"),
    ).round(0)

    print(f"コーディネート報酬率: {args.coordination_rate*100:.1f}%")
    print(summary.to_string())

    if args.output:
        summary.to_csv(args.output, encoding="utf-8-sig")
        print(f"\n[出力] {args.output}")


if __name__ == "__main__":
    main()
