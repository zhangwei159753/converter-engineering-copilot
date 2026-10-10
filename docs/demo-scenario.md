# Local demo scenario

This walkthrough uses only synthetic demonstration cases. It requires a configured DeepSeek API key for model calls; FastEmbed downloads the local `BAAI/bge-small-zh-v1.5` embedding model on first use. The examples are not real incidents, manufacturer data, or validated maintenance outcomes.

## 1. Start the application

Use the local setup in the root [README](../README.md), or start the Docker Compose services. Check `/api/health` and `/api/ready`; the latter checks that SQLite can acquire a write lock within one second and that the worker is running.

Uploads are limited by default to 20 MiB, 200 PDF pages, 10,000 CSV rows, and 2,000 chunks. The local source ledger reports validation and indexing state. A completed import only means the synthetic source passed file checks and was indexed; it does not make the example a real incident or validate a maintenance outcome. The CSV rows retain the `synthetic_demo_fixture` marker, which the workbench displays explicitly.

## 2. Import synthetic maintenance rows

In the workbench, choose **导入技术资料** and upload [`examples/demo_maintenance_cases.csv`](../examples/demo_maintenance_cases.csv). The importer indexes each CSV row and stores one structured case per row. The `record_type` column labels each row `synthetic_demo_fixture`; the source attribution and explanation are also stated in this guide and the evaluation records.

## 3. Retrieve a similar case

Choose **检修案例检索**, enter `光伏并网逆变器` as the equipment type and `启动升载过程中出现直流母线过压告警` as the symptom, then submit. The task page shows queued/running/completed events. The report lists matching fields, differences, recorded validation/handling text, similarity, and the imported row citation. Similarity is not a cause probability.

## 4. Run a fault analysis

Choose **故障现象分析**, enter the same converter and symptom, and provide load, ambient temperature, and grid voltage. A complete retrieval path can use the imported rows as local evidence; optional Tavily adds web sources. The report shows candidate hypotheses, suggested checks, source identifiers, and quality warnings. If conditions are omitted, the workflow asks for clarification instead of drawing a conclusion.

For a useful negative-path demonstration, stop the API or remove a required provider key and submit a task: the UI displays a task/API failure. With the service running but no matching source, the report is partial and contains no generated hypothesis.

## 5. Inspect report evidence and export

Open a citation identifier to inspect its source excerpt and locator. Use **导出 Markdown** to download the structured report. The frontend task page reconnects to progress events through `Last-Event-ID`, so refreshing does not create a second analysis task.

## 6. Review an evaluation run

Run `uv run python evals/converter_copilot/run_eval.py --mode fault_analysis` with a configured provider key. The command writes a metric summary and a separate review bundle under `.data/evaluation_results/`. In the Web app, open **人工评测**, import the `*-review.json` file, inspect each claim against its captured excerpt and locator, assign evidence labels, select retrieval gold IDs, enter a local reviewer identifier, and save. Revisions appear in the example's history. Empty gold labels remain `null`/`insufficient_labels`; labels should reflect a documented review of the snapshot, not assumptions about field conditions.

## What to say in a portfolio demo

Describe these rows and any generated output as synthetic and model-assisted. Do not claim measured recall, field validation, or maintenance savings. The evidence check validates traceable citation IDs and conflict flags, not whether an engineering claim is objectively correct. A qualified engineer should confirm any proposed diagnosis against authorized manuals, actual measurements, and operating procedures.

Human review creates an auditable annotation, not proof of field correctness or maintenance benefit. Do not substitute it for equipment manuals, field measurements, or site safety procedures.

## 7. Run the work-order assistance flow

Open **工单研判** in the left navigation. Select a synthetic ticket such as `WO-2026-0418`; inspect its model, firmware, alarm code, operating context, attachments, and missing-field warning. Choose **发起辅助研判** to submit the mapped fields to the existing `fault_analysis` workflow. The browser opens the task progress page and the report, when ready, links back to the source ticket.

Return to the work order and select the completed analysis. Open its evidence report, then record a reviewer name, a verdict (**接受建议**, **需补充信息**, or **不采纳**), and an optional note. Re-reviewing appends a new version to the local audit history. The synthetic ticket source is read-only; the review does not update a CMMS, issue equipment commands, or establish field correctness.
