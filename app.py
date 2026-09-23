"""
前后端分离架构，本文件不再直接运行。

启动方式（开两个终端）：

  终端 1 — 后端：  uvicorn backend.server:app --reload --port 8000
  终端 2 — 前端：  streamlit run frontend/app.py

端口和地址可以通过 .env 里的 BACKEND_URL 调整。
"""
print(__doc__)