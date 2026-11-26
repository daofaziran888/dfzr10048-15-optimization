"""缺片核对接口：批次缺片批量检查、领取、到片上报、确认采纳与回执查询。

独立于通用资源路由：缺片核对跨媒体分片、失败重投与转写任务三个模块，
按批次维度编排，不落在单一模块表上。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.missing_scan import get_missing_scan_service

router = APIRouter(prefix="/api/missing-scan", tags=["缺片核对"])


class CheckPayload(BaseModel):
    batch_id: str
    expected: list[int] | None = Field(default=None)
    arrived: list[int] = Field(default_factory=list)


class ArrivalPayload(BaseModel):
    source: str
    byte_size: int = 0
    digest: str = ""
    range_start: float = 0.0
    range_end: float = 0.0
    qualified: bool = True


class ConfirmPayload(BaseModel):
    adopt_source: str | None = None
    task_id: str | None = None


@router.post("/check")
def check_batch(payload: CheckPayload) -> dict[str, Any]:
    """批量检查片号：登记应收与已到片号，重算缺片并逐条关联未结补采单。"""
    ok, message, result = get_missing_scan_service().check_batch(
        payload.batch_id, payload.expected, payload.arrived,
    )
    return {"ok": ok, "message": message, "result": result}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    """整组检查结果：应收、已到、仍缺与逐条缺片明细，供分别领取。"""
    result = get_missing_scan_service().get_batch(batch_id)
    if result is None:
        return {"ok": False, "message": f"批次 {batch_id} 不存在，请先做批量检查", "result": None}
    return {"ok": True, "message": "ok", "result": result}


@router.post("/batches/{batch_id}/items/{seq}/claim")
def claim_item(batch_id: str, seq: int) -> dict[str, Any]:
    """领取缺片：进入补采中并复用同一张未结补采单。"""
    ok, message, item = get_missing_scan_service().claim_item(batch_id, seq)
    return {"ok": ok, "message": message, "item": item}


@router.post("/batches/{batch_id}/items/{seq}/arrivals")
def report_arrival(batch_id: str, seq: int, payload: ArrivalPayload) -> dict[str, Any]:
    """上报到片：同来源同摘要不重复；多份不同摘要进入待确认。"""
    ok, message, item = get_missing_scan_service().report_arrival(
        batch_id, seq, payload.source, payload.byte_size, payload.digest,
        payload.range_start, payload.range_end, payload.qualified,
    )
    return {"ok": ok, "message": message, "item": item}


@router.post("/batches/{batch_id}/items/{seq}/confirm")
def confirm_adoption(batch_id: str, seq: int, payload: ConfirmPayload) -> dict[str, Any]:
    """确认采纳：选定一份到片，结算补采单并产出采纳回执；重报不重复。"""
    ok, message, receipt, created = get_missing_scan_service().confirm_adoption(
        batch_id, seq, payload.adopt_source, payload.task_id,
    )
    return {"ok": ok, "message": message, "receipt": receipt, "created": created}


@router.get("/batches/{batch_id}/items/{seq}/receipt")
def get_receipt(batch_id: str, seq: int) -> dict[str, Any]:
    """按批次与片号查采纳回执。"""
    receipt = get_missing_scan_service().get_receipt(batch_id, seq)
    if receipt is None:
        return {"ok": False, "message": f"批次 {batch_id} 片号 {seq} 暂无采纳回执", "receipt": None}
    return {"ok": True, "message": "ok", "receipt": receipt}


@router.get("/receipts/by-task/{task_id}")
def get_receipt_by_task(task_id: str) -> dict[str, Any]:
    """从转写任务查采纳回执：实际所用字节与另一份录音的出处。"""
    receipt = get_missing_scan_service().get_receipt_by_task(task_id)
    if receipt is None:
        return {"ok": False, "message": f"转写任务 {task_id} 暂无采纳回执", "receipt": None}
    return {"ok": True, "message": "ok", "receipt": receipt}
