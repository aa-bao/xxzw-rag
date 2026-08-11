"""安全 JSONPath 子集编译器测试。"""
from __future__ import annotations

import pytest

from src.structured.paths import (
    CompiledPath,
    PathSyntaxError,
    compile_path,
    resolve_many,
    resolve_one,
)


@pytest.mark.parametrize(
    "path",
    ["$", "$.posts", "$.posts[*]", "$.items[0]", "comments[*]"],
)
def test_supported_paths_compile(path: str) -> None:
    assert compile_path(path, relative=not path.startswith("$")).tokens


@pytest.mark.parametrize(
    "path",
    ["$..posts", "$.posts[?(@.x)]", "$.x()", "$.items[-1]"],
)
def test_unsafe_paths_are_rejected(path: str) -> None:
    with pytest.raises(PathSyntaxError):
        compile_path(path)


def test_root_path() -> None:
    path = compile_path("$")
    assert path.tokens == (compile_path("$").tokens[0],)
    assert path.absolute is True
    assert path.wildcard_index is None
    assert path.scalar is True
    assert path.ijson_prefix() == ""


def test_relative_path_requires_flag() -> None:
    with pytest.raises(PathSyntaxError):
        compile_path("comments[*]")
    with pytest.raises(PathSyntaxError):
        compile_path("$", relative=True)


def test_field_path_prefix() -> None:
    assert compile_path("$.posts").ijson_prefix() == "posts"
    assert compile_path("$.items[0]").ijson_prefix() == "items"
    assert compile_path("$.posts[*]").ijson_prefix() == "posts.item"
    assert compile_path("$[*]").ijson_prefix() == "item"
    assert compile_path("comments[*]", relative=True).ijson_prefix() == "comments.item"


def test_wildcard_flags() -> None:
    root_array = compile_path("$[*]")
    assert root_array.wildcard_index == 1
    assert root_array.scalar is False

    nested = compile_path("$.posts[*]")
    assert nested.wildcard_index == 2
    assert nested.scalar is False


def test_index_segments_are_scalar() -> None:
    path = compile_path("$.items[0]")
    assert path.scalar is True
    assert path.wildcard_index is None


def test_compile_rejects_bad_shapes() -> None:
    for bad in (
        "",
        ".",
        "$.",
        "$[",
        "$[]",
        "$.a[*][*]",
        "$[*].a",
        "$.a[0][*]",
        "$.a[0]x",
        "$..a",
        "$.a?(@)",
        "a.b",
    ):
        with pytest.raises(PathSyntaxError):
            compile_path(bad)


def test_resolve_one_scalar_paths() -> None:
    value = {"items": [{"name": "a"}, {"name": "b"}], "meta": {"count": 2}}
    assert resolve_one(value, compile_path("$.meta.count")) == 2
    assert resolve_one(value, compile_path("$.items[1].name")) == "b"


def test_resolve_one_rejects_wildcard() -> None:
    with pytest.raises(PathSyntaxError):
        resolve_one({"posts": [1, 2]}, compile_path("$.posts[*]"))


def test_resolve_one_missing_field_raises_key_error() -> None:
    with pytest.raises(KeyError):
        resolve_one({"a": 1}, compile_path("$.missing"))


def test_resolve_many_child_array() -> None:
    value = {"posts": [{"id": 1}, {"id": 2}]}
    path = compile_path("$.posts[*]")
    assert resolve_many(value, path) == [{"id": 1}, {"id": 2}]


def test_resolve_many_root_array() -> None:
    value = [1, 2, 3]
    path = compile_path("$[*]")
    assert resolve_many(value, path) == [1, 2, 3]


def test_resolve_many_requires_wildcard() -> None:
    with pytest.raises(PathSyntaxError):
        resolve_many({"a": [1]}, compile_path("$.a"))


def test_resolve_many_non_array_raises_type_error() -> None:
    with pytest.raises(TypeError):
        resolve_many({"a": 5}, compile_path("$.a[*]"))
