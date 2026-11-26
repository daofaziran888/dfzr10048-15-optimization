"""批次缺片重扫与重投去重测试：缺片核对器、补采单去重、采纳回执与重启恢复。"""
from __future__ import annotations

import app.services.missing_scan as missing_scan_module
from app.pipeline.missing_fragment_scanner import (
    InMemoryOrderGateway,
    MissingFragmentScanner,
)
from app.services.fail_redeliver import FailRedeliverService
from app.services.missing_scan import MissingScanService
from app.store import store


def _orders_for(batch_id: str) -> list[dict]:
    """重投页上指定批次的全部补采单。"""
    return [
        r for r in store.rows("fail_redeliver")
        if str(r.get("关联分片", "")).startswith(f"{batch_id}-S")
    ]


def _open_orders_for(batch_id: str) -> list[dict]:
    return [r for r in _orders_for(batch_id) if r.get("status") in ("待重投", "重投中")]


class TestMissingFragmentScanner:
    """管线层：独立批次核对、未结单去重、冲突确认与采纳幂等。"""

    def test_check_batch_missing(self):
        scanner = MissingFragmentScanner()
        result = scanner.check_batch("PB", [1, 2, 3, 4, 5], [2, 4])
        assert result["missing"] == [1, 3, 5]
        assert result["arrived"] == [2, 4]
        assert len(result["items"]) == 3

    def test_cross_batch_same_seq_no_offset(self):
        scanner = MissingFragmentScanner()
        scanner.check_batch("PA", [1], [1])
        result = scanner.check_batch("PB2", [1, 2], [2])
        # 批次 A 的 1 号片已到，不能抵消批次 PB2 的 1 号缺片
        assert result["missing"] == [1]

    def test_recheck_no_duplicate_open_orders(self):
        gateway = InMemoryOrderGateway()
        scanner = MissingFragmentScanner(order_gateway=gateway)
        scanner.check_batch("PC", [1, 2, 3, 4, 5], [2, 4])
        assert gateway.total_count() == 3
        scanner.check_batch("PC", [1, 2, 3, 4, 5], [2, 4])
        scanner.check_batch("PC", [1, 2, 3, 4, 5], [2, 4])
        # 重复扫不增加相同未结单
        assert gateway.total_count() == 3
        assert gateway.open_count() == 3

    def test_claim_idempotent(self):
        scanner = MissingFragmentScanner()
        scanner.check_batch("PD", [1, 2], [2])
        ok1, _, item1 = scanner.claim_item("PD", 1)
        ok2, _, item2 = scanner.claim_item("PD", 1)
        assert ok1 and ok2
        assert item1["order_id"] == item2["order_id"]
        assert item2["status"] == "补采中"

    def test_claim_not_missing_rejected(self):
        scanner = MissingFragmentScanner()
        scanner.check_batch("PE", [1, 2], [2])
        ok, msg, _ = scanner.claim_item("PE", 2)
        assert not ok
        assert "不在缺片清单" in msg

    def test_same_arrival_reported_twice_not_duplicated(self):
        scanner = MissingFragmentScanner()
        scanner.check_batch("PF", [1], [])
        scanner.claim_item("PF", 1)
        scanner.report_arrival("PF", 1, "原入口补报", 100, "aa11", 0, 300, True)
        ok, msg, item = scanner.report_arrival("PF", 1, "原入口补报", 100, "aa11", 0, 300, True)
        assert ok
        assert "未重复" in msg
        assert item["candidate_count"] == 1

    def test_conflict_then_confirm_and_settle(self):
        gateway = InMemoryOrderGateway()
        scanner = MissingFragmentScanner(order_gateway=gateway)
        scanner.check_batch("PG", [1], [])
        scanner.claim_item("PG", 1)
        scanner.report_arrival("PG", 1, "原入口补报", 4194304, "aa11", 0, 300, True)
        _, _, item = scanner.report_arrival("PG", 1, "补采回传", 4089444, "bb22", 0, 300, True)
        # 同区间不同摘要 → 待确认
        assert item["status"] == "待确认"
        ok, msg, receipt, created = scanner.confirm_adoption("PG", 1, "原入口补报", "TRCB-X")
        assert ok and created
        assert receipt["adopted"]["source"] == "原入口补报"
        assert receipt["adopted_bytes"] == 4194304
        assert receipt["others"][0]["source"] == "补采回传"
        assert "FRDL-PG-S001" in receipt["others"][0]["provenance"]
        # 补采单已结算
        assert gateway.open_count() == 0

    def test_confirm_default_prefers_qualified_original(self):
        scanner = MissingFragmentScanner()
        scanner.check_batch("PH", [1], [])
        scanner.claim_item("PH", 1)
        scanner.report_arrival("PH", 1, "补采回传", 100, "bb22", 0, 300, True)
        scanner.report_arrival("PH", 1, "原入口补报", 120, "aa11", 0, 300, True)
        ok, _, receipt, _ = scanner.confirm_adoption("PH", 1)
        assert ok
        assert receipt["adopted"]["source"] == "原入口补报"

    def test_confirm_idempotent_receipt(self):
        scanner = MissingFragmentScanner()
        scanner.check_batch("PI", [1], [])
        scanner.claim_item("PI", 1)
        scanner.report_arrival("PI", 1, "原入口补报", 100, "aa11", 0, 300, True)
        _, _, receipt1, created1 = scanner.confirm_adoption("PI", 1, task_id="TRCB-1")
        _, msg2, receipt2, created2 = scanner.confirm_adoption("PI", 1, task_id="TRCB-1")
        assert created1 and not created2
        assert "未重复" in msg2
        assert receipt1["receipt_id"] == receipt2["receipt_id"]
        assert scanner.receipt_count() == 1

    def test_confirm_without_candidates_rejected(self):
        scanner = MissingFragmentScanner()
        scanner.check_batch("PJ", [1], [])
        scanner.claim_item("PJ", 1)
        ok, msg, _, _ = scanner.confirm_adoption("PJ", 1)
        assert not ok
        assert "尚无候选到片" in msg

    def test_late_bulk_arrival_settles_item(self):
        gateway = InMemoryOrderGateway()
        scanner = MissingFragmentScanner(order_gateway=gateway)
        scanner.check_batch("PK", [1, 2], [2])
        result = scanner.check_batch("PK", [1, 2], [1, 2])
        assert result["missing"] == []
        assert gateway.open_count() == 0

    def test_snapshot_restore_keeps_state(self):
        scanner = MissingFragmentScanner()
        scanner.check_batch("PL", [1, 2, 3], [2])
        scanner.claim_item("PL", 1)
        scanner.report_arrival("PL", 1, "原入口补报", 100, "aa11", 0, 300, True)
        scanner.confirm_adoption("PL", 1, task_id="TRCB-9")
        snapshot = scanner.snapshot()

        restored = MissingFragmentScanner()
        restored.restore(snapshot)
        result = restored.check_batch("PL", [1, 2, 3], [2])
        assert result["missing"] == [3]
        receipt = restored.get_receipt("PL", 1)
        assert receipt is not None
        assert receipt["adopted_bytes"] == 100
        assert restored.get_receipt_by_task("TRCB-9")["receipt_id"] == receipt["receipt_id"]


class TestMissingScanService:
    """服务层：补采单落到失败重投模块，状态落盘，重启后重扫重报不重复。"""

    def test_full_duty_scenario(self, tmp_path):
        svc = MissingScanService(storage_path=tmp_path / "state.json")

        # B 要收 1-5，收到 2 和 4 后只缺 1、3、5
        ok, msg, result = svc.check_batch("B", [1, 2, 3, 4, 5], [2, 4])
        assert ok
        assert result["missing"] == [1, 3, 5]
        assert len(_open_orders_for("B")) == 3

        # 异批同号不能抵消 B
        ok, _, _ = svc.check_batch("A", [1], [1])
        assert ok
        _, _, result = svc.check_batch("B", [1, 2, 3, 4, 5], [2, 4])
        assert result["missing"] == [1, 3, 5]

        # 重复扫不会增加相同未结单
        svc.check_batch("B", [1, 2, 3, 4, 5], [2, 4])
        assert len(_orders_for("B")) == 3
        assert len(_open_orders_for("B")) == 3

        # 领 B/1
        ok, msg, item = svc.claim_item("B", 1)
        assert ok
        assert item["status"] == "补采中"
        assert item["order_id"] == "FRDL-B-S001"

        # 同时收到原入口补报的合格原片和补采录音：同区间、不同摘要
        svc.report_arrival("B", 1, "原入口补报", 4194304, "aa11bb22", 0, 300, True)
        _, _, item = svc.report_arrival("B", 1, "补采回传", 4089444, "cc33dd44", 0, 300, True)
        assert item["status"] == "待确认"
        assert item["candidate_count"] == 2

        # 值班员确认处理：采纳原入口补报，回执关联转写任务
        ok, msg, receipt, created = svc.confirm_adoption("B", 1, "原入口补报", "TRCB-0001")
        assert ok and created
        assert receipt["receipt_id"] == "RCPT-B-S001"
        assert receipt["adopted_bytes"] == 4194304
        assert receipt["adopted"]["digest"] == "aa11bb22"
        assert receipt["others"][0]["source"] == "补采回传"
        assert "FRDL-B-S001" in receipt["others"][0]["provenance"]

        # 从转写任务查实际所用的字节和另一份录音的出处
        by_task = svc.get_receipt_by_task("TRCB-0001")
        assert by_task is not None
        assert by_task["adopted_bytes"] == 4194304
        assert by_task["others"][0]["provenance"].startswith("补采回传")

        # 重投页核对哪张单已结算
        orders = {r["重投编号"]: r for r in _orders_for("B")}
        assert orders["FRDL-B-S001"]["status"] == "已补齐"
        assert orders["FRDL-B-S001"]["重投结论"] == "采纳原入口补报"
        assert orders["FRDL-B-S003"]["status"] == "待重投"
        assert orders["FRDL-B-S005"]["status"] == "待重投"

        # 重启：换实例加载同一状态文件
        svc2 = MissingScanService(storage_path=tmp_path / "state.json")

        # 重试批量检查：B/1 已采纳，仍缺 3、5，未结单不重复
        ok, _, result = svc2.check_batch("B", [1, 2, 3, 4, 5], [2, 4])
        assert ok
        assert result["missing"] == [3, 5]
        assert len(_orders_for("B")) == 3

        # 重报 B/1 的采纳不重复
        ok, msg, receipt2, created2 = svc2.confirm_adoption("B", 1, "原入口补报", "TRCB-0001")
        assert ok and not created2
        assert "未重复" in msg
        assert receipt2["receipt_id"] == "RCPT-B-S001"
        assert len(_orders_for("B")) == 3

        # B/3 和 B/5 仍可从整组检查结果分别领取
        group = svc2.get_batch("B")
        claimable = {i["seq"] for i in group["items"] if i["status"] == "待领取"}
        assert claimable == {3, 5}
        ok, _, item3 = svc2.claim_item("B", 3)
        assert ok and item3["order_id"] == "FRDL-B-S003"
        ok, _, item5 = svc2.claim_item("B", 5)
        assert ok and item5["order_id"] == "FRDL-B-S005"

    def test_restart_recovers_group_result(self, tmp_path):
        svc = MissingScanService(storage_path=tmp_path / "state.json")
        svc.check_batch("RB", [1, 2, 3, 4, 5], [2, 4])
        svc.claim_item("RB", 1)
        svc.report_arrival("RB", 1, "原入口补报", 2048, "dd66", 0, 300, True)
        svc.confirm_adoption("RB", 1, task_id="TRCB-0003")

        svc2 = MissingScanService(storage_path=tmp_path / "state.json")
        group = svc2.get_batch("RB")
        assert group["missing"] == [3, 5]
        assert group["check_count"] == 1
        receipt = svc2.get_receipt("RB", 1)
        assert receipt["task_id"] == "TRCB-0003"
        assert receipt["adopted_bytes"] == 2048

    def test_confirm_with_unknown_task_rejected(self, tmp_path):
        svc = MissingScanService(storage_path=tmp_path / "state.json")
        svc.check_batch("TB", [1], [])
        svc.claim_item("TB", 1)
        svc.report_arrival("TB", 1, "原入口补报", 100, "aa11", 0, 300, True)
        ok, msg, receipt, created = svc.confirm_adoption("TB", 1, task_id="TRCB-9999")
        assert not ok and not created
        assert "不存在" in msg

    def test_transcribe_task_service_reads_receipt(self, tmp_path, monkeypatch):
        svc = MissingScanService(storage_path=tmp_path / "state.json")
        svc.check_batch("QB", [1], [])
        svc.claim_item("QB", 1)
        svc.report_arrival("QB", 1, "补采回传", 512, "ee77", 0, 300, True)
        svc.confirm_adoption("QB", 1, task_id="TRCB-0002")

        monkeypatch.setattr(missing_scan_module, "_service", svc)
        from app.services.transcribe_task import TranscribeTaskService
        receipt = TranscribeTaskService().get_adoption_receipt("TRCB-0002")
        assert receipt is not None
        assert receipt["adopted_bytes"] == 512
        assert receipt["adopted"]["source"] == "补采回传"

    def test_first_check_requires_expected(self, tmp_path):
        svc = MissingScanService(storage_path=tmp_path / "state.json")
        ok, msg, _ = svc.check_batch("NB", None, [])
        assert not ok
        assert "应收片号" in msg

    def test_settle_order_idempotent(self):
        svc = FailRedeliverService()
        svc.ensure_open_order("SB", 1, "SB-S001")
        ok1, _ = svc.settle_order("FRDL-SB-S001", "采纳原入口补报")
        ok2, msg2 = svc.settle_order("FRDL-SB-S001", "采纳补采回传")
        assert ok1 and ok2
        assert "未重复" in msg2
        rows = [r for r in store.rows("fail_redeliver") if r.get("重投编号") == "FRDL-SB-S001"]
        assert rows[0]["重投结论"] == "采纳原入口补报"

    def test_ensure_open_order_dedup(self):
        svc = FailRedeliverService()
        _, created1 = svc.ensure_open_order("DB", 2, "DB-S002")
        _, created2 = svc.ensure_open_order("DB", 2, "DB-S002")
        assert created1 and not created2
        rows = [r for r in store.rows("fail_redeliver") if r.get("关联分片") == "DB-S002"]
        assert len(rows) == 1
