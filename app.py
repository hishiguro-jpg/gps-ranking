import io
import os
import sqlite3
import datetime
from pathlib import Path

import streamlit as st
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

from fpdf import FPDF

# ── フォント初期化（Mac/Linux 両対応） ────────────────────────────────────────
def _find_jp_font():
    candidates = [
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",                    # macOS
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",             # Ubuntu noto-cjk
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
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

st.set_page_config(page_title="GPS計測 順位表", page_icon="⚡", layout="wide")

# ── パスワード認証 ────────────────────────────────────────────────────────────
def _check_password() -> bool:
    """secrets.toml の APP_PASSWORD と照合。一致したらセッション中は通過。"""
    try:
        correct = st.secrets["APP_PASSWORD"]
    except (KeyError, FileNotFoundError):
        return True  # ローカル開発時（secrets未設定）はスキップ

    if st.session_state.get("_auth_ok"):
        return True

    with st.form("login_form"):
        st.markdown("### GPS順位表ジェネレーター")
        pw = st.text_input("パスワードを入力してください", type="password")
        submitted = st.form_submit_button("ログイン", use_container_width=True)

    if submitted:
        if pw == correct:
            st.session_state["_auth_ok"] = True
            st.rerun()
        else:
            st.error("パスワードが違います")
    return False

if not _check_password():
    st.stop()

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;700;900&display=swap');
html, body, [class*="css"] { font-family: 'Noto Sans JP', sans-serif; }
.main-title { font-size:2.4rem;font-weight:900;text-align:center;color:#0d1b2a;margin-bottom:.2rem;letter-spacing:.05em; }
.sub-title  { text-align:center;color:#555;font-size:1rem;margin-bottom:2rem; }
.metric-card { background:linear-gradient(135deg,#0d1b2a 0%,#1b3a5c 100%);color:white;border-radius:12px;padding:16px 20px;text-align:center;margin:4px; }
.metric-card .label { font-size:.75rem;opacity:.75; }
.metric-card .value { font-size:1.8rem;font-weight:900; }
.metric-card .unit  { font-size:.75rem;opacity:.75; }
.section-header { font-size:1.3rem;font-weight:700;color:#0d1b2a;border-left:6px solid #e63946;padding-left:12px;margin:2rem 0 .8rem; }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-title">⚡ GPS 順位表ジェネレーター</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">CSVをアップロードするだけで個人・チーム順位を自動生成</div>', unsafe_allow_html=True)

# ── 定数 ──────────────────────────────────────────────────────────────────────
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}

STATSPORTS_MAP = {
    "name_col": "Name", "team_col": "Team", "number_col": "Number",
    "pos_col": "Position", "dist_col": "Distance", "duration_col": "Duration_TF",
    "spd_av_col": "SPD AV", "spd_mx_col": "SPD MX", "sprint_col": "Sprint",
    "hr_avg_col": "HR AVG", "hr_max_col": "HR MAX", "kcal_col": "KCAL",
    "accel_z3_col": "Accel_Z3", "decel_z3_col": "Decel_Z3", "ts_col": "ST_TMSP",
}

DISP_COLS    = ["選手名","チーム","ポジション","総距離(m)","最高速度","スプリント数","加減速値","プレー時間"]
DETAIL_COLS  = ["選手名","ポジション","総距離(m)","最高速度","スプリント数","加減速値","プレー時間","消費kcal","平均HR","最高HR"]
PDF_OWN_COLS = ["順位","選手名","ポジション","総距離(m)","最高速度","スプリント数","加減速値"]
PDF_RNK_COLS = ["順位","選手名","チーム","総距離(m)","最高速度","スプリント数","加減速値"]
RANK_METRICS = ["総距離(m)","最高速度","スプリント数","加減速値"]
CHART_METRICS = [("総距離(m)","m"),("最高速度","km/h"),("スプリント数","回"),("加減速値","")]
NUM_COLS     = ["総距離(m)","平均速度","最高速度","スプリント数","加減速値","消費kcal","平均HR","最高HR"]
STR_COLS     = ["ポジション","プレー時間","source","背番号"]
DB_PATH      = Path(__file__).parent / "data" / "history.db"

# ── データベース ──────────────────────────────────────────────────────────────

def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            session_label TEXT NOT NULL,
            session_date  TEXT NOT NULL,
            created_at    TEXT
        );
        CREATE TABLE IF NOT EXISTS records (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   INTEGER NOT NULL REFERENCES sessions(id),
            player_name  TEXT NOT NULL,
            team         TEXT NOT NULL,
            position     TEXT,
            distance     REAL, avg_speed REAL, max_speed REAL,
            sprints      REAL, accel_decel REAL, kcal REAL,
            avg_hr       REAL, max_hr REAL, play_time TEXT
        );
        """)


def save_session(df: pd.DataFrame, label: str, date: str):
    init_db()
    with sqlite3.connect(DB_PATH) as con:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = con.execute("INSERT INTO sessions (session_label,session_date,created_at) VALUES (?,?,?)", (label, date, now))
        sid = cur.lastrowid
        for _, r in df.iterrows():
            def fv(k): v = r.get(k); return float(v) if pd.notna(v) else None
            con.execute(
                "INSERT INTO records VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, str(r.get("選手名","")), str(r.get("チーム","")), str(r.get("ポジション","")),
                 fv("総距離(m)"), fv("平均速度"), fv("最高速度"),
                 fv("スプリント数"), fv("加減速値"), fv("消費kcal"),
                 fv("平均HR"), fv("最高HR"), str(r.get("プレー時間","")))
            )


def load_history() -> pd.DataFrame:
    init_db()
    with sqlite3.connect(DB_PATH) as con:
        return pd.read_sql_query("""
            SELECT s.id AS session_id, s.session_label, s.session_date,
                   r.player_name AS 選手名, r.team AS チーム, r.position AS ポジション,
                   r.distance AS 総距離, r.avg_speed AS 平均速度, r.max_speed AS 最高速度,
                   r.sprints AS スプリント数, r.accel_decel AS 加減速値,
                   r.kcal AS 消費kcal, r.avg_hr AS 平均HR, r.max_hr AS 最高HR
            FROM sessions s JOIN records r ON r.session_id = s.id
            ORDER BY s.session_date, s.id
        """, con)


def list_sessions() -> pd.DataFrame:
    init_db()
    with sqlite3.connect(DB_PATH) as con:
        return pd.read_sql_query(
            "SELECT id, session_label, session_date, created_at FROM sessions ORDER BY session_date DESC",
            con
        )


def delete_session(sid: int):
    with sqlite3.connect(DB_PATH) as con:
        con.execute("DELETE FROM records  WHERE session_id=?", (sid,))
        con.execute("DELETE FROM sessions WHERE id=?", (sid,))


def delete_sessions(sids: list):
    with sqlite3.connect(DB_PATH) as con:
        for sid in sids:
            con.execute("DELETE FROM records  WHERE session_id=?", (sid,))
            con.execute("DELETE FROM sessions WHERE id=?", (sid,))


def update_session(sid: int, label: str, date: str):
    with sqlite3.connect(DB_PATH) as con:
        con.execute("UPDATE sessions SET session_label=?, session_date=? WHERE id=?",
                    (label, date, sid))


def list_sessions_with_count() -> pd.DataFrame:
    """セッション一覧（選手数付き）"""
    init_db()
    with sqlite3.connect(DB_PATH) as con:
        return pd.read_sql_query("""
            SELECT s.id, s.session_label AS セッション名, s.session_date AS 計測日,
                   s.created_at AS 登録日時,
                   COUNT(DISTINCT r.player_name) AS 選手数,
                   COUNT(DISTINCT r.team) AS チーム数
            FROM sessions s
            LEFT JOIN records r ON r.session_id = s.id
            GROUP BY s.id
            ORDER BY s.session_date DESC, s.id DESC
        """, con)


def load_session_records(sid: int) -> pd.DataFrame:
    """特定セッションの選手データ"""
    with sqlite3.connect(DB_PATH) as con:
        return pd.read_sql_query("""
            SELECT player_name AS 選手名, team AS チーム, position AS ポジション,
                   distance AS 総距離, max_speed AS 最高速度,
                   sprints AS スプリント数, accel_decel AS 加減速値,
                   kcal AS 消費kcal, avg_hr AS 平均HR, max_hr AS 最高HR,
                   play_time AS プレー時間
            FROM records WHERE session_id=?
            ORDER BY distance DESC
        """, con, params=(sid,))


# ── GPS データ処理 ─────────────────────────────────────────────────────────────

@st.cache_data
def load_csv(data: bytes, name: str) -> pd.DataFrame:
    for enc in ["utf-8-sig","utf-8","shift_jis","cp932"]:
        try:
            return pd.read_csv(io.BytesIO(data), encoding=enc)
        except Exception:
            continue
    raise ValueError(f"文字コードを認識できません: {name}")


def is_statsports(df: pd.DataFrame) -> bool:
    return {"Name","Team","Distance","SPD MX","Sprint"}.issubset(df.columns)


def extract_session_date(df_raw: pd.DataFrame) -> str:
    if "ST_TMSP" in df_raw.columns:
        try:
            return pd.to_datetime(df_raw["ST_TMSP"].dropna().iloc[0]).strftime("%Y-%m-%d")
        except Exception:
            pass
    return datetime.date.today().strftime("%Y-%m-%d")


def clean_statsports(df: pd.DataFrame, source: str = "") -> pd.DataFrame:
    m = STATSPORTS_MAP
    res = pd.DataFrame({
        "source":     source,
        "背番号":      pd.to_numeric(df.get(m["number_col"], pd.NA), errors="coerce"),
        "選手名":      df[m["name_col"]],
        "チーム":      df[m["team_col"]],
        "ポジション":  df.get(m["pos_col"], ""),
        "プレー時間":  df.get(m["duration_col"], ""),
        "総距離(m)":   pd.to_numeric(df[m["dist_col"]], errors="coerce").round(1),
        "平均速度":    pd.to_numeric(df.get(m["spd_av_col"], pd.NA), errors="coerce").round(1),
        "最高速度":    pd.to_numeric(df.get(m["spd_mx_col"], pd.NA), errors="coerce").round(1),
        "スプリント数": pd.to_numeric(df.get(m["sprint_col"], pd.NA), errors="coerce"),
        "消費kcal":    pd.to_numeric(df.get(m["kcal_col"], pd.NA), errors="coerce").round(1),
        "平均HR":      pd.to_numeric(df.get(m["hr_avg_col"], pd.NA), errors="coerce").replace(0, pd.NA),
        "最高HR":      pd.to_numeric(df.get(m["hr_max_col"], pd.NA), errors="coerce").replace(0, pd.NA),
    })
    accel = pd.to_numeric(df.get(m["accel_z3_col"], 0), errors="coerce").fillna(0)
    decel = pd.to_numeric(df.get(m["decel_z3_col"], 0), errors="coerce").fillna(0)
    res["加減速値"] = (accel + decel).round(1)
    return res


# ── 表示ヘルパー ──────────────────────────────────────────────────────────────

def rank_label(n): return MEDALS.get(n, f"{n}位")

def add_rank_col(df: pd.DataFrame, sort_col: str) -> pd.DataFrame:
    df = df.sort_values(sort_col, ascending=False).reset_index(drop=True)
    df.insert(0, "順位", [rank_label(i+1) for i in range(len(df))])
    return df

def round_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in df.select_dtypes(include="number").columns:
        df[c] = df[c].round(1)
    return df

def show_table(df):
    st.dataframe(round_df(df).style, use_container_width=True, hide_index=True)

def anonymize(df: pd.DataFrame, own_team: str) -> pd.DataFrame:
    df = df.copy(); df.loc[df["チーム"] != own_team, "選手名"] = "―"; return df

def calc_team_scores(df, metrics, method):
    rows = []
    for team, g in df.groupby("チーム"):
        row = {"チーム": team, "人数": len(g)}
        for m in metrics:
            vals = pd.to_numeric(g[m], errors="coerce").dropna().sort_values(ascending=False)
            if method == "全員合計":   row[m] = vals.sum()
            elif method == "全員平均":  row[m] = vals.mean()
            else:                       row[m] = vals.head(3).sum()
        rows.append(row)
    return pd.DataFrame(rows)


# ── グラフ生成 ─────────────────────────────────────────────────────────────────

COLORS = ["#1b3a5c","#e63946","#2a9d8f","#e9c46a","#f4a261","#264653"]

def make_team_chart(df: pd.DataFrame, team_name: str, hist_df: pd.DataFrame = None) -> bytes:
    """自チームの4指標棒グラフ（+ 前回比較あれば表示）"""
    fig, axes = plt.subplots(2, 2, figsize=(11, 7))
    fig.patch.set_facecolor("#f8f9fa")
    for ax, (metric, unit) in zip(axes.flat, CHART_METRICS):
        if metric not in df.columns:
            ax.set_visible(False); continue
        names = df["選手名"].values
        vals  = pd.to_numeric(df[metric], errors="coerce").values
        idx   = vals.argsort()
        bars  = ax.barh(names[idx], vals[idx], color="#1b3a5c", height=0.55)
        # 前回比較ライン
        if hist_df is not None and not hist_df.empty:
            col_map = {"総距離(m)":"総距離","最高速度":"最高速度","スプリント数":"スプリント数","加減速値":"加減速値"}
            hcol = col_map.get(metric)
            if hcol and hcol in hist_df.columns:
                prev = hist_df.groupby("選手名")[hcol].last()
                for i, name in enumerate(names[idx]):
                    if name in prev.index and pd.notna(prev[name]):
                        ax.axvline(prev[name], ymin=(i-0.3)/len(names), ymax=(i+0.3+0.55)/len(names),
                                   color="#e63946", linewidth=1.5, linestyle="--", alpha=0.8)
        ax.set_title(f"{metric}　({unit})" if unit else metric, fontsize=10, fontweight="bold")
        ax.spines[["top","right"]].set_visible(False)
        for bar, v in zip(bars, vals[idx]):
            if pd.notna(v):
                ax.text(v + max(vals[~pd.isna(vals)], default=1)*0.01,
                        bar.get_y()+bar.get_height()/2, f"{v:.1f}", va="center", fontsize=8)
    label = "赤点線=前回値" if hist_df is not None and not hist_df.empty else ""
    plt.suptitle(f"【{team_name}】計測データ　{label}", fontsize=13, fontweight="bold")
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def make_history_chart(hist_df: pd.DataFrame, players: list, metric: str) -> bytes:
    """選択選手の時系列折れ線グラフ"""
    col_map = {"総距離(m)":"総距離","最高速度":"最高速度","スプリント数":"スプリント数","加減速値":"加減速値"}
    hcol = col_map.get(metric, metric)
    if hcol not in hist_df.columns:
        return b""
    fig, ax = plt.subplots(figsize=(10, 5))
    for i, player in enumerate(players):
        sub = hist_df[hist_df["選手名"] == player].sort_values("session_date")
        if sub.empty: continue
        ax.plot(sub["session_label"], pd.to_numeric(sub[hcol], errors="coerce"),
                marker="o", label=player, color=COLORS[i % len(COLORS)], linewidth=2)
    ax.set_title(f"{metric} の推移", fontsize=12, fontweight="bold")
    ax.set_xlabel("セッション"); ax.set_ylabel(metric)
    ax.legend(fontsize=9, loc="best")
    ax.spines[["top","right"]].set_visible(False)
    plt.xticks(rotation=25, ha="right", fontsize=8)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def make_team_trend_chart(hist_df: pd.DataFrame, metric: str) -> bytes:
    """チームごとの平均値時系列"""
    col_map = {"総距離(m)":"総距離","最高速度":"最高速度","スプリント数":"スプリント数","加減速値":"加減速値"}
    hcol = col_map.get(metric, metric)
    if hcol not in hist_df.columns:
        return b""
    fig, ax = plt.subplots(figsize=(10, 5))
    for i, (team, g) in enumerate(hist_df.groupby("チーム")):
        avg = (g.groupby(["session_label","session_date"])[hcol]
                 .mean().reset_index().sort_values("session_date"))
        ax.plot(avg["session_label"], pd.to_numeric(avg[hcol], errors="coerce"),
                marker="s", label=team, color=COLORS[i % len(COLORS)], linewidth=2)
    ax.set_title(f"チーム平均 {metric} の推移", fontsize=12, fontweight="bold")
    ax.set_xlabel("セッション"); ax.set_ylabel(metric)
    ax.legend(fontsize=9)
    ax.spines[["top","right"]].set_visible(False)
    plt.xticks(rotation=25, ha="right", fontsize=8)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


# ── 比較テーブル ──────────────────────────────────────────────────────────────

HIST_COL_MAP = {
    "総距離(m)": "総距離",
    "最高速度":   "最高速度",
    "スプリント数": "スプリント数",
    "加減速値":   "加減速値",
}


def build_comparison(hist_df: pd.DataFrame, session_ids: list, metric: str):
    """
    選択セッション × 選手 のワイドテーブルを返す。
    変化列（最新 - 最古）と変化率列も付加する。
    戻り値: (pivot_df, ordered_col_keys)
    """
    hcol = HIST_COL_MAP.get(metric, metric)
    sub  = hist_df[hist_df["session_id"].isin(session_ids)].copy()
    if sub.empty:
        return pd.DataFrame(), []

    # セッションを日付順に並べ、重複ラベルでも一意なキーを作る
    sess_order = (
        sub[["session_id", "session_label", "session_date"]]
        .drop_duplicates()
        .sort_values(["session_date", "session_id"])
    )
    # 列キー: "ラベル（日付）" — session_id も付与して一意にする
    sess_order["col_key"] = sess_order.apply(
        lambda r: f"{r['session_label']}（{r['session_date']}）", axis=1
    )
    # 重複を連番で区別
    seen = {}
    col_keys = []
    for k in sess_order["col_key"]:
        seen[k] = seen.get(k, 0) + 1
        col_keys.append(k if seen[k] == 1 else f"{k} #{seen[k]}")
    sess_order["col_key"] = col_keys
    id_to_key = dict(zip(sess_order["session_id"], sess_order["col_key"]))

    sub["col_key"] = sub["session_id"].map(id_to_key)

    pivot = sub.pivot_table(
        index=["選手名", "チーム"],
        columns="col_key",
        values=hcol,
        aggfunc="first",
    ).reset_index()
    pivot.columns.name = None

    ordered_keys = [k for k in col_keys if k in pivot.columns]
    pivot = pivot[["選手名", "チーム"] + ordered_keys].copy()
    for c in ordered_keys:
        pivot[c] = pd.to_numeric(pivot[c], errors="coerce").round(1)

    # 変化・変化率 (最新 - 最古)
    if len(ordered_keys) >= 2:
        first, last = ordered_keys[0], ordered_keys[-1]
        pivot["変化"] = (pivot[last] - pivot[first]).round(1)
        pivot["変化率(%)"] = (pivot["変化"] / pivot[first] * 100).round(1)

    return pivot, ordered_keys


def style_comparison(df: pd.DataFrame, label_cols: list):
    """変化列を緑/赤で色付け、各指標セルも差分の大きさでグラデーション"""
    def cell_color(val, col):
        if col == "変化" or col == "変化率(%)":
            if pd.isna(val):   return ""
            if val > 0:        return "background-color:#d4edda;color:#155724"
            if val < 0:        return "background-color:#f8d7da;color:#721c24"
            return "background-color:#fff3cd;color:#856404"
        return ""

    def apply_colors(df_):
        styles = pd.DataFrame("", index=df_.index, columns=df_.columns)
        for col in df_.columns:
            styles[col] = df_[col].apply(lambda v: cell_color(v, col))
        return styles

    styled = df.style.apply(apply_colors, axis=None)

    # 変化セルに ↑↓ テキスト付加
    def fmt_change(v):
        if pd.isna(v): return "—"
        arrow = "↑" if v > 0 else ("↓" if v < 0 else "→")
        return f"{arrow} {v:+.1f}"

    def fmt_pct(v):
        if pd.isna(v): return "—"
        arrow = "↑" if v > 0 else ("↓" if v < 0 else "→")
        return f"{arrow} {v:+.1f}%"

    fmt = {}
    if "変化" in df.columns:    fmt["変化"]     = fmt_change
    if "変化率(%)" in df.columns: fmt["変化率(%)"] = fmt_pct
    for c in label_cols:
        fmt[c] = lambda v: f"{v:.1f}" if pd.notna(v) else "—"
    return styled.format(fmt)


def build_all_metrics_summary(hist_df: pd.DataFrame, session_ids: list):
    """
    全指標について最新 vs 最古の変化を選手ごとにまとめたサマリー表。
    返却: df（選手名, チーム, 指標×変化）
    """
    rows = []
    for metric in RANK_METRICS:
        pivot, label_cols = build_comparison(hist_df, session_ids, metric)
        if pivot.empty or "変化" not in pivot.columns:
            continue
        for _, r in pivot.iterrows():
            rows.append({
                "選手名": r["選手名"],
                "チーム": r["チーム"],
                "指標":   metric,
                "最古値": r[label_cols[0]]  if label_cols else None,
                "最新値": r[label_cols[-1]] if label_cols else None,
                "変化":   r.get("変化"),
                "変化率(%)": r.get("変化率(%)"),
            })
    return pd.DataFrame(rows)


# ── PDF 生成 ──────────────────────────────────────────────────────────────────

def _to_pdf_text(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "順位" in df.columns:
        df["順位"] = df["順位"].replace({"🥇":"1位","🥈":"2位","🥉":"3位"})
    return df


def _pdf_table(pdf: FPDF, df: pd.DataFrame, title: str):
    PAGE_W = pdf.w - pdf.l_margin - pdf.r_margin
    ROW_H, HDR_H = 8, 9
    weights = {"順位":1.0,"選手名":2.2,"チーム":1.8,"ポジション":1.2,"プレー時間":1.5}
    col_w = [PAGE_W * weights.get(c, 1.4) / sum(weights.get(c, 1.4) for c in df.columns)
             for c in df.columns]
    pdf.set_font("JP", size=13); pdf.set_text_color(13, 27, 42)
    pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT", align="C"); pdf.ln(2)
    pdf.set_font("JP", size=8)
    pdf.set_fill_color(13, 27, 42); pdf.set_text_color(255, 255, 255)
    for col, w in zip(df.columns, col_w):
        pdf.cell(w, HDR_H, str(col), border=1, align="C", fill=True)
    pdf.ln()
    pdf.set_text_color(30, 30, 30)
    for _, row in df.iterrows():
        for val, w in zip(row.values, col_w):
            pdf.cell(w, ROW_H, "―" if pd.isna(val) else str(val), border=1, align="C")
        pdf.ln()
    pdf.ln(3)


def _pdf_image(pdf: FPDF, png_bytes: bytes):
    if not png_bytes:
        return
    pdf.add_page()
    with open("/tmp/_gps_chart.png", "wb") as f:
        f.write(png_bytes)
    pdf.image("/tmp/_gps_chart.png", x=10, y=15, w=pdf.w - 20)


def generate_team_pdf(team_name, df_own_str, df_own_num, df_ranking_anon,
                      show_ranking, event_label="", hist_df=None):
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    if not JP_FONT:
        raise RuntimeError("日本語フォントが見つかりません。packages.txt に fonts-noto-cjk を追加してください。")
    pdf.add_font("JP", "", JP_FONT)
    pdf.set_auto_page_break(auto=True, margin=15)

    # 表紙
    pdf.add_page()
    pdf.set_font("JP", size=22); pdf.set_text_color(13, 27, 42)
    pdf.ln(30)
    pdf.cell(0, 14, "GPS 計測データ", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("JP", size=17)
    pdf.cell(0, 11, f"【 {team_name} 】", align="C", new_x="LMARGIN", new_y="NEXT")
    if event_label:
        pdf.ln(4); pdf.set_font("JP", size=11); pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 8, event_label, align="C", new_x="LMARGIN", new_y="NEXT")

    # 自チームデータ表
    pdf.add_page()
    _pdf_table(pdf, _to_pdf_text(df_own_str), f"【{team_name}】メンバー計測データ")

    # 自チームグラフ（前回比較あり）
    prev_hist = None
    if hist_df is not None and not hist_df.empty:
        t_hist = hist_df[hist_df["チーム"] == team_name]
        prev_hist = t_hist if not t_hist.empty else None
    chart_png = make_team_chart(df_own_num, team_name, prev_hist)
    _pdf_image(pdf, chart_png)

    # 全体ランキング
    if show_ranking and df_ranking_anon is not None:
        pdf.add_page()
        _pdf_table(pdf, _to_pdf_text(df_ranking_anon), "全体ランキング（他チームの選手名は非表示）")

    return bytes(pdf.output())


# ── ファイルアップロード ───────────────────────────────────────────────────────

st.markdown("### 📂 CSVファイルをアップロード")
st.caption("複数チームのCSVを一度にアップロードできます（STATSPORTSエクスポートCSVに対応）")

col_up, col_sample = st.columns([3, 1])
with col_up:
    uploaded_files = st.file_uploader("CSVファイルを選択（複数可）", type=["csv"], accept_multiple_files=True)

_sample = """Name,Number,Position,Team,Duration_TF,Distance,SPD AV,SPD MX,Sprint,HR AVG,HR MAX,KCAL,Accel_Z3,Decel_Z3,ST_TMSP
田中 太郎,10,FW,チームA,0:38:22,3200,6.5,22.3,8,145,185,95.2,4,3,2026-05-21 10:00:00
佐藤 花子,7,MF,チームA,0:38:22,2800,5.8,19.1,5,138,175,82.0,2,2,2026-05-21 10:00:00
鈴木 一郎,9,FW,チームB,0:38:22,3500,7.1,24.5,11,152,190,105.5,6,4,2026-05-21 10:00:00
高橋 美咲,11,MF,チームB,0:38:22,3100,6.3,20.8,7,140,178,90.1,3,2,2026-05-21 10:00:00
伊藤 健二,8,FW,チームC,0:38:22,2950,6.0,21.0,6,148,182,88.3,5,3,2026-05-21 10:00:00
渡辺 さくら,6,MF,チームC,0:38:22,2600,5.3,17.5,4,132,168,77.8,2,1,2026-05-21 10:00:00
"""
with col_sample:
    st.download_button("📥 サンプルCSV", _sample.encode("utf-8"),
                       file_name="sample_gps.csv", mime="text/csv", use_container_width=True)

# ── データ読み込み ─────────────────────────────────────────────────────────────

csv_loaded = False
df = pd.DataFrame()
teams, has_teams = [], False

if uploaded_files:
    dfs_clean = []
    auto_dates = []
    for f in uploaded_files:
        try:
            df_raw = load_csv(f.getvalue(), f.name)
        except Exception as e:
            st.error(f"❌ {f.name}: {e}"); continue
        if is_statsports(df_raw):
            dfs_clean.append(clean_statsports(df_raw, f.name))
            auto_dates.append(extract_session_date(df_raw))
        else:
            st.warning(f"⚠️ {f.name} は標準GPS形式と異なります")
            with st.expander(f"{f.name} の列マッピング"):
                cols = list(df_raw.columns)
                c1, c2, c3 = st.columns(3)
                nc = c1.selectbox("選手名", cols, key=f"nc_{f.name}")
                tc = c2.selectbox("チーム名", ["なし"]+cols, key=f"tc_{f.name}")
                dc = c3.selectbox("距離列", cols, key=f"dc_{f.name}")
                tc = None if tc == "なし" else tc
                dfs_clean.append(pd.DataFrame({
                    "source":f.name,"背番号":pd.NA,"選手名":df_raw[nc],
                    "チーム":df_raw[tc] if tc else "（未設定）","ポジション":"","プレー時間":"",
                    "総距離(m)":pd.to_numeric(df_raw[dc],errors="coerce"),
                    **{c:pd.NA for c in ["平均速度","最高速度","スプリント数","加減速値","消費kcal","平均HR","最高HR"]}
                }))
                auto_dates.append(datetime.date.today().strftime("%Y-%m-%d"))

    if dfs_clean:
        df_all = pd.concat(dfs_clean, ignore_index=True)
        _agg = {**{c:"max" for c in NUM_COLS if c in df_all.columns},
                **{c:"first" for c in STR_COLS if c in df_all.columns}}
        df       = df_all.groupby(["選手名","チーム"], as_index=False).agg(_agg)
        has_teams = df["チーム"].nunique() > 1
        teams     = sorted(df["チーム"].unique())
        csv_loaded = True

        # セッション保存UI
        with st.expander("💾 このデータを履歴に保存する"):
            sc1, sc2, sc3 = st.columns([2,2,1])
            default_date = auto_dates[0] if auto_dates else datetime.date.today().strftime("%Y-%m-%d")
            s_label = sc1.text_input("セッションラベル（例: 玖珠SS 第1回）", placeholder="練習名・大会名")
            s_date  = sc2.text_input("計測日（YYYY-MM-DD）", value=default_date)
            if sc3.button("保存する", use_container_width=True):
                if not s_label.strip():
                    st.warning("セッションラベルを入力してください")
                else:
                    save_session(df, s_label.strip(), s_date)
                    st.success(f"「{s_label}」を保存しました（{len(df)}名）")
                    st.cache_data.clear()

        # サマリーカード
        st.markdown('<div class="section-header">📊 サマリー</div>', unsafe_allow_html=True)
        c1,c2,c3,c4,c5 = st.columns(5)
        def mc(label,value,unit=""):
            return f'<div class="metric-card"><div class="label">{label}</div><div class="value">{value}</div><div class="unit">{unit}</div></div>'
        c1.markdown(mc("参加選手数",len(df),"名"),unsafe_allow_html=True)
        c2.markdown(mc("チーム数",df["チーム"].nunique(),"チーム"),unsafe_allow_html=True)
        c3.markdown(mc("最大総距離",f"{df['総距離(m)'].max():.1f}","m"),unsafe_allow_html=True)
        c4.markdown(mc("最高速度",f"{df['最高速度'].max():.1f}" if df["最高速度"].notna().any() else "—","km/h"),unsafe_allow_html=True)
        c5.markdown(mc("最多スプリント",f"{df['スプリント数'].max():.0f}" if df["スプリント数"].notna().any() else "—","回"),unsafe_allow_html=True)

# ── タブ ──────────────────────────────────────────────────────────────────────

tab_ind, tab_team, tab_detail, tab_dist, tab_hist, tab_mgr, tab_print = st.tabs([
    "🏅 個人順位","🏆 チーム順位","📋 チーム別詳細","📤 チーム配布",
    "📊 履歴・比較","🗂️ 履歴管理","🖨️ 全体出力"
])

_NO_CSV = "CSVファイルをアップロードすると表示されます。"

# ===== 個人順位 =====
with tab_ind:
    st.markdown('<div class="section-header">🏅 個人順位</div>', unsafe_allow_html=True)
    if not csv_loaded:
        st.info(_NO_CSV)
    else:
        rm = st.radio("ランキング基準", RANK_METRICS, horizontal=True, key="ind_m")
        show_table(add_rank_col(df[DISP_COLS].copy(), rm))
        positions = [p for p in df["ポジション"].dropna().unique() if str(p).strip()]
        if len(positions) > 1:
            st.markdown("**ポジション別**")
            for pt, pos in zip(st.tabs(sorted(positions)), sorted(positions)):
                with pt:
                    show_table(add_rank_col(df[df["ポジション"]==pos][DISP_COLS].copy(), rm))

# ===== チーム順位 =====
with tab_team:
    st.markdown('<div class="section-header">🏆 チーム順位</div>', unsafe_allow_html=True)
    if not csv_loaded:
        st.info(_NO_CSV)
    elif not has_teams:
        st.info("複数チームのCSVをアップロードするとチーム順位が表示されます。")
    else:
        sm  = st.radio("集計方法", ["全員合計","全員平均","上位3名合計"], horizontal=True)
        trm = st.radio("ランキング基準", RANK_METRICS, horizontal=True, key="team_m")
        dt  = add_rank_col(round_df(calc_team_scores(df, RANK_METRICS, sm)), trm)
        show_table(dt)

# ===== チーム別詳細 =====
with tab_detail:
    st.markdown('<div class="section-header">📋 チーム別詳細</div>', unsafe_allow_html=True)
    if not csv_loaded:
        st.info(_NO_CSV)
    else:
        for tab_t, team in zip(st.tabs(teams), teams):
            with tab_t:
                df_t = add_rank_col(df[df["チーム"]==team][DETAIL_COLS].copy(), "総距離(m)")
                show_table(df_t)
                valid = [c for c in ["総距離(m)","最高速度","スプリント数","加減速値","消費kcal"] if df_t[c].notna().any()]
                if valid:
                    st.markdown("**チーム統計**")
                    s = df_t[valid].describe().loc[["mean","max","min"]].round(1)
                    s.index = ["平均","最大","最小"]
                    st.dataframe(s, use_container_width=True)

# ===== チーム配布 =====
with tab_dist:
    st.markdown('<div class="section-header">📤 チーム別配布データ</div>', unsafe_allow_html=True)
    if not csv_loaded:
        st.info(_NO_CSV)
    else:
        st.caption("選択チームには選手名を表示、他チームの選手名は非表示にします")

        ca, cb = st.columns([2,2])
        sel_team   = ca.selectbox("対象チームを選択", teams, key="dist_t")
        ev_label   = ca.text_input("イベント名・日付（PDF表紙に記載）", placeholder="例: 玖珠SS 2026-05-21")
        rmd        = cb.radio("ランキング基準", RANK_METRICS, horizontal=True, key="dist_rm")
        show_rnk   = cb.toggle("全体ランキングを表示する", value=True)

        st.divider()

        # 自チームデータ（数値保持）
        df_own_num = df[df["チーム"]==sel_team][DETAIL_COLS].copy()
        df_own_ranked = add_rank_col(df_own_num, rmd)
        st.markdown(f"#### 【{sel_team}】メンバー計測データ")
        show_table(df_own_ranked)

        # グラフ（Streamlit上にも表示）
        # 直近の履歴を前回比較用に取得
        hist_all = load_history()
        hist_team = hist_all[hist_all["チーム"]==sel_team] if not hist_all.empty else pd.DataFrame()
        chart_png = make_team_chart(df_own_num, sel_team, hist_team if not hist_team.empty else None)
        st.image(chart_png, use_container_width=True)
        if not hist_team.empty:
            st.caption("赤破線 = 前回セッション値との比較")

        # 全体ランキング（匿名化）
        df_rnk_anon = None
        if show_rnk:
            st.markdown("#### 全体ランキング（他チームの選手名は非表示）")
            df_rnk_anon = add_rank_col(anonymize(df[DISP_COLS].copy(), sel_team), rmd)
            show_table(df_rnk_anon)

        st.divider()
        st.markdown("**PDFダウンロード**")

        # PDF用データ（文字列化）
        def to_str_df(dframe, cols):
            d = dframe[cols].copy()
            for c in d.select_dtypes(include="number").columns:
                d[c] = d[c].apply(lambda v: f"{v:.1f}" if pd.notna(v) else "―")
            return d

        df_own_pdf = to_str_df(df_own_ranked, PDF_OWN_COLS)
        df_rnk_pdf = to_str_df(df_rnk_anon, PDF_RNK_COLS) if df_rnk_anon is not None else None

        try:
            pdf_bytes = generate_team_pdf(
                sel_team, df_own_pdf, df_own_num,
                df_rnk_pdf, show_rnk, ev_label,
                hist_all if not hist_all.empty else None
            )
            st.download_button(
                f"📄 {sel_team} の配布用PDFをダウンロード",
                pdf_bytes, f"{sel_team}_GPS計測データ.pdf",
                mime="application/pdf", use_container_width=True
            )
        except Exception as e:
            st.error(f"PDF生成エラー: {e}")

# ===== 履歴・比較 =====
with tab_hist:
    st.markdown('<div class="section-header">📊 履歴・比較</div>', unsafe_allow_html=True)

    hist_df = load_history()

    if hist_df.empty:
        st.info("まだ保存されたセッションがありません。「💾 このデータを履歴に保存する」からデータを保存してください。")
    else:
        # ── セッション選択 ────────────────────────────────────────────────
        sess_list = list_sessions_with_count()
        sess_options = {
            row["id"]: f"{row['セッション名']}（{row['計測日']}）"
            for _, row in sess_list.iterrows()
        }
        # デフォルト: 最新2件
        default_ids = sess_list["id"].head(2).tolist()

        sel_ids = st.multiselect(
            "比較するセッションを選択（2つ以上推奨）",
            options=list(sess_options.keys()),
            default=default_ids,
            format_func=lambda i: sess_options[i],
            key="cmp_sessions",
        )

        if not sel_ids:
            st.info("比較したいセッションを選択してください。")
        else:
            sub_hist = hist_df[hist_df["session_id"].isin(sel_ids)].copy()

            # ── 指標別 比較テーブル ───────────────────────────────────────
            st.markdown('<div class="section-header">📋 指標別 比較テーブル</div>', unsafe_allow_html=True)
            cmp_metric = st.radio("比較する指標", RANK_METRICS, horizontal=True, key="cmp_metric")

            pivot, label_cols = build_comparison(hist_df, sel_ids, cmp_metric)
            if not pivot.empty:
                styled = style_comparison(pivot, label_cols)
                st.dataframe(styled, use_container_width=True, hide_index=True)
                if len(label_cols) >= 2:
                    st.caption(f"変化 = 【{label_cols[-1]}】−【{label_cols[0]}】　緑=増加 ／ 赤=減少 ／ 黄=変化なし")
            else:
                st.warning("選択したセッションにデータがありません。")

            # ── 全指標サマリー ────────────────────────────────────────────
            if len(label_cols) >= 2:
                st.markdown('<div class="section-header">🔍 全指標 変化サマリー</div>', unsafe_allow_html=True)
                st.caption(f"【{label_cols[0]}】→【{label_cols[-1]}】の変化を全指標でまとめています")

                summary = build_all_metrics_summary(hist_df, sel_ids)
                if not summary.empty:
                    # 指標ごとにタブで表示
                    smry_tabs = st.tabs(RANK_METRICS)
                    for stab, metric in zip(smry_tabs, RANK_METRICS):
                        with stab:
                            s = summary[summary["指標"] == metric][
                                ["選手名","チーム","最古値","最新値","変化","変化率(%)"]
                            ].copy()
                            s = s.sort_values("変化", ascending=False).reset_index(drop=True)

                            def _style_summary(df_):
                                styles = pd.DataFrame("", index=df_.index, columns=df_.columns)
                                for i, v in enumerate(df_["変化"]):
                                    if pd.isna(v): continue
                                    color = "#d4edda" if v > 0 else ("#f8d7da" if v < 0 else "#fff3cd")
                                    tcolor = "#155724" if v > 0 else ("#721c24" if v < 0 else "#856404")
                                    styles.iloc[i] = [f"background-color:{color};color:{tcolor}"] * len(df_.columns)
                                return styles

                            def _fmt_chg(v):
                                if pd.isna(v): return "—"
                                return f"{'↑' if v>0 else '↓' if v<0 else '→'} {v:+.1f}"
                            def _fmt_pct(v):
                                if pd.isna(v): return "—"
                                return f"{'↑' if v>0 else '↓' if v<0 else '→'} {v:+.1f}%"

                            styled_s = (
                                s.style
                                .apply(_style_summary, axis=None)
                                .format({
                                    "最古値": lambda v: f"{v:.1f}" if pd.notna(v) else "—",
                                    "最新値": lambda v: f"{v:.1f}" if pd.notna(v) else "—",
                                    "変化":   _fmt_chg,
                                    "変化率(%)": _fmt_pct,
                                })
                            )
                            st.dataframe(styled_s, use_container_width=True, hide_index=True)

                            # ダウンロード
                            st.download_button(
                                f"📥 {metric} 比較CSVをダウンロード",
                                s.to_csv(index=False, encoding="utf-8-sig"),
                                f"比較_{metric}.csv", "text/csv",
                                key=f"dl_smry_{metric}",
                            )

            # ── 生データ一覧 ──────────────────────────────────────────────
            with st.expander("📄 生データ一覧"):
                disp = sub_hist[["session_label","session_date","選手名","チーム",
                                  "総距離","最高速度","スプリント数","加減速値"]].copy()
                disp = disp.rename(columns={"総距離":"総距離(m)"})
                st.dataframe(round_df(disp), use_container_width=True, hide_index=True)

# ===== 履歴管理 =====
with tab_mgr:
    st.markdown('<div class="section-header">🗂️ 履歴管理</div>', unsafe_allow_html=True)

    mgr_sess = list_sessions_with_count()

    if mgr_sess.empty:
        st.info("保存されたセッションがありません。CSVをアップロードして「💾 履歴に保存する」から登録してください。")
    else:
        # ── セッション一覧（編集可能テーブル） ─────────────────────────────
        st.markdown("#### セッション一覧")
        st.caption("セッション名・計測日は直接編集できます。削除は行右端のチェックボックスを入れて「変更を保存」してください。")

        edit_df = mgr_sess.copy()
        edit_df.insert(0, "削除", False)

        edited = st.data_editor(
            edit_df,
            column_config={
                "削除":        st.column_config.CheckboxColumn("🗑️ 削除", width="small"),
                "id":          st.column_config.NumberColumn("ID", disabled=True, width="small"),
                "セッション名": st.column_config.TextColumn("セッション名", width="large"),
                "計測日":       st.column_config.TextColumn("計測日 (YYYY-MM-DD)", width="medium"),
                "登録日時":     st.column_config.TextColumn("登録日時", disabled=True, width="medium"),
                "選手数":       st.column_config.NumberColumn("選手数", disabled=True, width="small"),
                "チーム数":     st.column_config.NumberColumn("チーム数", disabled=True, width="small"),
            },
            hide_index=True,
            use_container_width=True,
            key="sess_editor",
        )

        sa, sb, sc = st.columns([2, 2, 2])
        if sa.button("💾 変更を保存する", type="primary", use_container_width=True):
            to_delete = edited[edited["削除"] == True]["id"].tolist()
            updated = 0
            for _, row in edited[edited["削除"] == False].iterrows():
                orig = mgr_sess[mgr_sess["id"] == row["id"]]
                if orig.empty:
                    continue
                if (row["セッション名"] != orig["セッション名"].values[0] or
                        row["計測日"] != orig["計測日"].values[0]):
                    update_session(int(row["id"]), str(row["セッション名"]), str(row["計測日"]))
                    updated += 1
            if to_delete:
                delete_sessions(to_delete)
                st.success(f"{len(to_delete)} 件のセッションを削除しました。")
            if updated:
                st.success(f"{updated} 件のセッション情報を更新しました。")
            if not to_delete and not updated:
                st.info("変更はありませんでした。")
            st.rerun()

        # ── 全履歴エクスポート ───────────────────────────────────────────
        all_hist = load_history()
        if not all_hist.empty:
            exp_csv = all_hist.rename(columns={"総距離":"総距離(m)"}).to_csv(index=False, encoding="utf-8-sig")
            sb.download_button("📥 全履歴をCSVでダウンロード", exp_csv,
                               "全履歴データ.csv", "text/csv", use_container_width=True)

        st.divider()

        # ── セッション詳細ビュー ─────────────────────────────────────────
        st.markdown("#### セッション詳細")
        sel_sid = st.selectbox(
            "確認するセッションを選択",
            options=mgr_sess["id"].tolist(),
            format_func=lambda i: f"[{i}] {mgr_sess[mgr_sess['id']==i]['セッション名'].values[0]}  （{mgr_sess[mgr_sess['id']==i]['計測日'].values[0]}）",
            key="detail_sess",
        )
        if sel_sid:
            recs = load_session_records(sel_sid)
            if not recs.empty:
                d1, d2, d3 = st.columns(3)
                d1.metric("選手数", len(recs))
                d2.metric("チーム数", recs["チーム"].nunique())
                d3.metric("平均総距離", f"{recs['総距離'].mean():.1f} m" if recs["総距離"].notna().any() else "—")
                st.dataframe(round_df(recs), use_container_width=True, hide_index=True)

                # このセッションのみCSVダウンロード
                label = mgr_sess[mgr_sess["id"]==sel_sid]["セッション名"].values[0]
                st.download_button(
                    f"📥 このセッションをCSVでダウンロード",
                    recs.to_csv(index=False, encoding="utf-8-sig"),
                    f"{label}_データ.csv", "text/csv",
                )
            else:
                st.warning("このセッションに記録がありません。")

# ===== 全体出力 =====
with tab_print:
    st.markdown('<div class="section-header">🖨️ 全体出力</div>', unsafe_allow_html=True)
    if not csv_loaded:
        st.info(_NO_CSV)
    else:
        df_out = add_rank_col(df[DISP_COLS].copy(), "総距離(m)")
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("📄 個人順位CSV",
                df_out.to_csv(index=False, encoding="utf-8-sig"),
                "個人順位表.csv", "text/csv", use_container_width=True)
        with c2:
            if has_teams and "dt" in dir():
                st.download_button("📄 チーム順位CSV",
                    dt.to_csv(index=False, encoding="utf-8-sig"),
                    "チーム順位表.csv", "text/csv", use_container_width=True)

        st.markdown("---")
        st.markdown("**全チームPDF一括生成**")
        hist_all2 = load_history()
        pdf_cols  = st.columns(len(teams))
        for col, team in zip(pdf_cols, teams):
            df_ot = add_rank_col(df[df["チーム"]==team][DETAIL_COLS].copy(), "総距離(m)")
            df_rt = add_rank_col(anonymize(df[DISP_COLS].copy(), team), "総距離(m)")
            def s2(d,c): return to_str_df(d, c)
            try:
                pb = generate_team_pdf(team, s2(df_ot, PDF_OWN_COLS), df[df["チーム"]==team][DETAIL_COLS].copy(),
                                       s2(df_rt, PDF_RNK_COLS), True, "",
                                       hist_all2 if not hist_all2.empty else None)
                col.download_button(f"📄 {team}", pb, f"{team}_GPS計測データ.pdf",
                                    "application/pdf", use_container_width=True)
            except Exception as e:
                col.error(str(e))

st.caption("GPS計測 順位表ジェネレーター | STATSPORTSエクスポートCSV対応")
