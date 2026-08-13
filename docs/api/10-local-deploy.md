# 10 本地自建部署

**功能**：在自有服务器/笔记本上搭建同一套 API 服务（同一代码库，功能一致）。

## 10.1 启动

```bash
# 依赖：python3 + requests（token 池用）；服务端纯标准库
python scripts/mineru_api_server.py \
  --host 0.0.0.0 --port 8900 \
  --admin-key sk-admin-<随机32位> \
  --tokens "$(cat mineru_tokens.txt | tr '\n' ',')" \
  --strategy weighted --key-rate 60
```

## 10.2 参数表

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--host` | 127.0.0.1 | 0.0.0.0 对外 |
| `--port` | 8900 | 监听端口 |
| `--admin-key` | 自动生成 | 或环境变量 `MINERU_ADMIN_KEY`；<16 字符启动警告 |
| `--tokens` | 环境变量/CSV | 逗号分隔 token 列表 |
| `--strategy` | rr（线上 weighted） | rr / weighted / score |
| `--rate` | 40 | 每 token 每分钟提交数 |
| `--key-rate` | 60 | 每用户 key 每分钟请求数 |
| `--ban-threshold` | 5 | 连续失败熔断阈值 |
| `--health-interval` | 300 | 健康检查间隔秒（0=关） |
| `--model` | pipeline | pipeline / vlm / html |
| `--formula/--table/--ocr` | - | 默认解析选项 |
| `--language` | - | 默认语言（en/zh） |
| `--download-workers` | 8 | 产物下载线程 |
| `--submit-workers` | 8 | 提交线程 |
| `--poll-workers` | 8 | 轮询线程 |
| `--no-flash` | - | 禁用 flash 免 token 通道 |
| `--flash-rate` | 20 | flash 通道限频（/min） |
| `--cors` | 关 | 显式开启 CORS |
| `--data-dir` | data/ | keys/state/uploads 持久化 |
| `--out-dir` | out/ | 产物目录 |

## 10.3 环境变量

| 变量 | 说明 |
| --- | --- |
| `MINERU_TOKENS` | 逗号分隔 token（优先于 --tokens） |
| `MINERU_ADMIN_KEY` | 管理员 key |
| `MINERU_STRATEGY` | 调度策略 |
| `MINERU_BAN_THRESHOLD` | 熔断阈值 |
| `MINERU_HEALTH_INTERVAL` | 健康检查间隔 |

## 10.4 数据持久化（data-dir）

| 文件 | 内容 |
| --- | --- |
| `keys.json` | 用户 key 台账（含 admin） |
| `state.json` | 任务记录 + 调度池统计（latency EMA/ban/suspend 等，重启恢复） |
| `uploads/` | 上传的本地文件 |

> Render 免费层磁盘非持久：admin key 必须经环境变量注入，否则重启丢失。

## 10.5 Docker（Render 同款）

```dockerfile
# deploy/Dockerfile（仓库自带）
FROM python:3.11-slim
WORKDIR /app
COPY scripts/ scripts/
COPY deploy/render.env.example ./
CMD ["python", "scripts/mineru_api_server.py", "--host", "0.0.0.0", "--port", "8900"]
```

```bash
docker build -f deploy/Dockerfile -t mineru-api .
docker run -d -p 8900:8900 \
  -e MINERU_TOKENS="sk-a,sk-b" \
  -e MINERU_ADMIN_KEY="sk-admin-xxx" \
  -e MINERU_STRATEGY=weighted \
  mineru-api
```

## 10.6 验证

```bash
curl -s http://127.0.0.1:8900/health
curl -s -X POST -H "Authorization: Bearer sk-admin-xxx" -H "Content-Type: application/json" \
  -d '{"name":"tester"}' http://127.0.0.1:8900/v1/keys
# 用返回的 user key 走 01-quickstart 的流程
```
