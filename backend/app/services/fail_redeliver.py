"""失败重投回填领域服务：登记转码或转写失败分片的重投单，只处理仍缺失分片不覆盖已成功产出，产出重投单数据集供水位推进消费。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class FailRedeliverService(BaseModuleService):
    MODULE = "fail_redeliver"
    ENTITY = "重投单"
    OPERATION_TERM = "回填"
    LIST_FIELDS = ["重投编号", "关联分片", "失败阶段", "失败原因", "重投次数", "重投策略", "重投结论", "重投时间"]
    REQUIRED_FIELDS = ["重投编号", "关联分片", "失败阶段"]
    STATUS_ORDER = ["待重投", "重投中", "已补齐", "重投放弃"]
    ACTION_RULES = {"启动重投": "重投中", "确认补齐": "已补齐", "放弃重投": "重投放弃"}
    NEGATIVE_ACTIONS = []

    def register_redeliver(
        self, redeliver_id: str, shard_id: str, fail_stage: str,
        fail_reason: str, strategy: str,
    ) -> tuple[dict[str, Any] | None, str]:
        """登记失败重投单。"""
        rows = store.rows(self.MODULE)
        entry = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "重投编号": redeliver_id, "关联分片": shard_id,
            "失败阶段": fail_stage, "失败原因": fail_reason,
            "重投次数": "0", "重投策略": strategy,
            "重投结论": "-", "重投时间": "-",
            "status": "待重投", "pending": True, "abnormal": False,
        }
        rows.append(entry)
        return entry, f"重投单 {redeliver_id} 已登记"

    def execute_redeliver(
        self, redeliver_id: str, success: bool, conclusion: str,
    ) -> tuple[bool, str]:
        """执行重投并记录结论。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("重投编号") == redeliver_id:
                count = int(r.get("重投次数", "0")) + 1
                r["重投次数"] = str(count)
                r["重投时间"] = "2026-11-01"
                if success:
                    r["重投结论"] = conclusion
                    r["status"] = "已补齐"
                    r["pending"] = False
                    return True, f"重投 {redeliver_id} 已补齐"
                else:
                    r["重投结论"] = conclusion
                    r["status"] = "待重投" if count < 3 else "重投放弃"
                    r["pending"] = count < 3
                    return False, f"重投 {redeliver_id} 第 {count} 次失败"
        return False, f"重投单 {redeliver_id} 不存在"

    def get_by_shard(self, shard_id: str) -> list[dict[str, Any]]:
        """获取指定分片的所有重投单。"""
        rows = store.rows(self.MODULE)
        return [r for r in rows if r.get("关联分片") == shard_id]

    def get_pending(self) -> list[dict[str, Any]]:
        """获取所有待重投的单据。"""
        rows = store.rows(self.MODULE)
        return [r for r in rows if r.get("status") == "待重投"]

    OPEN_STATUSES = ("待重投", "重投中")

    def ensure_open_order(
        self, batch_id: str, seq: int, shard_id: str,
    ) -> tuple[dict[str, Any], bool]:
        """确保指定批次缺片存在一张未结补采单：已有未结单直接复用，不重复开单。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("关联分片") == shard_id and r.get("status") in self.OPEN_STATUSES:
                return r, False
        base_no = f"FRDL-{batch_id}-S{seq:03d}"
        existing = {r.get("重投编号") for r in rows}
        order_no = base_no
        suffix = 1
        while order_no in existing:
            suffix += 1
            order_no = f"{base_no}-R{suffix}"
        entry, _ = self.register_redeliver(
            order_no, shard_id, "缺片补采", f"批次{batch_id}缺片{seq}", "仅补缺",
        )
        return entry, True

    def is_order_open(self, order_no: str) -> bool:
        """判断补采单是否仍未结（待重投或重投中）。"""
        rows = store.rows(self.MODULE)
        return any(
            r.get("重投编号") == order_no and r.get("status") in self.OPEN_STATUSES
            for r in rows
        )

    def settle_order(self, order_no: str, conclusion: str) -> tuple[bool, str]:
        """结算补采单为已补齐；已结算的单据重复结算直接返回，不重复改动。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("重投编号") == order_no:
                if r.get("status") == "已补齐":
                    return True, f"补采单 {order_no} 已结算，未重复处理"
                r["重投结论"] = conclusion
                r["重投时间"] = "2026-11-26"
                r["status"] = "已补齐"
                r["pending"] = False
                return True, f"补采单 {order_no} 已结算"
        return False, f"补采单 {order_no} 不存在"