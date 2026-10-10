# Local demo scenario

This walkthrough uses only synthetic demonstration cases. It requires a configured DeepSeek API key for model calls; FastEmbed downloads the local `BAAI/bge-small-zh-v1.5` embedding model on first use. The examples are not real incidents, manufacturer data, or validated maintenance outcomes.

## 1. Start the application

Use the local setup in the root [README](../README.md), or start the Docker Compose services. Check `/api/health` and `/api/ready`; the latter checks SQLite availability.

## 2. Import synthetic maintenance rows

In the workbench, choose **导入技术资料** and upload [`examples/demo_maintenance_cases.csv`](../examples/demo_maintenance_cases.csv). The importer indexes each CSV row and stores one structured case per row. The `record_type` column labels each row `synthetic_demo_fixture`; the source attribution and explanation are also stated in this guide and the evaluation records.

## 3. Retrieve a similar case

Choose **检修案例检索**, enter `光伏并网逆变器` as the equipment type and `启动升载过程中出现直流母线过压告警` as the symptom, then submit. The task page shows queued/running/completed events. The report lists matching fields, differences, recorded validation/handling text, similarity, and the imported row citation. Similarity is not a cause probability.

## 4. Run a fault analysis

Choose **故障现象分析**, enter the same converter and symptom, and provide load, ambient temperature, and grid voltage. A complete retrieval path can use the imported rows as local evidence; optional Tavily adds web sources. The report shows candidate hypotheses, suggested checks, source identifiers, and quality warnings. If conditions are omitted, the workflow asks for clarification instead of drawing a conclusion.

For a useful negative-path demonstration, stop the API or remove a required provider key and submit a task: the UI displays a task/API failure. With the service running but no matching source, the report is partial and contains no generated hypothesis.

## 5. Inspect report evidence and export

Open a citation identifier to inspect its source excerpt and locator. Use **导出 Markdown** to download the structured report. The frontend task page reconnects to progress events through `Last-Event-ID`, so refreshing does not create a second analysis task.

## What to say in a portfolio demo

Describe these rows and any generated output as synthetic and model-assisted. Do not claim measured recall, field validation, or maintenance savings. The evidence check validates traceable citation IDs and conflict flags, not whether an engineering claim is objectively correct. A qualified engineer should confirm any proposed diagnosis against authorized manuals, actual measurements, and operating procedures.
