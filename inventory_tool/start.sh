#!/bin/bash
# 在庫発注計算ツール 起動スクリプト
cd "$(dirname "$0")/.."

IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "不明")

echo "========================================"
echo "  在庫発注計算ツール"
echo "========================================"
echo ""
echo "【あなたのPC用URL】"
echo "  http://localhost:8502"
echo ""
echo "【メンバー共有用URL（同じWiFi内）】"
echo "  http://${IP}:8502"
echo ""
echo "このウィンドウを閉じるとアプリが停止します。"
echo "========================================"
echo ""

if command -v streamlit &>/dev/null; then
  streamlit run inventory_tool/streamlit_app.py --server.address 0.0.0.0 --server.port 8502
elif python3 -m streamlit version &>/dev/null 2>&1; then
  python3 -m streamlit run inventory_tool/streamlit_app.py --server.address 0.0.0.0 --server.port 8502
else
  echo "Streamlit が見つかりません。まず以下を実行してください:"
  echo "  pip3 install -r requirements.txt"
  exit 1
fi
