# Converter Engineering Copilot

面向变流器研发与运维场景的证据驱动工程分析应用。它把本地技术资料、可选网页检索和结构化 LLM 工作流组织成可交互的 Web 工作台，支持故障现象分析、技术调研与实验设计、相似检修案例检索。

本项目是基于 [LangChain 开源项目 `open_deep_research`](https://github.com/langchain-ai/open_deep_research) 的个人工程改造，保留上游研究 Agent，并新增 Converter Copilot 产品层。上游采用 MIT License；本仓库保留原始 [LICENSE](LICENSE)。项目中的变流器案例均为人工编写的合成演示样例，不代表真实现场记录、制造商结论或已验证维修结果。

## 功能

- **故障现象分析**：要求补充工况；并行检索本地文档和可选网页来源；给出候选假设、核验步骤和引用。
- **技术调研与实验设计**：整理技术路线、实验条件、局限和可复现实验步骤。
- **检修案例检索**：导入 PDF、Markdown 或 CSV，以本地向量检索和结构化字段对照历史案例。
- **交互式 Web 工作台**：创建任务、查看 SSE 进度、打开来源摘录、回看任务和导出 Markdown 报告。
- **工单辅助研判台**：查看只读合成工单、检查设备上下文与缺失字段、发起故障分析、查看关联证据报告并追加人工审核记录。
- **人工评测工作台**：导入模型运行的证据快照，标注证据支持度和检索金标准，追踪复核版本；未复核标签不参与质量指标。
- **多 Agent 协作审计**：将故障研判拆成受预算限制的角色步骤，呈现执行时间线、候选假设分支、支持与反对证据、适用性门禁和审核未完成状态；不暴露私有 CoT。
- **复核记忆检索**：只从可追溯且被工程师接受的报告构建关联，工单展示来源工单、复核版本、适用差异和报告证据链接；不把相似度解释成因果概率。
- **离线策略对比**：使用同一数据集对比 baseline 和 collaborative，将结构指标与人工质量指标分开；评测摘要可在浏览器本地导入查看。

## 本机运行

需要 Python 3.11、[uv](https://docs.astral.sh/uv/)、Node.js 20+ 和 npm。任务分析需要 DeepSeek API key；文档向量使用本地 FastEmbed 模型，首次启动时会下载模型文件。Tavily 和 LangSmith 是可选服务。没有 Tavily key 时，系统仍可使用本地资料检索。

1. 从模板创建 `.env`，在 API 使用的环境中填写 `DEEPSEEK_API_KEY`。`DEEPSEEK_BASE_URL` 默认指向 DeepSeek 官方 API；`TAVILY_API_KEY` 和 LangSmith 设置可留空。

   ```powershell
   Copy-Item .env.example .env
   ```

2. 启动 API：

   ```powershell
   uv sync --frozen
   uv run uvicorn converter_copilot.api.main:app --reload --port 8000
   ```

3. 在另一个终端启动 Web 前端：

   ```powershell
   cd web
   npm ci
   npm run dev
   ```

4. 打开 <http://localhost:5173>。API 文档在 <http://localhost:8000/docs>，`/api/health` 检查进程存活，`/api/ready` 检查 SQLite 能否在 1 秒内获得写锁并确认后台 worker 正在运行。

数据库、Chroma 向量索引、FastEmbed 模型缓存和评测结果默认写入 `.data/`。文档导入接受 `.pdf`、`.md` 和 `.csv`。向量模型默认使用适合中文技术资料的 `BAAI/bge-small-zh-v1.5`，首次导入或检索时会下载模型；需要联网及约 100 MB 磁盘空间。向量集合使用新的 `converter_engineering_copilot_bge_small_zh_v1_5` 名称，保留旧 OpenAI 向量集合不动；已有资料需重新导入以生成本地向量。不要上传未经授权的工厂、客户或个人数据。

### 本地任务可靠性

SQLite 在 API 启动时执行版本化迁移。升级已有非空数据库前，应用会通过 SQLite 在线备份生成一致副本，默认放在 `.data/backups/`，文件名包含原数据库名、目标 schema 版本和时间。迁移或备份失败时不会启动 worker；`/api/health` 仍返回进程存活，`/api/ready` 返回 HTTP 503。迁移前请确认备份目录所在磁盘有足够空间。

此版本按单机单实例运行：一个 API 进程独占 `.data/` 下的 SQLite、Chroma 和模型缓存。不要启动多个 API 容器或进程共享同一数据目录。Compose 已将这些数据放在 `converter_data` 命名卷中。

后台 worker 从 SQLite 中领取已排队任务，默认并发为 1。服务重启后会继续处理排队任务；运行中任务的租约过期后会重新排队，达到最大尝试次数后转为失败。任务处理按“至少一次”保证：进程在模型调用期间退出时，模型调用可能再次发生，因此外部调用和费用可能重复；报告与终态事件仍在一个 SQLite 事务中提交。

可在 `.env` 中调整以下设置，默认值适用于本地单机：

### 导入校验与恢复

导入器先在内存/临时目录完成校验，之后才写入导入台账、SQLite 案例或 Chroma。默认限制为单文件 20 MiB、PDF 200 页、CSV 10,000 行、每个文件 2,000 个文本片段；可用 `IMPORT_MAX_BYTES`、`IMPORT_MAX_PDF_PAGES`、`IMPORT_MAX_CSV_ROWS`、`IMPORT_MAX_CHUNKS` 调整。CSV 必须是 UTF-8，包含唯一且非空的表头，以及每行必填的 `symptom`；错误响应包含稳定错误码和行号，不回显上传文本。

校验通过后，原文件按内容哈希、解析器版本和切块版本计算稳定导入 ID，原始字节原子保存在 `CONVERTER_SOURCE_DIR`（默认 `.data/sources/`）。SQLite 保存导入状态、哈希、版本、预期片段数和安全错误码；API 和 Web 页面不会暴露本机绝对路径。相同内容与版本重复上传时复用现有台账、案例和 Chroma 向量；解析器或切块版本变化会生成新的导入身份。

工作台的“来源台账”显示导入状态、已索引片段和案例数量。启动时会重放 `validated`、`indexing` 等未完成导入，Web 也提供手动修复。SQLite 与 Chroma 不共享事务，因此台账会保留不完整状态；重放使用稳定 ID 的 upsert，避免生成重复向量或案例。缺失原文件时，先重新上传相同文件恢复本地副本，再执行修复。删除需要浏览器确认；系统按顺序移除对应向量、导入案例和原文件，若中断可继续删除。删除后重新上传相同文件会重新导入。

每条 CSV 案例通过 `record_type=synthetic_demo_fixture` 标记为合成演示数据；工作台明确标示此类数据不是现场记录。导入完成仅表示文件通过结构校验并写入本地检索，不代表工程内容、故障结论或维修效果经过验证。

| 环境变量 | 默认值 | 含义 |
| --- | ---: | --- |
| `CONVERTER_WORKER_CONCURRENCY` | `1` | 同时执行的任务数 |
| `CONVERTER_WORKER_POLL_SECONDS` | `0.5` | 空闲 worker 的轮询间隔 |
| `CONVERTER_TASK_LEASE_SECONDS` | `60` | 任务租约及续租时长 |
| `CONVERTER_TASK_TIMEOUT_SECONDS` | `1800` | 单任务最长执行时间 |
| `CONVERTER_TASK_MAX_ATTEMPTS` | `3` | worker 自动恢复的最大执行次数 |

任务详情页显示执行尝试次数、租约恢复事件和按错误码生成的安全处理建议。API 和任务事件不会返回 worker ID、密钥或原始异常文本；需要排查时请查看本机 API 服务日志。若需恢复迁移前备份，请先停止 API，再将选定的备份文件复制为 `CONVERTER_DB_PATH` 指向的数据库文件，然后重新启动服务。

## Docker Compose

安装 Docker Desktop，创建 `.env` 并填写 `DEEPSEEK_API_KEY`，然后运行：

```powershell
docker compose up --build
```

Web 工作台在 <http://localhost:5173>，API 文档在 <http://localhost:8000/docs>。Compose 使用命名卷保存数据库、Chroma 和 FastEmbed 模型；模型、搜索、LangSmith 密钥仅传给 API 容器，Web 镜像不包含这些密钥。DeepSeek key 没有配置时，页面和存活检查仍可打开，但模型任务会失败并展示安全错误指引。查看 API 日志可用 `docker compose logs -f api`；停止服务但保留本地数据可用 `docker compose down`。

## 评测

```powershell
uv run pytest tests/converter_copilot -q
uv run python evals/converter_copilot/run_eval.py
uv run python evals/converter_copilot/run_eval.py --mode fault_analysis --compare-strategies --recall-at-k 5
```

评测执行真实工作流，需要 `DEEPSEEK_API_KEY`。`--compare-strategies` 会对每条样例各运行 baseline 和 collaborative 一次，模型调用约为单策略运行的两倍；输出摘要与每种策略独立的 review bundle 写到 `.data/evaluation_results/`。在 Web 左侧进入“人工评测”，通过“导入 Agent 离线评测结果”在浏览器本地查看策略比较，再分别导入 review bundle 检查证据摘录与定位、人工标注。仓库中的合成起始样例没有检索金标准和人工支持标签，人工质量指标会保持 `null`；结构完整度不能替代准确率或现场验证。评测字段、复核过程和指标限制见 [评测说明](evals/converter_copilot/README.md)。

## 演示路径与设计说明

- [演示场景](docs/demo-scenario.md)：用合成工单走完字段核对、分析任务、证据报告和人工审核，并可导入合成 CSV 走检索流程。
- [系统架构](docs/architecture.md)：模块边界、数据路径、失败状态和部署方式。
- [本地运维指南](docs/operations.md)：数据恢复、导入修复、人工评测和 CI 检查流程。
- 工单工作台从左侧“工单研判”进入。数据来自包内合成样例，只读且不访问企业工单系统或设备遥测；审核是本地自由文本记录，不代表外部工单状态已变更。

## 边界

此应用提供研发与维护人员复核的辅助信息，不会连接或控制变流器，不会自动下发控制指令，也不会把相似案例当成故障因果证明。模型输出需由具备资质的工程人员结合设备手册、现场规程和测量结果核验。
