# Converter Engineering Copilot

面向变流器研发与运维场景的证据驱动工程分析应用。它把本地技术资料、可选网页检索和结构化 LLM 工作流组织成可交互的 Web 工作台，支持故障现象分析、技术调研与实验设计、相似检修案例检索。

本项目是基于 [LangChain 开源项目 `open_deep_research`](https://github.com/langchain-ai/open_deep_research) 的个人工程改造，保留上游研究 Agent，并新增 Converter Copilot 产品层。上游采用 MIT License；本仓库保留原始 [LICENSE](LICENSE)。项目中的变流器案例均为人工编写的合成演示样例，不代表真实现场记录、制造商结论或已验证维修结果。

## 功能

- **故障现象分析**：要求补充工况；并行检索本地文档和可选网页来源；给出候选假设、核验步骤和引用。
- **技术调研与实验设计**：整理技术路线、实验条件、局限和可复现实验步骤。
- **检修案例检索**：导入 PDF、Markdown 或 CSV，以本地向量检索和结构化字段对照历史案例。
- **交互式 Web 工作台**：创建任务、查看 SSE 进度、打开来源摘录、回看任务和导出 Markdown 报告。
- **评测和追踪**：JSONL 数据集、可重复指标、逐样例失败记录、可选 LangSmith traces。

## 本机运行

需要 Python 3.11、[uv](https://docs.astral.sh/uv/)、Node.js 20+ 和 npm。任务分析需要 OpenAI API key；Tavily 和 LangSmith 是可选服务。没有 Tavily key 时，系统仍可使用本地资料检索。

1. 从模板创建 `.env`，在 API 使用的环境中填写 `OPENAI_API_KEY`。`TAVILY_API_KEY` 和 LangSmith 设置可留空。

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

4. 打开 <http://localhost:5173>。API 文档在 <http://localhost:8000/docs>，存活检查在 `/api/health`，SQLite readiness 检查在 `/api/ready`。

数据库、Chroma 向量索引和评测结果默认写入 `.data/`。文档导入接受 `.pdf`、`.md` 和 `.csv`。首次本地文档导入和检索需要 OpenAI embeddings 配置。不要上传未经授权的工厂、客户或个人数据。

## Docker Compose

安装 Docker Desktop，创建 `.env` 并填写 API key，然后运行：

```powershell
docker compose up --build
```

Web 工作台在 <http://localhost:5173>，API 文档在 <http://localhost:8000/docs>。Compose 使用命名卷保存数据库和 Chroma；模型、搜索、LangSmith 密钥仅传给 API 容器，Web 镜像不包含这些密钥。API key 没有配置时，页面和健康检查仍可打开，但模型任务会失败并展示服务状态。

## 评测

```powershell
uv run pytest tests/converter_copilot -q
uv run python evals/converter_copilot/run_eval.py
uv run python evals/converter_copilot/run_eval.py --mode fault_analysis --recall-at-k 5
```

评测执行真实工作流，需要 `OPENAI_API_KEY`。结果写到 `.data/evaluation_results/`。仓库中的起始样例尚无检索金标准和人工证据支持标注，因此相关指标会如实为 `null`；请先人工复核标注，不能把合成示例或未标注分数写成质量承诺。评测定义、字段和 tracing 说明见 [评测说明](evals/converter_copilot/README.md)。

## 演示路径与设计说明

- [演示场景](docs/demo-scenario.md)：用合成 CSV 导入案例并走完任务、进度和报告流程。
- [系统架构](docs/architecture.md)：模块边界、数据路径、失败状态和部署方式。

## 边界

此应用提供研发与维护人员复核的辅助信息，不会连接或控制变流器，不会自动下发控制指令，也不会把相似案例当成故障因果证明。模型输出需由具备资质的工程人员结合设备手册、现场规程和测量结果核验。
