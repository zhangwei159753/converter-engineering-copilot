"""Read-only, deterministic synthetic work-order source for local demos."""

from pydantic import BaseModel, ConfigDict, Field


class WorkOrderRecord(BaseModel):
    """A maintenance ticket with explicit provenance and completeness metadata."""

    model_config = ConfigDict(frozen=True)

    work_order_id: str
    equipment_model: str
    serial_number: str
    firmware_version: str | None = None
    alarm_code: str
    symptom: str
    occurred_at: str
    operating_conditions: dict[str, str | float | int | None] = Field(default_factory=dict)
    attachment_names: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    severity: str
    status: str
    synthetic_demo: bool = True


_WORK_ORDERS = (
    WorkOrderRecord(
        work_order_id="WO-2026-0418",
        equipment_model="CPC-2500 / 690 V",
        serial_number="CPC25-8A-10482",
        firmware_version="v3.8.2",
        alarm_code="DC_OV_017",
        symptom="直流母线过压告警，降载后仍出现间歇性复归。",
        occurred_at="2026-10-08T09:42:00+08:00",
        operating_conditions={"active_power_kw": 1830, "dc_bus_voltage_v": 1124, "ambient_temperature_c": 41},
        attachment_names=["dc_bus_20261008.csv", "现场巡检记录.md"],
        missing_fields=["告警前 30 秒波形"],
        severity="high",
        status="open",
    ),
    WorkOrderRecord(
        work_order_id="WO-2026-0412",
        equipment_model="CPC-1800 / 690 V",
        serial_number="CPC18-7B-08831",
        firmware_version="v3.7.9",
        alarm_code="IGBT_TEMP_042",
        symptom="A 相功率模块温度高于 B/C 相约 14 ℃，风机转速反馈正常。",
        occurred_at="2026-10-07T14:16:00+08:00",
        operating_conditions={"active_power_kw": 1510, "module_temp_a_c": 88, "module_temp_b_c": 74},
        attachment_names=["thermal_snapshot.pdf"],
        missing_fields=["散热器入口温度", "同工况历史温度趋势"],
        severity="medium",
        status="investigating",
    ),
    WorkOrderRecord(
        work_order_id="WO-2026-0398",
        equipment_model="CPC-2500 / 690 V",
        serial_number="CPC25-8A-10210",
        firmware_version="v3.8.1",
        alarm_code="GRID_UV_006",
        symptom="并网侧欠压事件期间设备短时脱网，电网电压已恢复。",
        occurred_at="2026-10-05T03:27:00+08:00",
        operating_conditions={"active_power_kw": 2060, "grid_voltage_pu": 0.82, "event_duration_ms": 180},
        attachment_names=["grid_event_log.csv"],
        severity="medium",
        status="open",
    ),
    WorkOrderRecord(
        work_order_id="WO-2026-0371",
        equipment_model="CPC-1200 / 400 V",
        serial_number="CPC12-4C-05177",
        firmware_version="v3.6.4",
        alarm_code="FAN_SPEED_013",
        symptom="柜内冷却风机转速反馈偶发低于设定阈值，当前告警已消失。",
        occurred_at="2026-10-02T11:03:00+08:00",
        operating_conditions={"active_power_kw": 940, "cabinet_temperature_c": 36},
        attachment_names=[],
        missing_fields=["风机运行时长", "告警期间转速记录", "更换记录"],
        severity="low",
        status="resolved",
    ),
    WorkOrderRecord(
        work_order_id="WO-2026-0355",
        equipment_model="CPC-1800 / 690 V",
        serial_number="CPC18-7B-08609",
        firmware_version=None,
        alarm_code="INSULATION_WARN_021",
        symptom="绝缘监测预警在高湿天气后出现，复测结果未记录。",
        occurred_at="2026-09-29T06:51:00+08:00",
        operating_conditions={"active_power_kw": None, "relative_humidity_pct": 93},
        attachment_names=["巡检备注.md"],
        missing_fields=["固件版本", "绝缘复测值", "告警时有功功率"],
        severity="high",
        status="investigating",
    ),
)


def list_work_orders(query: str | None = None, status: str | None = None) -> list[WorkOrderRecord]:
    """Return matching synthetic work orders without external reads or writes."""
    normalized_query = query.strip().casefold() if query else ""
    normalized_status = status.strip().casefold() if status else ""
    records = _WORK_ORDERS
    if normalized_query:
        records = tuple(
            item for item in records
            if normalized_query in " ".join((
                item.work_order_id,
                item.equipment_model,
                item.serial_number,
                item.alarm_code,
                item.symptom,
            )).casefold()
        )
    if normalized_status:
        records = tuple(item for item in records if item.status == normalized_status)
    return list(records)


def get_work_order(work_order_id: str) -> WorkOrderRecord | None:
    """Return one synthetic work order by stable ID."""
    return next((item for item in _WORK_ORDERS if item.work_order_id == work_order_id), None)
