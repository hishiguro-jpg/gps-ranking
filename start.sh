#!/bin/bash
# GPS順位表ジェネレーター 起動スクリプト
cd "$(dirname "$0")"

# MacのIPアドレスを取得
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "不明")

echo "========================================"
echo "  GPS 順位表ジェネレーター"
echo "========================================"
echo ""
echo "【あなたのPC用URL】"
echo "  http://localhost:8501"
echo ""
echo "【メンバー共有用URL（同じWiFi内）】"
echo "  http://${IP}:8501"
echo ""
echo "上のURLをメンバーに伝えてください。"
echo "このウィンドウを閉じるとアプリが停止します。"
echo "========================================"
echo ""

if command -v streamlit &>/dev/null; then
  streamlit run app.py --server.address 0.0.0.0
elif python3 -m streamlit version &>/dev/null 2>&1; then
  python3 -m streamlit run app.py --server.address 0.0.0.0
else
  echo "Streamlit が見つかりません。まず以下を実行してください:"
  echo "  pip3 install -r requirements.txt"
  exit 1
fi
