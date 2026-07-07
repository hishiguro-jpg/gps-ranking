#!/usr/bin/env python3
"""受発注生データを order_calculator.py 用の sales_history.csv に変換する

生データ(受発注管理システムからエクスポートしたCSV)は1行=1件(1個)の
受発注記録という前提で、商品名の表記ゆれ(【寄付額変更前】等のラベルや
①②③、ポータル名の付記)を吸収し、対象商品グループごとに指定した
日付列で日次集計する。

対応済みの商品グループ:
    シリカちゃん天然水(40本) / シリカちゃん天然水(24本)
    ※ 別商品を対象に追加したい場合は classify_product() にルールを追加する

使い方:
    python raw_data_import.py --input raw_export.csv --date-column kifu \
        --output sales_history_kifubi.csv

    python raw_data_import.py --input raw_export.csv --date-column shukka \
        --output sales_history_shukkabi.csv
"""

import argparse
import io

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


def read_csv_any_encoding(source):
    """ファイルパスまたはファイルライクオブジェクト(Streamlitのアップロードファイル等)を読む

    エンコーディングを1つずつ試すため、都度先頭から読み直せるようバイト列に
    一度だけ読み込んでからパースする(ファイルライクオブジェクトはstream位置が
    残るため、単純に複数回pd.read_csv(source, ...)すると2回目以降が壊れる)。
    """
    if hasattr(source, "read"):
        data = source.read()
    else:
        with open(source, "rb") as f:
            data = f.read()

    last_error = None
    for enc in CSV_ENCODINGS:
        try:
            return pd.read_csv(io.BytesIO(data), encoding=enc)
        except Exception as e:
            last_error = e
            continue
    raise ValueError(f"文字コードを認識できませんでした ({last_error})")


def build_sales_history(raw_df, date_column_key):
    """生データDataFrameを商品×日付で集計したsales_history形式のDataFrameに変換する

    戻り値: (daily_df, stats) のタプル。
    daily_df は date, product, quantity 列を持つ(order_calculator.pyの入力形式)。
    stats は 入力件数/対象外件数/日付不正件数/商品別件数 を含む辞書。
    """
    df = raw_df.copy()
    df.columns = [c.strip() for c in df.columns]

    date_col = DATE_COLUMNS[date_column_key]
    if date_col not in df.columns:
        raise ValueError(f"列が見つかりません: {date_col}（列一覧: {list(df.columns)}）")

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
    daily["date"] = pd.to_datetime(daily["date"])

    stats = {
        "input_count": len(df),
        "unmatched_count": unmatched_count,
        "invalid_date_count": invalid_date_count,
        "product_counts": matched["product"].value_counts(),
    }
    return daily, stats


def main():
    parser = argparse.ArgumentParser(description="受発注生データをsales_history.csv形式に変換する")
    parser.add_argument("--input", required=True, help="受発注管理システムからエクスポートした生データCSV")
    parser.add_argument(
        "--date-column", choices=["kifu", "shukka"], required=True,
        help="集計基準の日付: kifu=寄附日(需要の発生タイミング) / shukka=出荷日(実際の在庫消費タイミング)",
    )
    parser.add_argument("--output", required=True, help="出力するsales_history.csvのパス")
    args = parser.parse_args()

    raw_df = read_csv_any_encoding(args.input)
    daily, stats = build_sales_history(raw_df, args.date_column)
    daily_out = daily.copy()
    daily_out["date"] = daily_out["date"].dt.strftime("%Y-%m-%d")
    daily_out.to_csv(args.output, index=False, encoding="utf-8-sig")

    print(f"入力件数: {stats['input_count']}")
    print(f"対象商品に一致せず除外: {stats['unmatched_count']}件")
    if stats["invalid_date_count"]:
        print(f"日付が空/不正のため除外: {stats['invalid_date_count']}件")
    print("\n分類結果(件数内訳):")
    print(stats["product_counts"].to_string())
    print(f"\n[出力] {args.output} ({len(daily_out)}行)")


if __name__ == "__main__":
    main()
