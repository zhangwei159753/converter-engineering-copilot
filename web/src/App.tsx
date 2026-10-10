import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./components/AppShell";
import ReportPage from "./pages/ReportPage";
import TaskPage from "./pages/TaskPage";
import WorkspacePage from "./pages/WorkspacePage";
import EvaluationPage from "./pages/EvaluationPage";
import WorkOrdersPage from "./pages/WorkOrdersPage";

export default function App() {
  return <Routes><Route element={<AppShell />}><Route path="/" element={<WorkspacePage />} /><Route path="/work-orders" element={<WorkOrdersPage />} /><Route path="/evaluations" element={<EvaluationPage />} /><Route path="/tasks/:taskId" element={<TaskPage />} /><Route path="/tasks/:taskId/report" element={<ReportPage />} /><Route path="*" element={<Navigate to="/" replace />} /></Route></Routes>;
}
