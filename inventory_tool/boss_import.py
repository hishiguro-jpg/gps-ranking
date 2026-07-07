#!/usr/bin/env python3
"""BOSS-OMSの受発注生データを order_calculator.py 用の sales_history.csv に変換する

生データ(BOSSからエクスポートしたCSV)は1行=1件(1個)の受発注記録という前提で、
商品名の表記ゆれ(【寄付額変更前】等のラベルや①②③、ポータル名の付記)を吸収し、
対象商品グループごとに指定した日付列で日次集計する。

対応済みの商品グループ:
    シリカちゃん天然水(40本) / シリカちゃん天然水(24本)
    ※ 別商品を対象に追加したい場合は classify_product() にルールを追加する

使い方:
    python boss_import.py --input raw_boss_export.csv --date-column kifu \
        --output sales_history_kifubi.csv

    python boss_import.py --input raw_boss_export.csv --date-column shukka \
        --output sales_history_shukkabi.csv
"""

import argparse

import pandas as pd

CSV_ENCODINGS = ["utf-8-sig", "utf-8", "cp932", "shift_jis"]

DATE_COLUMNS = {
    "kifu": "寄附日(寄附効果発生日)",
    "shukka": "出荷日",
}


def classify_product(raw_name):
    name = str(raw_name)
    if "シリカちゃん" not in name or "天然水" not in name:
        return None
    if "４０" in name or "40" in name:
        return "シリカちゃん天然水(40本)"
    if "２４" in name or "24" in name:
        return "シリカちゃん天然水(24本)"
    return None


def read_csv_any_encoding(path):
    last_error = None
    for enc in CSV_ENCODINGS:
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception as e:
            last_error = e
            continue
    raise ValueError(f"文字コードを認識できませんでした: {path} ({last_error})")


def main():
    parser = argparse.ArgumentParser(description="BOSS生データをsales_history.csv形式に変換する")
    parser.add_argument("--input", required=True, help="BOSSからエクスポートした生データCSV")
    parser.add_argument(
        "--date-column", choices=["kifu", "shukka"], required=True,
        help="集計基準の日付: kifu=寄附日(需要の発生タイミング) / shukka=出荷日(実際の在庫消費タイミング)",
    )
    parser.add_argument("--output", required=True, help="出力するsales_history.csvのパス")
    args = parser.parse_args()

    df = read_csv_any_encoding(args.input)
    df.columns = [c.strip() for c in df.columns]

    date_col = DATE_COLUMNS[args.date_column]
    if date_col not in df.columns:
        raise SystemExit(f"列が見つかりません: {date_col}（列一覧: {list(df.columns)}）")

    df["product"] = df["商品名"].map(classify_product)
    matched = df[df["product"].notna()].copy()
    unmatched_count = len(df) - len(matched)

    matched["date"] = pd.to_datetime(
        matched[date_col].astype(str).str.split(" ").str[0], errors="coerce"
    )
    before_dropna = len(matched)
    matched = matched.dropna(subset=["date"])
    invalid_date_count = before_dropna - len(matched)

    daily = matched.groupby(["date", "product"]).size().reset_index(name="quantity")
    daily = daily.sort_values(["product", "date"])
    daily["date"] = daily["date"].dt.strftime("%Y-%m-%d")

    daily.to_csv(args.output, index=False, encoding="utf-8-sig")

    print(f"入力件数: {len(df)}")
    print(f"対象商品に一致せず除外: {unmatched_count}件")
    if invalid_date_count:
        print(f"日付が空/不正のため除外: {invalid_date_count}件")
    print("\n分類結果(件数内訳):")
    print(matched["product"].value_counts().to_string())
    print(f"\n[出力] {args.output} ({len(daily)}行)")


if __name__ == "__main__":
    main()
