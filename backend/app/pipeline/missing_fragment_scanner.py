"""批次缺片核对器：独立批次核对已到片与补采单，未结单去重，产出采纳分片回执。

核心行为：
- 批次独立：异批同号互不影响，已到片与补采单只在本批次内核对
- 批量检查幂等：重复扫描不新增相同未结补采单
- 领取幂等：同一缺片重复领取复用同一张补采单
- 到片冲突：同区间不同摘要的多份到片进入待确认，由值班员确认采纳
- 采纳幂等：重报采纳返回既有回执，不重复结算补采单
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

SOURCE_NORMAL = "正常到片"
SOURCE_ORIGINAL = "原入口补报"
SOURCE_RECAPTURE = "补采回传"


def shard_id_of(batch_id: str, seq: int) -> str:
    """批次内缺片的分片标识：批次前缀保证异批同号互不抵消。"""
    return f"{batch_id}-S{seq:03d}"


def order_id_of(batch_id: str, seq: int) -> str:
    """补采单编号：按批次与片号确定，重扫/重启后复用同一编号去重。"""
    return f"FRDL-{batch_id}-S{seq:03d}"


def receipt_id_of(batch_id: str, seq: int) -> str:
    """采纳回执编号：按批次与片号确定，重报采纳时命中既有回执。"""
    return f"RCPT-{batch_id}-S{seq:03d}"


class OrderGateway(Protocol):
    """补采单网关：核对器只声明意图，单据落库由服务层实现。"""

    def ensure_open_order(self, batch_id: str, seq: int, shard_id: str) -> str:
        """确保指定缺片存在一张未结补采单，返回补采单编号。"""
        ...

    def is_order_open(self, order_id: str) -> bool:
        """判断补采单是否仍未结。"""
        ...

    def settle_order(self, order_id: str, conclusion: str) -> bool:
        """结算补采单；重复结算返回 False。"""
        ...


class InMemoryOrderGateway:
    """进程内补采单网关：供管线测试与缺省场景使用。"""

    def __init__(self) -> None:
        self._open_by_shard: dict[str, str] = {}
        self._open: dict[str, bool] = {}
        self._conclusions: dict[str, str] = {}

    def ensure_open_order(self, batch_id: str, seq: int, shard_id: str) -> str:
        existing = self._open_by_shard.get(shard_id)
        if existing and self._open.get(existing):
            return existing
        order_id = order_id_of(batch_id, seq)
        suffix = 1
        candidate = order_id
        while candidate in self._open and not self._open[candidate]:
            suffix += 1
            candidate = f"{order_id}-R{suffix}"
        self._open[candidate] = True
        self._open_by_shard[shard_id] = candidate
        return candidate

    def is_order_open(self, order_id: str) -> bool:
        return self._open.get(order_id, False)

    def settle_order(self, order_id: str, conclusion: str) -> bool:
        if order_id not in self._open or not self._open[order_id]:
            return False
        self._open[order_id] = False
        self._conclusions[order_id] = conclusion
        return True

    def open_count(self) -> int:
        return sum(1 for v in self._open.values() if v)

    def total_count(self) -> int:
        return len(self._open)


@dataclass
class ArrivalCandidate:
    """到片记录：正常到片、原入口补报或补采回传，带字节与摘要。"""

    source: str
    byte_size: int = 0
    digest: str = ""
    range_start: float = 0.0
    range_end: float = 0.0
    qualified: bool = True
    reported_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "byte_size": self.byte_size,
            "digest": self.digest,
            "range_start": self.range_start,
            "range_end": self.range_end,
            "qualified": self.qualified,
            "reported_at": self.reported_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArrivalCandidate":
        return cls(
            source=str(data.get("source", "")),
            byte_size=int(data.get("byte_size", 0)),
            digest=str(data.get("digest", "")),
            range_start=float(data.get("range_start", 0.0)),
            range_end=float(data.get("range_end", 0.0)),
            qualified=bool(data.get("qualified", True)),
            reported_at=str(data.get("reported_at", "")),
        )


@dataclass
class MissingItem:
    """缺片条目：记录领取状态、关联补采单与候选到片。"""

    seq: int
    status: str = "待领取"
    order_id: str | None = None
    candidates: list[ArrivalCandidate] = field(default_factory=list)
    receipt_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "status": self.status,
            "order_id": self.order_id,
            "candidate_count": len(self.candidates),
            "candidates": [c.to_dict() for c in self.candidates],
            "receipt_id": self.receipt_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MissingItem":
        return cls(
            seq=int(data.get("seq", 0)),
            status=str(data.get("status", "待领取")),
            order_id=data.get("order_id"),
            candidates=[ArrivalCandidate.from_dict(c) for c in data.get("candidates", [])],
            receipt_id=data.get("receipt_id"),
        )


@dataclass
class AdoptionReceipt:
    """采纳分片回执：转写任务实际所用字节与落选到片的出处。"""

    receipt_id: str
    batch_id: str
    seq: int
    task_id: str | None
    order_id: str | None
    adopted: ArrivalCandidate
    others: list[ArrivalCandidate]
    confirmed_at: str

    def to_dict(self) -> dict[str, Any]:
        others = []
        for c in self.others:
            entry = c.to_dict()
            if c.source == SOURCE_RECAPTURE and self.order_id:
                entry["provenance"] = f"{c.source}（补采单 {self.order_id}）"
            else:
                entry["provenance"] = c.source
            others.append(entry)
        return {
            "receipt_id": self.receipt_id,
            "batch_id": self.batch_id,
            "seq": self.seq,
            "task_id": self.task_id,
            "order_id": self.order_id,
            "adopted": self.adopted.to_dict(),
            "adopted_bytes": self.adopted.byte_size,
            "others": others,
            "confirmed_at": self.confirmed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AdoptionReceipt":
        others = []
        for c in data.get("others", []):
            c = dict(c)
            c.pop("provenance", None)
            others.append(ArrivalCandidate.from_dict(c))
        return cls(
            receipt_id=str(data.get("receipt_id", "")),
            batch_id=str(data.get("batch_id", "")),
            seq=int(data.get("seq", 0)),
            task_id=data.get("task_id"),
            order_id=data.get("order_id"),
            adopted=ArrivalCandidate.from_dict(data.get("adopted", {})),
            others=others,
            confirmed_at=str(data.get("confirmed_at", "")),
        )


@dataclass
class BatchState:
    """单批次核对状态：应收片号、已到片与缺片条目。"""

    batch_id: str
    expected: set[int] = field(default_factory=set)
    check_count: int = 0
    arrivals: dict[int, ArrivalCandidate] = field(default_factory=dict)
    items: dict[int, MissingItem] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "expected": sorted(self.expected),
            "check_count": self.check_count,
            "arrivals": {str(seq): a.to_dict() for seq, a in self.arrivals.items()},
            "items": {str(seq): item.to_dict() for seq, item in self.items.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BatchState":
        return cls(
            batch_id=str(data.get("batch_id", "")),
            expected={int(s) for s in data.get("expected", [])},
            check_count=int(data.get("check_count", 0)),
            arrivals={int(seq): ArrivalCandidate.from_dict(a) for seq, a in data.get("arrivals", {}).items()},
            items={int(seq): MissingItem.from_dict(i) for seq, i in data.get("items", {}).items()},
        )


class MissingFragmentScanner:
    """批次缺片核对器：批量检查、领取、到片冲突与采纳回执。"""

    ITEM_PENDING = "待领取"
    ITEM_CLAIMED = "补采中"
    ITEM_CONFLICT = "待确认"
    ITEM_ADOPTED = "已采纳"
    ITEM_ARRIVED = "已到片"
    UNSETTLED = (ITEM_PENDING, ITEM_CLAIMED, ITEM_CONFLICT)

    def __init__(self, order_gateway: OrderGateway | None = None, clock: Any = None) -> None:
        self._gateway: OrderGateway = order_gateway or InMemoryOrderGateway()
        self._clock = clock or (lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self._batches: dict[str, BatchState] = {}
        self._receipts: dict[str, AdoptionReceipt] = {}

    # ---- 批量检查 ----

    def check_batch(
        self, batch_id: str, expected: list[int] | None, arrived: list[int] | None = None,
    ) -> dict[str, Any]:
        """批量检查片号：登记应收与已到，重算缺片并逐条关联未结补采单。

        幂等：重复扫描复用既有缺片条目与未结补采单，不重复登记。
        """
        batch = self._batches.setdefault(batch_id, BatchState(batch_id=batch_id))
        batch.check_count += 1
        if expected is not None:
            batch.expected = {int(s) for s in expected}
        for seq in arrived or []:
            self._mark_arrived(batch, int(seq), ArrivalCandidate(
                source=SOURCE_NORMAL, reported_at=self._clock(),
            ))
        for seq in self._missing_seqs(batch):
            item = batch.items.get(seq)
            if item is None:
                item = MissingItem(seq=seq)
                batch.items[seq] = item
            self._ensure_order(batch_id, item)
        return self.batch_result(batch_id)

    def _mark_arrived(self, batch: BatchState, seq: int, arrival: ArrivalCandidate) -> None:
        if seq in batch.arrivals:
            return
        item = batch.items.get(seq)
        if item is not None and item.status in self.UNSETTLED and item.candidates:
            # 已有候选到片在走确认流程，整批到片不覆盖
            return
        batch.arrivals[seq] = arrival
        if item is not None and item.status in self.UNSETTLED:
            item.status = self.ITEM_ARRIVED
            if item.order_id:
                self._gateway.settle_order(item.order_id, "原入口到片，无需补采")

    def _missing_seqs(self, batch: BatchState) -> list[int]:
        return sorted(
            seq for seq in batch.expected
            if seq not in batch.arrivals and not self._item_settled(batch.items.get(seq))
        )

    def _item_settled(self, item: MissingItem | None) -> bool:
        return item is not None and item.status in (self.ITEM_ADOPTED, self.ITEM_ARRIVED)

    def _ensure_order(self, batch_id: str, item: MissingItem) -> str:
        if item.order_id and self._gateway.is_order_open(item.order_id):
            return item.order_id
        item.order_id = self._gateway.ensure_open_order(
            batch_id, item.seq, shard_id_of(batch_id, item.seq),
        )
        return item.order_id

    # ---- 领取 ----

    def claim_item(self, batch_id: str, seq: int) -> tuple[bool, str, dict[str, Any] | None]:
        """领取缺片：进入补采中并复用同一张未结补采单，重复领取不重复开单。"""
        batch = self._batches.get(batch_id)
        if batch is None:
            return False, f"批次 {batch_id} 不存在，请先做批量检查", None
        item = batch.items.get(int(seq))
        if item is None or item.status not in self.UNSETTLED:
            return False, f"批次 {batch_id} 片号 {seq} 不在缺片清单或已了结", None
        self._ensure_order(batch_id, item)
        if item.status == self.ITEM_PENDING:
            item.status = self.ITEM_CLAIMED
        return True, f"已领取批次 {batch_id} 缺片 {seq}，补采单 {item.order_id}", item.to_dict()

    # ---- 到片上报 ----

    def report_arrival(
        self, batch_id: str, seq: int, source: str, byte_size: int = 0,
        digest: str = "", range_start: float = 0.0, range_end: float = 0.0,
        qualified: bool = True,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """上报到片：同来源同摘要重报不重复；多份不同摘要进入待确认。"""
        batch = self._batches.get(batch_id)
        if batch is None:
            return False, f"批次 {batch_id} 不存在，请先做批量检查", None
        item = batch.items.get(int(seq))
        if item is None or item.status not in self.UNSETTLED:
            return False, f"批次 {batch_id} 片号 {seq} 不在缺片清单或已了结", None
        for c in item.candidates:
            if c.source == source and c.digest == digest:
                return True, "相同到片已登记，未重复记录", item.to_dict()
        item.candidates.append(ArrivalCandidate(
            source=source, byte_size=int(byte_size), digest=digest,
            range_start=float(range_start), range_end=float(range_end),
            qualified=bool(qualified), reported_at=self._clock(),
        ))
        digests = {c.digest for c in item.candidates if c.digest}
        if len(item.candidates) >= 2 and len(digests) >= 2:
            item.status = self.ITEM_CONFLICT
        elif item.status == self.ITEM_PENDING:
            item.status = self.ITEM_CLAIMED
        return True, f"批次 {batch_id} 片号 {seq} 到片已登记（{source}）", item.to_dict()

    # ---- 确认采纳 ----

    def confirm_adoption(
        self, batch_id: str, seq: int, adopt_source: str | None = None,
        task_id: str | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None, bool]:
        """确认采纳：选定一份到片，结算补采单并产出采纳回执。

        返回 (是否成功, 消息, 回执, 是否新建)；重报采纳命中既有回执，不重复结算。
        """
        batch = self._batches.get(batch_id)
        if batch is None:
            return False, f"批次 {batch_id} 不存在，请先做批量检查", None, False
        item = batch.items.get(int(seq))
        if item is None:
            return False, f"批次 {batch_id} 片号 {seq} 不在缺片清单", None, False
        if item.receipt_id and item.receipt_id in self._receipts:
            receipt = self._receipts[item.receipt_id]
            return True, (
                f"批次 {batch_id} 片号 {seq} 已有采纳回执 {receipt.receipt_id}，未重复登记"
            ), receipt.to_dict(), False
        if item.status not in self.UNSETTLED:
            return False, f"批次 {batch_id} 片号 {seq} 已了结，无需确认采纳", None, False
        if not item.candidates:
            return False, f"批次 {batch_id} 片号 {seq} 尚无候选到片，无法确认采纳", None, False
        adopted = self._pick_candidate(item.candidates, adopt_source)
        if adopted is None:
            return False, f"未找到来源为「{adopt_source}」的候选到片", None, False
        others = [c for c in item.candidates if c is not adopted]
        receipt = AdoptionReceipt(
            receipt_id=receipt_id_of(batch_id, int(seq)),
            batch_id=batch_id, seq=int(seq), task_id=task_id,
            order_id=item.order_id, adopted=adopted, others=others,
            confirmed_at=self._clock(),
        )
        self._receipts[receipt.receipt_id] = receipt
        item.status = self.ITEM_ADOPTED
        item.receipt_id = receipt.receipt_id
        batch.arrivals[int(seq)] = adopted
        if item.order_id:
            self._gateway.settle_order(item.order_id, f"采纳{adopted.source}")
        return True, (
            f"批次 {batch_id} 片号 {seq} 已采纳{adopted.source}，回执 {receipt.receipt_id}"
        ), receipt.to_dict(), True

    def _pick_candidate(
        self, candidates: list[ArrivalCandidate], adopt_source: str | None,
    ) -> ArrivalCandidate | None:
        if adopt_source:
            for c in candidates:
                if c.source == adopt_source:
                    return c
            return None
        for c in candidates:
            if c.source == SOURCE_ORIGINAL and c.qualified:
                return c
        for c in candidates:
            if c.qualified:
                return c
        return candidates[0]

    # ---- 查询 ----

    def batch_result(self, batch_id: str) -> dict[str, Any]:
        """整组检查结果：应收、已到、仍缺与逐条缺片明细。"""
        batch = self._batches.get(batch_id)
        if batch is None:
            return {}
        return {
            "batch_id": batch.batch_id,
            "expected": sorted(batch.expected),
            "check_count": batch.check_count,
            "arrived": sorted(batch.arrivals),
            "missing": self._missing_seqs(batch),
            "items": [batch.items[seq].to_dict() for seq in sorted(batch.items)],
        }

    def get_receipt(self, batch_id: str, seq: int) -> dict[str, Any] | None:
        receipt = self._receipts.get(receipt_id_of(batch_id, int(seq)))
        return receipt.to_dict() if receipt else None

    def get_receipt_by_task(self, task_id: str) -> dict[str, Any] | None:
        """按转写任务查采纳回执：实际所用字节与落选到片出处。"""
        for receipt in self._receipts.values():
            if receipt.task_id == task_id:
                return receipt.to_dict()
        return None

    def receipt_count(self) -> int:
        return len(self._receipts)

    # ---- 快照（供服务层持久化，重启后恢复） ----

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": 1,
            "batches": {bid: b.to_dict() for bid, b in self._batches.items()},
            "receipts": {rid: r.to_dict() for rid, r in self._receipts.items()},
        }

    def restore(self, data: dict[str, Any]) -> None:
        self._batches = {
            bid: BatchState.from_dict(b) for bid, b in (data.get("batches") or {}).items()
        }
        self._receipts = {
            rid: AdoptionReceipt.from_dict(r) for rid, r in (data.get("receipts") or {}).items()
        }
