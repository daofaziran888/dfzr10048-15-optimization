"""缺片核对服务：批次缺片重扫与重投去重的服务层入口。

职责：
- 持有批次缺片核对器，并把补采单意图落到失败重投模块（重投页可见）
- 核对状态落盘为 JSON 快照，重启后恢复，重扫与重报采纳不重复
- 确认采纳前校验转写任务存在，回执供转写任务侧查询
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.pipeline.missing_fragment_scanner import MissingFragmentScanner
from app.services.fail_redeliver import FailRedeliverService
from app.store import store

DEFAULT_STATE_FILE = Path(
    os.environ.get(
        "MISSING_SCAN_STATE_FILE",
        Path(__file__).resolve().parents[2] / "data" / "missing_scan_state.json",
    )
)


class FailRedeliverOrderGateway:
    """补采单网关实现：把未结单去重与结算落到失败重投模块。"""

    def __init__(self, service: FailRedeliverService | None = None) -> None:
        self._service = service or FailRedeliverService()

    def ensure_open_order(self, batch_id: str, seq: int, shard_id: str) -> str:
        entry, _ = self._service.ensure_open_order(batch_id, seq, shard_id)
        return str(entry["重投编号"])

    def is_order_open(self, order_id: str) -> bool:
        return self._service.is_order_open(order_id)

    def settle_order(self, order_id: str, conclusion: str) -> bool:
        ok, _ = self._service.settle_order(order_id, conclusion)
        return ok


class MissingScanService:
    """缺片核对服务：批量检查、领取、到片上报、确认采纳与回执查询。"""

    def __init__(
        self,
        storage_path: str | Path | None = None,
        scanner: MissingFragmentScanner | None = None,
    ) -> None:
        self._storage_path = Path(storage_path) if storage_path else DEFAULT_STATE_FILE
        self._scanner = scanner or MissingFragmentScanner(
            order_gateway=FailRedeliverOrderGateway(),
        )
        self._load()

    # ---- 持久化：重启后恢复核对状态 ----

    def _load(self) -> None:
        if not self._storage_path.exists():
            return
        try:
            data = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if isinstance(data, dict):
            self._scanner.restore(data)

    def _save(self) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._storage_path.with_suffix(self._storage_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(self._scanner.snapshot(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self._storage_path)

    # ---- 批量检查 ----

    def check_batch(
        self, batch_id: str, expected: list[int] | None, arrived: list[int] | None = None,
    ) -> tuple[bool, str, dict[str, Any]]:
        batch_id = (batch_id or "").strip()
        if not batch_id:
            return False, "批次号不能为空", {}
        if expected is None and not self._scanner.batch_result(batch_id):
            return False, f"批次 {batch_id} 首次检查必须提供应收片号", {}
        result = self._scanner.check_batch(batch_id, expected, arrived)
        self._save()
        missing = result.get("missing", [])
        return True, (
            f"批次 {batch_id} 第 {result.get('check_count')} 次检查："
            f"应收 {len(result.get('expected', []))} 片，仍缺 {len(missing)} 片 {missing}"
        ), result

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        result = self._scanner.batch_result((batch_id or "").strip())
        return result or None

    # ---- 领取 ----

    def claim_item(self, batch_id: str, seq: int) -> tuple[bool, str, dict[str, Any] | None]:
        ok, message, item = self._scanner.claim_item(batch_id.strip(), int(seq))
        if ok:
            self._save()
        return ok, message, item

    # ---- 到片上报 ----

    def report_arrival(
        self, batch_id: str, seq: int, source: str, byte_size: int = 0,
        digest: str = "", range_start: float = 0.0, range_end: float = 0.0,
        qualified: bool = True,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        source = (source or "").strip()
        if not source:
            return False, "到片来源不能为空", None
        ok, message, item = self._scanner.report_arrival(
            batch_id.strip(), int(seq), source, byte_size, digest,
            range_start, range_end, qualified,
        )
        if ok:
            self._save()
        return ok, message, item

    # ---- 确认采纳 ----

    def confirm_adoption(
        self, batch_id: str, seq: int, adopt_source: str | None = None,
        task_id: str | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None, bool]:
        task_id = (task_id or "").strip() or None
        if task_id and not self._task_exists(task_id):
            return False, f"转写任务 {task_id} 不存在，无法登记采纳回执", None, False
        ok, message, receipt, created = self._scanner.confirm_adoption(
            batch_id.strip(), int(seq), adopt_source, task_id,
        )
        if ok and created:
            self._save()
        return ok, message, receipt, created

    def _task_exists(self, task_id: str) -> bool:
        return any(
            r.get("任务编号") == task_id for r in store.rows("transcribe_task")
        )

    # ---- 回执查询 ----

    def get_receipt(self, batch_id: str, seq: int) -> dict[str, Any] | None:
        return self._scanner.get_receipt(batch_id.strip(), int(seq))

    def get_receipt_by_task(self, task_id: str) -> dict[str, Any] | None:
        """从转写任务查采纳回执：实际所用字节与另一份录音的出处。"""
        return self._scanner.get_receipt_by_task((task_id or "").strip())


_service: MissingScanService | None = None


def get_missing_scan_service() -> MissingScanService:
    """缺片核对服务单例：路由层按需获取，状态落盘后重启可恢复。"""
    global _service
    if _service is None:
        _service = MissingScanService()
    return _service
