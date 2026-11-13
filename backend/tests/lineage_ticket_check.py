"""血缘对照票行为自检：不依赖 pytest，直接 `python -I tests/lineage_ticket_check.py` 运行。

覆盖：生成幂等且只读、字段检索/筛选/排序、确认联动版本与元数据待办、
并发确认只落库一次、新补数只选已确认版本、三个入口共用同一份摘要、
冲突字段按节点版本与生效时间裁决。
"""
from __future__ import annotations

import copy
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.seed import SEED_ROWS
from app.services.backfill import BackfillService
from app.services.lineage_ticket import LineageTicketService
from app.store import store

NODE_NO = "LINEAGE-0003"


def reset() -> None:
    """每个用例从同一份示例数据出发，避免用例之间互相污染。"""
    for name, rows in SEED_ROWS.items():
        store._tables[name] = [copy.deepcopy(row) for row in rows]


def node_id() -> int:
    return int(next(row["id"] for row in store.rows("lineage") if row["节点编号"] == NODE_NO))


def test_generate_is_idempotent_and_read_only() -> None:
    tickets = LineageTicketService()
    before = copy.deepcopy(store.find("lineage", node_id()))
    first, message = tickets.generate(node_id(), {})
    assert first is not None, message
    assert first["状态"] == "待评审" and first["只读"] is True
    second, _ = tickets.generate(node_id(), {"候选版本": first["候选版本"]})
    assert second is not None and second["票号"] == first["票号"]
    after = store.find("lineage", node_id())
    assert after["图谱版本"] == before["图谱版本"]
    assert after["字段映射"] == before["字段映射"]
    assert after["status"] == before["status"]


def test_item_search_filter_and_sort() -> None:
    tickets = LineageTicketService()
    ticket, message = tickets.generate(node_id(), {})
    assert ticket is not None, message
    items, total = tickets.ticket_items(ticket["票号"], keyword="amt", change_type="新增")
    assert total >= 1
    assert all("amt" in item["字段名"] and item["变化类型"] == "新增" for item in items)
    names = [item["字段名"] for item in tickets.ticket_items(ticket["票号"], sort_by="字段名", order="desc")[0]]
    assert names == sorted(names, reverse=True)


def test_confirm_updates_version_and_creates_todos() -> None:
    tickets = LineageTicketService()
    ticket, message = tickets.generate(node_id(), {})
    assert ticket is not None, message
    result, message, todos = tickets.confirm(ticket["票号"], operator="评审人")
    assert result is not None, message
    assert result["状态"] == "已确认"
    assert todos, "确认后应生成受影响元数据记录的待确认事项"
    for todo in todos:
        assert todo["状态"] == "待确认"
        assert todo["引用血缘节点版本"] == f"{NODE_NO}@{ticket['候选版本']}"
        assert todo["引用对照票"] == ticket["票号"]
    effective = [v for v in tickets.versions(NODE_NO) if v["状态"] == "已生效"]
    assert len(effective) == 1
    again, message, extra = tickets.confirm(ticket["票号"])
    assert again is not None and extra == [], "重复确认不能重复落库"


def test_concurrent_confirm_lands_one_version() -> None:
    tickets = LineageTicketService()
    ticket, message = tickets.generate(node_id(), {})
    assert ticket is not None, message
    results = []

    def worker() -> None:
        results.append(tickets.confirm(ticket["票号"]))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all(result[1] for result in results)
    effective = [v for v in tickets.versions(NODE_NO) if v["状态"] == "已生效"]
    assert len(effective) == 1
    todos = [t for t in store.rows("_metadata_todos") if t["引用对照票"] == ticket["票号"]]
    assert len({t["元数据记录id"] for t in todos}) == len(todos)


def test_new_backfill_needs_confirmed_version() -> None:
    tickets = LineageTicketService()
    ticket, message = tickets.generate(node_id(), {})
    assert ticket is not None, message
    binding = f"{NODE_NO}@{ticket['候选版本']}"
    service = BackfillService()
    entry, missing = service.create_entry({
        "批次编号": "BACK-T1", "目标表名": "dws_order_summary", "开始日期": "2026-12-01", "绑定版本号": binding,
    })
    assert entry is None and missing == [], "候选版本不能用于新补数"
    assert all(item["版本号"] != ticket["候选版本"] for item in service.available_versions(NODE_NO))
    tickets.confirm(ticket["票号"])
    entry, missing = service.create_entry({
        "批次编号": "BACK-T2", "目标表名": "dws_order_summary", "开始日期": "2026-12-01", "绑定版本号": binding,
    })
    assert entry is not None and entry["绑定票号"] == ticket["票号"]


def test_three_entries_share_one_summary() -> None:
    tickets = LineageTicketService()
    ticket, message = tickets.generate(node_id(), {})
    assert ticket is not None, message
    summary = tickets.ticket_summary(ticket["票号"])
    assert summary["items"] == [
        {"字段名": item["字段名"], "旧值": item["旧值"], "新值": item["新值"],
         "变化类型": item["变化类型"], "确认状态": item["确认状态"],
         "裁决版本": item["裁决版本"], "是否冲突": bool(item["是否冲突"])}
        for item in ticket["items"]
    ]


def test_conflict_adjudicated_by_version_and_time() -> None:
    tickets = LineageTicketService()
    later, message = tickets.generate(node_id(), {
        "候选版本": "v9", "生效时间": "2027-01-01 00:00",
        "字段映射": {"order_amt": {"来源字段": "pay_amount", "目标字段": "order_amt",
                                   "类型": "decimal(18,2)", "语义": "订单实付金额（含退款）"}},
    })
    assert later is not None, message
    earlier, message = tickets.generate(node_id(), {
        "候选版本": "v8", "生效时间": "2026-12-01 00:00",
        "字段映射": {"order_amt": {"来源字段": "amount", "目标字段": "order_amt",
                                   "类型": "decimal(18,2)", "语义": "订单金额"}},
    })
    assert earlier is not None, message
    conflict = next(item for item in earlier["items"] if item["字段名"] == "order_amt")
    assert conflict["是否冲突"] is True
    assert conflict["裁决版本"] == f"{NODE_NO}@v9"
    result, message, _ = tickets.confirm(earlier["票号"])
    assert result is None and "v9" in message, "冲突字段应裁决给更晚生效的版本"


def test_pending_candidate_blocks_manual_publish() -> None:
    """候选版本不能提前生效：有待评审版本时，节点发布动作必须转去对照票确认。"""
    from app.services.lineage import LineageService

    ticket, message = LineageTicketService().generate(node_id(), {})
    assert ticket is not None, message
    entry, message = LineageService().run_action(node_id(), "发布节点")
    assert entry is None and ticket["候选版本"] in message


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures = 0
    for test in tests:
        reset()
        try:
            test()
            print(f"PASS {test.__name__}")
        except AssertionError as error:
            failures += 1
            print(f"FAIL {test.__name__}: {error}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
