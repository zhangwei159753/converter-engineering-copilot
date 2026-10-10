import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowDownRight, ArrowUpRight, CheckCircle2, CircleAlert, FilePlus2, Gauge, Lightbulb, RefreshCw, Trash2, UploadCloud, Zap } from "lucide-react";
import { createTask, deleteImport, importCases, listImports, listTasks, repairImport } from "../api/client";
import ModeForm from "../components/ModeForm";
import type { ImportSummary, TaskRecord } from "../types";

function shortDate(value: string) {
  return new Date(value).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function importStatusLabel(status: ImportSummary["status"]) {
  return ({
    validated: "待处理",
    indexing: "索引中",
    complete: "导入完成",
    failed: "需要重试",
    needs_repair: "需要修复",
    deleting: "删除未完成",
    deleted: "已删除",
  } as const)[status];
}

function importGuidance(record: ImportSummary) {
  switch (record.error_code) {
    case "source_missing": return "留存原文件缺失，请重新上传后再修复。";
    case "source_invalid":
    case "source_mismatch": return "原文件校验未通过，请重新上传原始文件。";
    case "vectors_missing": return "检测到向量索引缺失，可重放导入恢复索引。";
    case "cases_incomplete": return "检测到案例记录不完整，可重放导入恢复案例。";
    case "delete_incomplete": return "删除中断，可继续删除剩余数据。";
    case "import_replay_failed": return "导入重放未完成，请检查本地索引后重试。";
    default: return record.error_code ? `处理状态：${record.error_code}` : "";
  }
}

export default function WorkspacePage() {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [imports, setImports] = useState<ImportSummary[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [busyImportId, setBusyImportId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refreshImports = useCallback(async () => {
    try {
      setImports(await listImports());
    } catch {
      setImports([]);
    }
  }, []);

  useEffect(() => {
    listTasks().then(setTasks).catch(() => setTasks([]));
    void refreshImports();
  }, [refreshImports]);

  async function submit(mode: "fault_analysis" | "technical_research" | "case_retrieval", input: Record<string, unknown>) {
    setSubmitting(true);
    setError("");
    try {
      const { task } = await createTask({ mode, inputs: input });
      navigate(`/tasks/${task.task_id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "任务创建失败，请稍后重试。");
    } finally {
      setSubmitting(false);
    }
  }

  async function upload(file?: File) {
    if (!file) return;
    setNotice("");
    setError("");
    try {
      const result = await importCases(file);
      setNotice(result.reused
        ? "已存在相同内容，复用现有导入记录。"
        : `已导入 ${result.chunk_count} 个文档片段、${result.imported_case_count} 条案例。`);
      await refreshImports();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "文档导入失败。");
      await refreshImports();
    }
  }

  async function repair(importId: string) {
    setBusyImportId(importId);
    setError("");
    try {
      const updated = await repairImport(importId);
      setImports((current) => current.map((item) => item.import_id === importId ? updated : item));
      setNotice(updated.status === "complete" ? "导入修复完成。" : "导入仍需处理，请按提示检查原文件或本地服务。 ");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "导入修复失败。");
    } finally {
      setBusyImportId("");
    }
  }

  async function removeImport(record: ImportSummary) {
    const confirmed = window.confirm(`删除“${record.source_name}”及其本地索引和案例？此操作会删除留存文件，之后需要重新上传才能恢复。`);
    if (!confirmed) return;
    setBusyImportId(record.import_id);
    setError("");
    try {
      const deleted = await deleteImport(record.import_id);
      setImports((current) => current.filter((item) => item.import_id !== deleted.import_id));
      setNotice("导入记录及其本地数据已删除。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除导入失败。");
      await refreshImports();
    } finally {
      setBusyImportId("");
    }
  }

  return (
    <div className="workspace-page">
      <div className="workspace-heading"><div><div className="breadcrumb"><span>工作空间</span><ArrowDownRight size={13} /><span>分析工作台</span></div><h1>把现场现象，变成可核验的判断<span>.</span></h1><p>针对变流器研发与运维的证据驱动分析工作台</p></div><div className="heading-stamp"><span>WORKSPACE</span><strong>01</strong><small>POWER CONVERSION SYSTEMS</small></div></div>
      <div className="workspace-layout"><section className="primary-workspace"><div className="panel-caption"><div><span className="panel-index">A</span><span>新建工程任务</span></div><span className="caption-right"><i />系统就绪</span></div><ModeForm onSubmit={submit} submitting={submitting} />{error && <div className="inline-alert" role="alert">{error}</div>}{notice && <div className="inline-notice">{notice}</div>}</section>
        <aside className="workspace-aside"><div className="metric-card"><div className="metric-icon"><Gauge size={17} /></div><span className="section-kicker">SESSION STATUS</span><strong>工作空间已就绪</strong><p>本地数据库与检索目录可用，任务运行后会保留进度与报告。</p><div className="metric-footer"><span><i className="live-dot" />LOCAL MODE</span><span>01 / 03 MODES</span></div></div>
          <div className="aside-section"><div className="aside-title"><span>最近任务</span><span className="count-badge">{tasks.length.toString().padStart(2, "0")}</span></div>{tasks.length ? <div className="task-history">{tasks.slice(0, 5).map((task) => <button className="history-row" key={task.task_id} onClick={() => navigate(`/tasks/${task.task_id}`)}><span className={`history-mark ${task.status}`}><Zap size={13} /></span><span className="history-copy"><strong>{String(task.inputs.symptom ?? "变流器分析任务")}</strong><small>{task.mode === "fault_analysis" ? "故障现象分析" : task.mode} · {shortDate(task.created_at)} · 尝试 {task.attempt}/{task.max_attempts}</small></span><ArrowUpRight size={14} /></button>)}</div> : <div className="history-empty"><span className="empty-dash">—</span><p>这里会显示最近执行的工程任务。</p></div>}</div>
          <label className="import-card"><span className="import-icon"><UploadCloud size={18} /></span><span className="import-copy"><strong>导入技术资料</strong><small>PDF · Markdown · CSV</small></span><FilePlus2 size={16} /><input type="file" accept=".pdf,.md,.csv" aria-label="导入技术资料" onChange={(event) => upload(event.target.files?.[0])} /></label>
          <section className="import-ledger" aria-label="资料导入台账">
            <div className="import-ledger-head"><div><span>来源台账</span><small>本地副本 · 索引状态</small></div><span className="count-badge">{imports.length.toString().padStart(2, "0")}</span></div>
            {imports.length ? <div className="import-ledger-list">{imports.map((record) => {
              const needsSource = ["source_missing", "source_invalid", "source_mismatch"].includes(record.error_code ?? "");
              const canRepair = record.status !== "complete" && !needsSource;
              return <article className={`import-record ${record.status}`} key={record.import_id}>
                <div className="import-record-head"><span className={`import-state ${record.status}`}><i />{importStatusLabel(record.status)}</span><strong title={record.source_name}>{record.source_name}</strong></div>
                <p>{record.indexed_chunks}/{record.expected_chunk_count} 个片段 · {record.imported_cases} 条案例</p>
                {record.synthetic_demo && <span className="synthetic-note">合成演示数据，不是现场记录</span>}
                {importGuidance(record) && <div className="import-guidance" role="status"><CircleAlert size={13} />{importGuidance(record)}</div>}
                <div className="import-record-actions">
                  {canRepair && <button type="button" className="import-action" disabled={busyImportId === record.import_id} onClick={() => repair(record.import_id)}><RefreshCw size={13} />{record.status === "deleting" ? "继续删除" : "修复导入"}</button>}
                  {needsSource && <span className="reupload-hint">重新上传以恢复留存文件</span>}
                  <button type="button" className="import-action danger" disabled={busyImportId === record.import_id} aria-label="删除导入" onClick={() => removeImport(record)}><Trash2 size={13} />删除</button>
                </div>
              </article>;
            })}</div> : <div className="import-ledger-empty"><CheckCircle2 size={15} /><span>尚无导入资料，上传后会在这里显示校验和索引状态。</span></div>}
          </section>
          <div className="tip-card"><Lightbulb size={16} /><p><strong>分析建议</strong><br />补充负载、电网与环境信息，有助于排除工况相关因素。</p></div>
        </aside>
      </div>
    </div>
  );
}
