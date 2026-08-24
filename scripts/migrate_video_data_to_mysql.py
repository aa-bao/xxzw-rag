#!/usr/bin/env python3
"""把现有视频分析文件数据迁移到 MySQL rag_video_task 表。

- 优先扫描 rag-service/data/video_tasks/*.json（实时状态文件）
- 补充扫描 rag-service/data/video_output/*/manifest.json（历史交付物）
- 以 task_id 为业务唯一键，重复 task_id 只会更新，不会重复插入

用法（在仓库根目录）：
  python scripts/migrate_video_data_to_mysql.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAG_SERVICE = ROOT / "rag-service"
sys.path.insert(0, str(RAG_SERVICE))

from sqlalchemy.ext.asyncio import async_sessionmaker

from src.db.repositories import VideoTaskRepository
from src.db.session import create_engine
from src.shared.config import Settings


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def scan_states(task_root: Path, output_root: Path) -> dict[str, dict]:
    states: dict[str, dict] = {}

    if task_root.is_dir():
        for p in sorted(task_root.glob("*.json")):
            state = _read_json(p)
            if state and state.get("task_id"):
                states[str(state["task_id"])] = state

    if output_root.is_dir():
        for task_dir in sorted(output_root.iterdir()):
            if not task_dir.is_dir():
                continue
            manifest_path = task_dir / "manifest.json"
            if not manifest_path.is_file():
                continue
            manifest = _read_json(manifest_path)
            if not manifest:
                continue
            tid = str(manifest.get("task_id") or task_dir.name)
            if tid in states:
                continue
            states[tid] = {
                "task_id": tid,
                "source": str(manifest.get("source") or ""),
                "kind": str(manifest.get("kind") or "url"),
                "status": str(manifest.get("status") or "complete"),
                "created_at": manifest.get("created_at"),
                "updated_at": manifest.get("updated_at"),
                "output_dir": str(task_dir),
                "error": manifest.get("error"),
                "frames_requested": manifest.get("frames_requested"),
                "transcript_source": manifest.get("transcript_source"),
                "transcript": manifest.get("transcript"),
                "summary": manifest.get("summary") if isinstance(manifest.get("summary"), dict) else None,
                "report": manifest.get("report") if isinstance(manifest.get("report"), dict) else None,
                "keyframes": manifest.get("keyframes") if isinstance(manifest.get("keyframes"), list) else None,
                "cost": manifest.get("cost") if isinstance(manifest.get("cost"), dict) else None,
                "events": [],
                "qa_history": [],
                "video_path": manifest.get("video_path"),
                "audio_path": manifest.get("audio_path"),
            }

    return states


async def main() -> None:
    load_env(ROOT / ".env")
    settings = Settings.load(RAG_SERVICE / "config.yaml")
    database_url = settings.database.url.get_secret_value()
    engine = create_engine(
        database_url,
        pool_size=settings.database.pool_size,
        pool_recycle=settings.database.pool_recycle_seconds,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    task_root = Path(
        os.environ.get(
            "QUICK_WATCH_TASK_ROOT",
            str(RAG_SERVICE / "data" / "video_tasks"),
        )
    ).expanduser()
    output_root = Path(
        os.environ.get(
            "QUICK_WATCH_OUTPUT_ROOT",
            str(RAG_SERVICE / "data" / "video_output"),
        )
    ).expanduser()

    states = scan_states(task_root, output_root)
    print(f"发现 {len(states)} 个视频任务待迁移")

    async with session_factory() as session:
        repo = VideoTaskRepository(session)
        for tid, state in states.items():
            await repo.upsert_state(state)
            print(f"  migrated {tid} [{state.get('status')}]")

    await engine.dispose()
    print("迁移完成")


if __name__ == "__main__":
    asyncio.run(main())
