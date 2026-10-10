import { useState, type FormEvent } from "react";
import { ArrowUpRight, Check, CircuitBoard, FlaskConical, Search, ShieldAlert } from "lucide-react";
import type { FaultAnalysisInput, TaskMode } from "../types";

interface ModeFormProps {
  onSubmit: (input: FaultAnalysisInput) => Promise<void>;
  submitting: boolean;
}

const modes: { id: TaskMode; title: string; description: string; icon: typeof ShieldAlert; status: string }[] = [
  { id: "fault_analysis", title: "故障现象分析", description: "从症状、工况与证据出发，生成可核验的故障假设。", icon: ShieldAlert, status: "可用" },
  { id: "technical_research", title: "技术调研与实验设计", description: "整理公开资料，比较技术路线并形成实验步骤。", icon: FlaskConical, status: "即将开放" },
  { id: "case_retrieval", title: "检修案例检索", description: "基于已导入的手册与案例查找相似故障。", icon: Search, status: "即将开放" },
];

export default function ModeForm({ onSubmit, submitting }: ModeFormProps) {
  const [equipmentType, setEquipmentType] = useState("");
  const [symptom, setSymptom] = useState("");
  const [conditions, setConditions] = useState("");
  const [faultCode, setFaultCode] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!equipmentType.trim() || !symptom.trim()) {
      setError("请填写设备类型和故障现象。");
      return;
    }
    if (!conditions.trim()) {
      setError("请补充运行工况，例如负载、电网电压或环境温度。");
      return;
    }
    setError("");
    const operatingConditions: Record<string, string> = {};
    conditions.split(/[，,\n;]/).map((item) => item.trim()).filter(Boolean).forEach((item, index) => {
      operatingConditions[`context_${index + 1}`] = item;
    });
    await onSubmit({ equipment_type: equipmentType.trim(), symptom: symptom.trim(), operating_conditions: operatingConditions, fault_code: faultCode.trim() || undefined });
  }

  return (
    <div className="mode-form-wrap">
      <div className="mode-grid" role="group" aria-label="选择工作模式">
        {modes.map(({ id, title, description, icon: Icon, status }, index) => (
          <button key={id} type="button" className={`mode-card ${index === 0 ? "selected" : "disabled"}`} disabled={id !== "fault_analysis"} aria-label={title}>
            <span className="mode-icon"><Icon size={18} /></span>
            <span className="mode-card-copy"><strong>{title}</strong><small>{description}</small></span>
            <span className={`mode-status ${index === 0 ? "ready" : ""}`}>{index === 0 && <Check size={12} />}{status}</span>
          </button>
        ))}
      </div>
      <form className="fault-form" onSubmit={submit}>
        <div className="section-kicker"><span>01 / 分析请求</span><span className="required-note"><i />必填信息</span></div>
        <div className="form-grid">
          <label className="field"><span>设备类型 <b>*</b></span><div className="input-with-icon"><CircuitBoard size={16} /><input aria-label="设备类型" value={equipmentType} onChange={(e) => setEquipmentType(e.target.value)} placeholder="例如：光伏并网逆变器" /></div></label>
          <label className="field"><span>故障码 <em>可选</em></span><input aria-label="故障码" value={faultCode} onChange={(e) => setFaultCode(e.target.value)} placeholder="例如：F021" /></label>
          <label className="field field-wide"><span>故障现象 <b>*</b></span><textarea aria-label="故障现象" rows={3} value={symptom} onChange={(e) => setSymptom(e.target.value)} placeholder="描述告警、波形变化、发生时序或可观察到的异常……" /></label>
          <label className="field field-wide"><span>运行工况 <b>*</b><em>逗号分隔多个条件</em></span><textarea aria-label="运行工况" rows={2} value={conditions} onChange={(e) => setConditions(e.target.value)} placeholder="负载 50%，环境温度 35℃，并网电压 400V" /></label>
        </div>
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="form-bottom">
          <p><ShieldAlert size={15} /> 分析结果仅用于辅助判断，现场操作请遵循设备安全规程。</p>
          <button className="primary-button" type="submit" disabled={submitting}>{submitting ? "正在提交…" : "开始分析"}<ArrowUpRight size={16} /></button>
        </div>
      </form>
    </div>
  );
}
