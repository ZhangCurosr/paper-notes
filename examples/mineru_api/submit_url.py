#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例 02：任务提交——URL 全参数版（公式/表格/语言/页范围/复用/强制重解析/flash）
用法：
  set MINERU_API_KEY=sk-user-xxx
  python examples/mineru_api/submit_url.py "https://arxiv.org/pdf/2409.18839.pdf"
"""
import sys
from common import call

URL = sys.argv[1] if len(sys.argv) > 1 else "https://arxiv.org/pdf/2409.18839.pdf"

body = {
    "urls": [URL],
    "formula": True,        # 公式识别
    "table": True,          # 表格识别
    "language": "zh",       # 输出语言
    # "pages": "1-10,15",   # 页范围
    # "extra_formats": ["docx"],   # 额外导出 docx
    # "model": "pipeline",  # pipeline / vlm / html
    # "fresh": True,        # 强制重新解析（默认复用已完成结果）
    # "flash": True,        # URL 走免 token flash 通道
}
r = call("POST", "/v1/tasks", body)
data = r["data"]
print(f"task_ids : {data['task_ids']}")
print(f"reused   : {data.get('reused_ids')}（fresh=false 命中历史完成的 URL）")
print(f"数量     : {data['tasks']} 新建 / {data['reused']} 复用")

# 参数速查（与文档 02 对照）：
# urls/files 至少一项，合计 ≤50；文件扩展名白名单，≤100MB，base64 传输
# files[].pages / files[].ocr / files[].data_id 可单文件指定
