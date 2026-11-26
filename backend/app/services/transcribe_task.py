"""转写任务转换领域服务：对转码完成的分片执行语音转文本转换，产出转写产出数据集供语音分段与置信复核消费。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class TranscribeTaskService(BaseModuleService):
    MODULE = "transcribe_task"
    ENTITY = "转写任务"
    OPERATION_TERM = "转换"
    LIST_FIELDS = ["任务编号", "关联分片", "转写引擎", "语言设定", "输出文本行数", "平均置信度", "转写耗时", "任务批次"]
    REQUIRED_FIELDS = ["任务编号", "关联分片", "转写引擎"]
    STATUS_ORDER = ["待转写", "转写中", "已产出", "产出异常"]
    ACTION_RULES = {"启动转写": "转写中", "确认产出": "已产出", "登记异常": "产出异常"}
    NEGATIVE_ACTIONS = []

    def produce_transcription(
        self, task_id: str, line_count: int, avg_confidence: float,
        elapsed_seconds: int,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """记录转写产出：更新文本行数、置信度与产出状态。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("任务编号") == task_id:
                r["输出文本行数"] = str(line_count)
                r["平均置信度"] = str(avg_confidence)
                r["转写耗时"] = str(elapsed_seconds)
                r["status"] = "已产出"
                r["pending"] = False
                return True, f"任务 {task_id} 已产出 {line_count} 行", dict(r)
        return False, f"任务 {task_id} 不存在", None

    def get_by_batch(self, batch_id: str) -> list[dict[str, Any]]:
        """获取指定批次的所有转写任务。"""
        rows = store.rows(self.MODULE)
        return [r for r in rows if r.get("任务批次") == batch_id]

    def batch_avg_confidence(self, batch_id: str) -> float:
        """计算指定批次的平均置信度。"""
        tasks = self.get_by_batch(batch_id)
        confs = [
            float(t.get("平均置信度", "0"))
            for t in tasks
            if t.get("平均置信度") and float(t.get("平均置信度", "0")) > 0
        ]
        return sum(confs) / len(confs) if confs else 0.0

    def get_low_confidence_tasks(self, threshold: float) -> list[dict[str, Any]]:
        """获取低于指定置信度阈值的已产出任务。"""
        rows = store.rows(self.MODULE)
        return [
            r for r in rows
            if r.get("status") == "已产出"
            and float(r.get("平均置信度", "0")) < threshold
        ]

    def get_adoption_receipt(self, task_id: str) -> dict[str, Any] | None:
        """从转写任务查采纳分片回执：实际所用字节与另一份录音的出处。"""
        from app.services.missing_scan import get_missing_scan_service
        return get_missing_scan_service().get_receipt_by_task(task_id)