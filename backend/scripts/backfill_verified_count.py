#!/usr/bin/env python3
"""P10-T4 一次性回填脚本：按新口径重算 agent_tasks.verified_count。

口径（2026-10-09 老板批准）：
    verified_count = agent_findings 中 is_verified=True 且
    verification_status in ('confirmed', 'static_confirmed') 的条数，
    按 task_id GROUP BY 重算。

旧口径仅统计 confirmed，历史任务的 verified_count 偏小；本脚本从
agent_findings 真相源重算并 UPDATE agent_tasks，不加任何列。

用法（backend/ 目录下）：
    uv run python scripts/backfill_verified_count.py --dry-run
    uv run python scripts/backfill_verified_count.py

特性：
    - --dry-run 只打印将变更的任务与新旧值，零写入零提交；
    - 新旧值相同的任务跳过（可重复执行，幂等）；
    - 无匹配 finding 的任务若旧值非零则重置为 0。

注意：部署阶段由主控统一执行，本脚本不在开发机连接生产库。
"""
import argparse
import asyncio
import sys

from sqlalchemy import func, select, update

from app.db.session import AsyncSessionLocal
from app.models.agent_task import (
    VERIFIED_COUNT_STATUSES,
    AgentFinding,
    AgentTask,
)


def plan_backfill(
    task_rows: list[tuple[str, int]],
    verified_counts: dict[str, int],
) -> list[tuple[str, int, int]]:
    """根据任务现状与新口径计数生成变更计划（纯函数）。

    task_rows: [(task_id, old_verified_count), ...]
    verified_counts: {task_id: new_count}（GROUP BY 结果；零匹配任务缺键）
    返回 [(task_id, old, new), ...]，新旧相同跳过。
    """
    plan: list[tuple[str, int, int]] = []
    for task_id, old_value in task_rows:
        old = int(old_value or 0)
        new = int(verified_counts.get(task_id, 0))
        if old != new:
            plan.append((task_id, old, new))
    return plan


async def fetch_task_rows(db) -> list[tuple[str, int]]:
    """读取全部任务的 id 与当前 verified_count。"""
    stmt = select(AgentTask.id, AgentTask.verified_count)
    rows = (await db.execute(stmt)).all()
    return [(str(r[0]), r[1]) for r in rows]


async def fetch_verified_counts(db) -> dict[str, int]:
    """新口径 GROUP BY：task_id -> 已确认数（confirmed + static_confirmed）。"""
    stmt = (
        select(AgentFinding.task_id, func.count())
        .where(
            AgentFinding.is_verified == True,  # noqa: E712
            AgentFinding.verification_status.in_(VERIFIED_COUNT_STATUSES),
        )
        .group_by(AgentFinding.task_id)
    )
    rows = (await db.execute(stmt)).all()
    return {str(r[0]): int(r[1]) for r in rows}


async def apply_plan(db, plan: list[tuple[str, int, int]]) -> None:
    """逐任务 UPDATE verified_count 并提交。"""
    for task_id, _old, new in plan:
        await db.execute(
            update(AgentTask)
            .where(AgentTask.id == task_id)
            .values(verified_count=new)
        )
    if plan:
        await db.commit()


async def run_backfill(db, dry_run: bool) -> list[tuple[str, int, int]]:
    """执行回填流程，返回变更计划（dry_run 时不写入）。"""
    task_rows = await fetch_task_rows(db)
    verified_counts = await fetch_verified_counts(db)
    plan = plan_backfill(task_rows, verified_counts)

    for task_id, old, new in plan:
        print(f"[backfill] 任务 {task_id}: verified_count {old} -> {new}")

    if dry_run:
        print(f"[dry-run] 将更新 {len(plan)} 个任务；未执行任何写入或提交")
    elif plan:
        await apply_plan(db, plan)
        print(f"已更新 {len(plan)} 个任务的 verified_count")
    else:
        print("所有任务 verified_count 已符合新口径，无需更新")
    return plan


async def _main(dry_run: bool) -> int:
    async with AsyncSessionLocal() as db:
        try:
            await run_backfill(db, dry_run)
        except Exception:
            await db.rollback()
            raise
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="按 P10-T4 新口径回填 agent_tasks.verified_count"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将变更的任务与新旧值，不写入数据库",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args.dry_run)))


if __name__ == "__main__":
    main()
