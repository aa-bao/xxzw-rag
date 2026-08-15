"""映射建议测试：确定性规则 + 可选 LLM 增强 + 增强失败回退。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.structured.models import MappingSuggestion
from src.structured.profiler import SourceProfile, profile_source
from src.structured.suggestions import enhance_suggestion, suggest_mapping


def _json_file(tmp_path: Path, name: str, records: list[dict]) -> Path:
    path = tmp_path / name
    path.write_bytes(json.dumps(records).encode())
    return path


@pytest.fixture
def post_profile(tmp_path: Path) -> SourceProfile:
    """含唯一 ID、长正文、重复标签、时间戳与嵌套评论的典型帖子。"""
    records = [
        {
            "id": "p001",
            "title": "如何选择向量数据库",
            "body": "长正文内容，用于向量与全文检索。" * 40,
            "tags": ["vector", "database"],
            "date": "2026-01-01T08:00:00Z",
            "comments": [
                {"author": "alice", "content": "写得很清楚，收藏了。"},
                {"author": "bob", "content": "请问支持中文检索吗？"},
            ],
        },
        {
            "id": "p002",
            "title": "RAG 常见问题",
            "body": "另一段长正文内容，用于测试建议规则。" * 30,
            "tags": ["rag"],
            "date": "2026-01-02T09:30:00+08:00",
            "comments": [
                {"author": "carol", "content": "补充一个场景。"},
            ],
        },
    ]
    return profile_source(_json_file(tmp_path, "posts.json", records), "json")


def test_suggest_mapping_roles(post_profile: SourceProfile) -> None:
    suggestion = suggest_mapping(post_profile)

    assert isinstance(suggestion, MappingSuggestion)
    assert suggestion.field("id").role == "id"
    assert suggestion.field("title").role == "title"
    assert suggestion.field("body").role == "content"
    assert suggestion.field("tags").role == "keyword"
    assert suggestion.field("date").role == "timestamp"

    # 父子关系：comments 是子记录数组
    assert suggestion.record_types[0].record_path == "$"
    assert suggestion.record_types[0].children[0].record_path == "comments[*]"


def test_suggestion_fields_have_confidence_and_rule_ids(post_profile: SourceProfile) -> None:
    suggestion = suggest_mapping(post_profile)

    assert suggestion.field("id").confidence == pytest.approx(1.0)
    assert suggestion.field("id").rule_ids == ("unique_identifier",)
    assert "long_text" in suggestion.field("body").rule_ids
    assert "datetime_parse_rate" in suggestion.field("date").rule_ids


def test_low_uniqueness_short_text_becomes_keyword(tmp_path: Path) -> None:
    profile = profile_source(
        _json_file(
            tmp_path,
            "flat.json",
            [{"text": "普通描述文字", "code": "OK"} for _ in range(20)],
        ),
        "json",
    )
    suggestion = suggest_mapping(profile)

    # 所有值完全重复且非长文本 → 无 id/content；短且重复的标量 → keyword
    assert suggestion.field("text").role == "keyword"
    assert suggestion.field("code").role == "keyword"


def test_nested_qa_suggestion_keeps_urls_out_of_content_and_names_out_of_title(
    tmp_path: Path,
) -> None:
    """问答抓取数据中的长资源 URL 和嵌套人物名不能污染可检索正文。"""
    # 批量导入只根据首个文件生成共享模板；即使首条问题很短，question.text
    # 也必须按字段语义识别为正文，不能让后续长问题全部落入 display。
    question = "家居用品竞争大吗？"
    answer = "门槛较低的类目通常竞争更激烈，应结合支付金额和市场占比判断。" * 6
    profile = profile_source(
        _json_file(
            tmp_path,
            "topic.json",
            [
                {
                    "topic_id": "188111288515812",
                    "group": {
                        "name": "速卖通AliExpress",
                        "background_url": "https://images.example.com/" + "a" * 160,
                    },
                    "question": {
                        "owner": {
                            "name": "Sophia C.",
                            "avatar_url": "https://images.example.com/" + "b" * 160,
                        },
                        "text": question,
                    },
                    "answer": {
                        "owner": {
                            "name": "认证讲师",
                            "avatar_url": "https://images.example.com/" + "c" * 160,
                        },
                        "text": answer,
                    },
                    "columns": [{"column_id": "158114415842", "name": "新入必看帖子"}],
                }
            ],
        ),
        "json",
    )

    suggestion = suggest_mapping(profile)

    assert suggestion.field("question.text").role == "content"
    assert suggestion.field("answer.text").role == "content"
    assert suggestion.field("group.background_url").role == "display"
    assert suggestion.field("question.owner.avatar_url").role == "display"
    assert suggestion.field("answer.owner.avatar_url").role == "display"
    assert suggestion.field("group.name").role != "title"
    assert suggestion.field("question.owner.name").role != "title"
    assert suggestion.field("answer.owner.name").role != "title"
    content_paths = [
        field.path
        for field in suggestion.record_types[0].fields
        if field.role == "content"
    ]
    assert content_paths == ["question.text", "answer.text"]
    columns = next(
        child
        for child in suggestion.record_types[0].children
        if child.record_path == "columns[*]"
    )
    assert all(field.role != "title" for field in columns.fields)


def test_requires_user_confirmation_always_true(post_profile: SourceProfile) -> None:
    suggestion = suggest_mapping(post_profile)

    assert suggestion.requires_user_confirmation is True


class FakeChatClient:
    """记录 payload 并按注入结果返回的假聊天客户端。"""

    def __init__(self, result: str | None = None, raise_error: Exception | None = None) -> None:
        self.result = result
        self.raise_error = raise_error
        self.last_payload: dict | None = None

    async def chat_json(self, payload: dict) -> str:
        self.last_payload = payload
        if self.raise_error is not None:
            raise self.raise_error
        if self.result is None:
            raise AssertionError("FakeChatClient 未配置结果")
        return self.result


async def test_enhance_suggestion_sends_redacted_payload_and_parses(
    post_profile: SourceProfile,
) -> None:
    deterministic = suggest_mapping(post_profile)
    response = {
        "name": "帖子记录",
        "source_format": "json",
        "record_types": [
            {
                "name": "post",
                "record_path": "$",
                "fields": [
                    {"path": "id", "role": "id", "name": "post_id"},
                    {"path": "title", "role": "title", "name": "title"},
                    {"path": "body", "role": "content", "name": "body"},
                    {"path": "tags", "role": "keyword", "name": "tags"},
                    {"path": "date", "role": "timestamp", "name": "created_at"},
                ],
                "chunk_policy": "atomic",
            }
        ],
        "requires_user_confirmation": True,
    }
    fake_chat = FakeChatClient(result=json.dumps(response))

    enhanced = await enhance_suggestion(post_profile, deterministic, fake_chat)

    # 增强结果仍是建议，不能直接绑定文档
    assert enhanced.requires_user_confirmation is True
    assert fake_chat.last_payload["requires_user_confirmation"] is True

    # payload 只含路径/类型/统计与截断样例，不含完整正文
    payload = json.dumps(fake_chat.last_payload, ensure_ascii=False)
    assert "长正文内容，用于向量与全文检索。" not in payload
    assert fake_chat.last_payload["paths"]["body"]["avg_length"] > 0

    assert enhanced.field("id").role == "id"
    assert enhanced.field("id").name == "post_id"
    assert enhanced.field("body").role == "content"
    assert enhanced.field("date").role == "timestamp"
    assert enhanced.field("date").name == "created_at"
    assert enhanced.record_types[0].chunk_policy == "atomic"


async def test_enhance_invalid_json_falls_back(post_profile: SourceProfile) -> None:
    deterministic = suggest_mapping(post_profile)
    fake_chat = FakeChatClient(result="这不是 JSON")

    enhanced = await enhance_suggestion(post_profile, deterministic, fake_chat)

    assert enhanced == deterministic
    assert enhanced.requires_user_confirmation is True


async def test_enhance_timeout_falls_back(post_profile: SourceProfile) -> None:
    deterministic = suggest_mapping(post_profile)
    fake_chat = FakeChatClient(raise_error=TimeoutError("timeout"))

    enhanced = await enhance_suggestion(post_profile, deterministic, fake_chat)

    assert enhanced == deterministic


async def test_enhance_unsupported_role_falls_back(post_profile: SourceProfile) -> None:
    deterministic = suggest_mapping(post_profile)
    response = {
        "name": "帖子记录",
        "source_format": "json",
        "record_types": [
            {
                "name": "post",
                "record_path": "$",
                "fields": [{"path": "id", "role": "id"}, {"path": "body", "role": "script"}],
            }
        ],
    }
    fake_chat = FakeChatClient(result=json.dumps(response))

    enhanced = await enhance_suggestion(post_profile, deterministic, fake_chat)

    assert enhanced == deterministic


async def test_enhance_none_response_falls_back(post_profile: SourceProfile) -> None:
    deterministic = suggest_mapping(post_profile)
    fake_chat = FakeChatClient(result="null")

    enhanced = await enhance_suggestion(post_profile, deterministic, fake_chat)

    assert enhanced == deterministic
