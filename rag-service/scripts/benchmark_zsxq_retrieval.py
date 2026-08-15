"""PROTOTYPE — reproducible A/B retrieval benchmark for ZSXQ topic JSON.

A: one complete question/answer topic has one retrieval representation.
B: question, answer paragraphs and useful comments are retrieval representations;
   every hit returns and is evaluated as the complete topic.

The script intentionally evaluates topic ids, not physical vector entries.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np

MAX_EMBED_CHARS = 500


@dataclass(frozen=True)
class Topic:
    topic_id: str
    question: str
    answer: str
    comments: tuple[str, ...]

    @property
    def full_text(self) -> str:
        sections = [f"问题：\n{self.question}", f"回答：\n{self.answer}"]
        if self.comments:
            sections.append("补充讨论：\n" + "\n".join(self.comments))
        return "\n\n".join(sections)


@dataclass(frozen=True)
class QueryCase:
    query_id: str
    query: str
    relevant_topic_ids: tuple[str, ...]
    kind: str


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def load_topics(source_dir: Path) -> tuple[list[Topic], list[dict[str, str]]]:
    topics: list[Topic] = []
    skipped: list[dict[str, str]] = []
    for path in sorted(source_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            question = str((data.get("question") or {}).get("text") or "").strip()
            answer = str((data.get("answer") or {}).get("text") or "").strip()
            if not question or not answer:
                raise ValueError("question.text or answer.text is empty")
            comments = tuple(
                text
                for item in data.get("comments") or []
                if (text := str(item.get("text") or "").strip())
            )
            topics.append(Topic(str(data["topic_id"]), question, answer, comments))
        except Exception as exc:
            skipped.append({"file": path.name, "error": str(exc)})
    return topics, skipped


def stratified_sample(topics: list[Topic], count: int, seed: int) -> list[Topic]:
    """Keep short/medium/long records represented instead of sampling only easy cases."""
    ordered = sorted(topics, key=lambda topic: len(topic.full_text))
    rng = random.Random(seed)
    buckets = [ordered[index::5] for index in range(5)]
    selected: list[Topic] = []
    for index, bucket in enumerate(buckets):
        take = count // 5 + (1 if index < count % 5 else 0)
        selected.extend(rng.sample(bucket, min(take, len(bucket))))
    return sorted(selected, key=lambda topic: topic.topic_id)


def _sentences(text: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", part).strip(" ，。！？；:：")
        for part in re.split(r"[\n。！？；]+", text)
        if len(re.sub(r"\s+", "", part)) >= 10
    ]


def build_queries(topics: list[Topic], count: int, seed: int) -> list[QueryCase]:
    """Build held-out-looking queries from different fields, never the whole Q+A."""
    candidates: list[QueryCase] = []
    for topic in topics:
        question_parts = _sentences(topic.question)
        answer_parts = _sentences(topic.answer)
        if question_parts:
            part = max(question_parts, key=len)
            query = part[:100]
            candidates.append(QueryCase(f"{topic.topic_id}:q", query, (topic.topic_id,), "question"))
        if answer_parts:
            part = max(answer_parts, key=len)
            # Answer-side lookup tests whether advice/conclusions can find their question.
            query = part[:100]
            candidates.append(QueryCase(f"{topic.topic_id}:a", query, (topic.topic_id,), "answer"))
        useful_comments = [part for comment in topic.comments for part in _sentences(comment)]
        if useful_comments:
            part = max(useful_comments, key=len)
            candidates.append(QueryCase(f"{topic.topic_id}:c", part[:100], (topic.topic_id,), "comment"))

    rng = random.Random(seed + 1)
    by_kind: dict[str, list[QueryCase]] = {}
    for case in candidates:
        by_kind.setdefault(case.kind, []).append(case)
    selected: list[QueryCase] = []
    kinds = ("question", "answer", "comment")
    for index, kind in enumerate(kinds):
        bucket = by_kind.get(kind, [])
        take = count // len(kinds) + (1 if index < count % len(kinds) else 0)
        selected.extend(rng.sample(bucket, min(take, len(bucket))))
    if len(selected) < count:
        remaining = [case for case in candidates if case not in selected]
        selected.extend(rng.sample(remaining, min(count - len(selected), len(remaining))))
    return sorted(selected, key=lambda case: case.query_id)


def representations(topic: Topic, strategy: str) -> list[str]:
    if strategy == "atomic":
        return [topic.full_text[:MAX_EMBED_CHARS]]
    entries: list[str] = []
    sources = [topic.question, topic.answer, *topic.comments]
    for source in sources:
        parts = _sentences(source) or [source]
        for part in parts:
            for start in range(0, len(part), MAX_EMBED_CHARS):
                entry = part[start : start + MAX_EMBED_CHARS].strip()
                if entry and entry not in entries:
                    entries.append(entry)
    return entries


class EmbeddingClient:
    def __init__(self, cache_path: Path, concurrency: int = 12) -> None:
        self.base_url = (os.environ.get("EMBEDDING_BASE_URL") or os.environ["MODEL_RELAY_BASE_URL"]).rstrip("/")
        self.api_key = os.environ.get("EMBEDDING_API_KEY") or os.environ["MODEL_RELAY_API_KEY"]
        self.model = os.environ["EMBEDDING_MODEL"]
        self.cache_path = cache_path
        self.cache: dict[str, list[float]] = (
            json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
        )
        for shard in cache_path.parent.glob(cache_path.name + ".*.part.json"):
            self.cache.update(json.loads(shard.read_text(encoding="utf-8")))
        self.semaphore = asyncio.Semaphore(concurrency)

    def _save_cache(self, entries: dict[str, list[float]]) -> None:
        # Immutable shards avoid Windows file-lock races with antivirus/indexers.
        shard = self.cache_path.with_name(
            self.cache_path.name + f".{uuid.uuid4().hex}.part.json"
        )
        shard.write_text(json.dumps(entries), encoding="utf-8")

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}\0{text}".encode()).hexdigest()

    async def embed(self, texts: list[str]) -> np.ndarray:
        missing = list(dict.fromkeys(text for text in texts if self._key(text) not in self.cache))
        timeout = httpx.Timeout(90.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async def one(text: str) -> tuple[str, list[float]]:
                async with self.semaphore:
                    if self.model.startswith("doubao-embedding-vision"):
                        url = f"{self.base_url}/embeddings/multimodal"
                        payload = {"model": self.model, "input": [{"type": "text", "text": text}], "dimensions": 1024}
                    else:
                        url = f"{self.base_url}/embeddings"
                        payload = {"model": self.model, "input": [text]}
                    for attempt in range(5):
                        response = await client.post(url, json=payload, headers={"Authorization": f"Bearer {self.api_key}"})
                        if response.status_code == 200:
                            data = response.json()["data"]
                            vector = data["embedding"] if isinstance(data, dict) else data[0]["embedding"]
                            return self._key(text), vector
                        if response.status_code not in {429, 500, 502, 503, 504}:
                            raise RuntimeError(f"embedding API {response.status_code}: {response.text[:300]}")
                        await asyncio.sleep(2**attempt)
                    raise RuntimeError(f"embedding retries exhausted for {text[:40]!r}")

            for start in range(0, len(missing), 100):
                results = await asyncio.gather(*(one(text) for text in missing[start : start + 100]))
                batch_entries = dict(results)
                self.cache.update(batch_entries)
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self._save_cache(batch_entries)
                print(f"embedded {min(start + 100, len(missing))}/{len(missing)} new texts", flush=True)
        matrix = np.asarray([self.cache[self._key(text)] for text in texts], dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.maximum(norms, 1e-12)


async def paraphrase_queries(
    cases: list[QueryCase], cache_path: Path, concurrency: int = 3
) -> list[QueryCase]:
    """Rewrite diagnostic source snippets into realistic, non-verbatim searches."""
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        cached = {}
    base_url = os.environ.get(
        "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).rstrip("/")
    api_key = os.environ.get("DASHSCOPE_API_KEY") or os.environ["MODEL_RELAY_API_KEY"]
    model = os.environ.get("VISION_MODEL") or os.environ["CHAT_MODEL"]
    missing = [case for case in cases if case.query_id not in cached]
    semaphore = asyncio.Semaphore(concurrency)
    timeout = httpx.Timeout(120.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async def rewrite(batch: list[QueryCase]) -> dict[str, str]:
            payload_items = [{"id": case.query_id, "text": case.query} for case in batch]
            prompt = (
                "把下面每条知识片段改写成用户会输入知识库搜索框的简短中文问题。"
                "保留原意和关键实体，不回答问题，不新增事实；换一种说法，避免连续照抄原文。"
                "仅输出 JSON 对象，键为 id，值为改写后的问题。\n"
                + json.dumps(payload_items, ensure_ascii=False)
            )
            async with semaphore:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.2,
                        "response_format": {"type": "json_object"},
                    },
                )
            if response.status_code != 200:
                raise RuntimeError(f"chat API {response.status_code}: {response.text[:300]}")
            content = response.json()["choices"][0]["message"]["content"]
            match = re.search(r"\{.*\}", content, flags=re.S)
            if not match:
                raise RuntimeError(f"chat response has no JSON object: {content[:300]}")
            data = json.loads(match.group(0))
            return {str(key): str(value).strip() for key, value in data.items()}

        for start in range(0, len(missing), 15):
            batch = missing[start : start + 15]
            cached.update(await rewrite(batch))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cached, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"paraphrased {min(start + 15, len(missing))}/{len(missing)}", flush=True)
    return [
        QueryCase(case.query_id, cached.get(case.query_id, case.query), case.relevant_topic_ids, case.kind)
        for case in cases
    ]


def evaluate(
    cases: list[QueryCase], query_vectors: np.ndarray,
    entry_vectors: np.ndarray, entry_topic_ids: list[str], topics_by_id: dict[str, Topic],
    top_k: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for case, query_vector in zip(cases, query_vectors, strict=True):
        scores = entry_vectors @ query_vector
        order = np.argsort(-scores)
        ranked: list[tuple[str, float]] = []
        seen: set[str] = set()
        for index in order:
            topic_id = entry_topic_ids[int(index)]
            if topic_id in seen:
                continue
            seen.add(topic_id)
            ranked.append((topic_id, float(scores[int(index)])))
            if len(ranked) >= top_k:
                break
        relevant = set(case.relevant_topic_ids)
        hits = [topic_id in relevant for topic_id, _ in ranked]
        first_rank = next((index + 1 for index, hit in enumerate(hits) if hit), None)
        rows.append({
            "query_id": case.query_id, "kind": case.kind, "query": case.query,
            "relevant_topic_ids": list(case.relevant_topic_ids),
            "ranked": [{"topic_id": topic_id, "score": score} for topic_id, score in ranked],
            "recall_at_k": len({topic_id for topic_id, _ in ranked} & relevant) / len(relevant),
            "precision_at_k": sum(hits) / top_k,
            "top1_accuracy": float(bool(hits and hits[0])),
            "reciprocal_rank": 1 / first_rank if first_rank else 0.0,
            "returned_chars": sum(len(topics_by_id[topic_id].full_text) for topic_id, _ in ranked),
        })
    keys = ("recall_at_k", "precision_at_k", "top1_accuracy", "reciprocal_rank", "returned_chars")
    summary = {key: sum(float(row[key]) for row in rows) / len(rows) for key in keys}
    summary["query_count"] = len(rows)
    summary["top_k"] = top_k
    summary["estimated_returned_tokens"] = summary["returned_chars"] / 2
    summary["by_kind"] = {
        kind: {
            key: sum(float(row[key]) for row in rows if row["kind"] == kind) / sum(row["kind"] == kind for row in rows)
            for key in keys[:-1]
        }
        for kind in sorted({str(row["kind"]) for row in rows})
    }
    return summary, rows


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--corpus-size", type=int, default=400)
    parser.add_argument("--query-count", type=int, default=120)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260813)
    parser.add_argument("--output", type=Path, default=Path("artifacts/zsxq-retrieval-ab.json"))
    parser.add_argument("--paraphrase", action="store_true")
    args = parser.parse_args()
    _load_dotenv(Path(__file__).resolve().parents[2] / ".env")

    all_topics, skipped = load_topics(args.source_dir)
    topics = stratified_sample(all_topics, min(args.corpus_size, len(all_topics)), args.seed)
    cases = build_queries(topics, args.query_count, args.seed)
    if args.paraphrase:
        cases = await paraphrase_queries(cases, args.output.with_suffix(".paraphrases.json"))
    client = EmbeddingClient(args.output.with_suffix(".embeddings.json"))
    query_vectors = await client.embed([case.query for case in cases])
    topics_by_id = {topic.topic_id: topic for topic in topics}
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": client.model,
        "dataset": {"available_topics": len(all_topics), "corpus_topics": len(topics), "queries": len(cases), "skipped": skipped},
        "query_mode": "llm_paraphrase" if args.paraphrase else "source_snippet_diagnostic",
        "metric_definition": {
            "recall_at_k": "relevant topics returned / all relevant topics",
            "precision_at_k": "relevant topics returned / K",
            "top1_accuracy": "first returned topic is relevant",
            "mrr": "mean reciprocal rank of first relevant topic",
        },
        "strategies": {},
    }
    for strategy in ("atomic", "topic"):
        texts: list[str] = []
        topic_ids: list[str] = []
        for topic in topics:
            entries = representations(topic, strategy)
            texts.extend(entries)
            topic_ids.extend([topic.topic_id] * len(entries))
        vectors = await client.embed(texts)
        summary, rows = evaluate(cases, query_vectors, vectors, topic_ids, topics_by_id, args.top_k)
        report["strategies"][strategy] = {"entry_count": len(texts), "summary": summary, "queries": rows}
        print(strategy, json.dumps(summary, ensure_ascii=False), flush=True)
    atomic = report["strategies"]["atomic"]["summary"]
    topic = report["strategies"]["topic"]["summary"]
    report["delta_topic_minus_atomic"] = {
        key: topic[key] - atomic[key]
        for key in ("recall_at_k", "precision_at_k", "top1_accuracy", "reciprocal_rank", "returned_chars", "estimated_returned_tokens")
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {args.output.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
