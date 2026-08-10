"""安全 JSONPath 子集编译器。

首版只支持：根（$）、对象字段（$.posts）、数组下标（$.items[0]）、
数组展开（$.posts[*]）以及子记录相对路径（comments[*]）。
拒绝递归下降（$..x）、过滤表达式（[?(...)]）、函数（()）与负下标，
数组展开必须是路径最后一段（不支持下标后缀）。

解析器是自有的受限语法实现，绝不执行用户代码。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class PathSyntaxError(ValueError):
    """路径语法不被安全子集支持或非法。"""


@dataclass(frozen=True)
class PathToken:
    kind: str
    value: str | int | None = None

    @classmethod
    def field(cls, name: str) -> PathToken:
        return cls("field", name)

    @classmethod
    def index(cls, value: int) -> PathToken:
        return cls("index", value)

    @classmethod
    def wildcard(cls) -> PathToken:
        return cls("wildcard")


@dataclass(frozen=True)
class CompiledPath:
    """编译后的安全路径。

    - `tokens`: 全部段，绝对路径含根 $ 段（便于非空表示根路径）。
    - `absolute`: 是否以 $ 开头（记录路径为绝对，字段路径为相对）。
    - `wildcard_index`: 展开段在 tokens 中的下标（$[*] 为 1，$.posts[*] 为 2）。
    - `scalar`: 是否为标量映射（不含数组展开段）。
    """

    tokens: tuple[PathToken, ...]
    absolute: bool = False
    wildcard_index: int | None = None
    scalar: bool = False

    def ijson_prefix(self) -> str:
        """转为 ijson 前缀：$.posts[*] → posts.item，$[*] → item，$ → ""。"""
        parts: list[str] = []
        for i, token in enumerate(self.tokens):
            if token.kind == "dollar":
                continue
            if self.wildcard_index is not None and i > self.wildcard_index:
                break
            # 标量下标段只用于指针定位，ijson 前缀按展开/字段推进
            if token.kind == "index":
                continue
            parts.append(token.value if isinstance(token.value, str) else "item")
        return ".".join(parts)


def compile_path(value: str, *, relative: bool = False) -> CompiledPath:
    """编译安全路径；语法非法时抛 PathSyntaxError。"""
    if not isinstance(value, str) or not value:
        raise PathSyntaxError("路径不能为空")
    tokens = _tokenize(value)
    if not tokens:
        raise PathSyntaxError("路径不能为空")

    first = tokens[0]
    if first.kind == "dollar":
        if relative:
            raise PathSyntaxError("相对路径不能以 $ 开头")
        rest = tokens[1:]
        if not rest:
            # 根路径：单条记录，标量
            return CompiledPath(tokens=(PathToken("dollar"),), absolute=True, scalar=True)
        if rest[0].kind == "dot":
            rest = rest[1:]
            if not rest:
                raise PathSyntaxError("路径不完整")
        elif rest[0].kind != "lbracket":
            raise PathSyntaxError("$ 后必须是 . 或 [")
    else:
        if not relative:
            raise PathSyntaxError("路径必须以 $ 开头（相对路径请使用 relative=True）")
        rest = tokens

    segments: list[PathToken] = []
    star_index: int | None = None
    bracket_ended = False
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok.kind == "dot":
            i += 1
            if i >= len(rest) or rest[i].kind != "field":
                raise PathSyntaxError("'.' 后必须是字段名")
            segments.append(PathToken.field(rest[i].value))
            bracket_ended = False
            i += 1
        elif tok.kind == "field":
            if bracket_ended:
                raise PathSyntaxError("']' 后需要 '.' 或 '['")
            segments.append(PathToken.field(tok.value))
            bracket_ended = False
            i += 1
        elif tok.kind == "lbracket":
            if i + 2 >= len(rest) or rest[i + 2].kind != "rbracket":
                raise PathSyntaxError("[...] 只支持单个下标或 *")
            inner = rest[i + 1]
            if bracket_ended:
                raise PathSyntaxError("不支持连续下标（如 [0][*]）")
            if inner.kind == "number":
                segments.append(PathToken.index(int(inner.value)))
            elif inner.kind == "star":
                if star_index is not None:
                    raise PathSyntaxError("只允许一个数组展开段")
                segments.append(PathToken.wildcard())
                star_index = len(segments) - 1
            else:
                raise PathSyntaxError("[...] 只支持非负下标或 *")
            bracket_ended = True
            i += 3
        else:
            raise PathSyntaxError(f"不支持的路径片段: {tok.kind}")

    if star_index is not None and star_index != len(segments) - 1:
        raise PathSyntaxError("展开段必须是路径的最后一段")

    if first.kind == "dollar":
        full = (PathToken("dollar"),) + tuple(segments)
        wildcard_index = star_index + 1 if star_index is not None else None
    else:
        full = tuple(segments)
        wildcard_index = star_index

    return CompiledPath(
        tokens=full,
        absolute=first.kind == "dollar",
        wildcard_index=wildcard_index,
        scalar=wildcard_index is None,
    )


def resolve_one(value: Any, path: CompiledPath) -> Any:
    """解析标量映射：按段逐层取值。通配符在此被拒绝。"""
    if path.wildcard_index is not None:
        raise PathSyntaxError("标量映射不支持数组展开段（如需取子数组请用 resolve_many）")
    current = value
    for token in path.tokens:
        if token.kind == "dollar":
            continue
        if token.kind == "field":
            if not isinstance(current, dict) or token.value not in current:
                raise KeyError(token.value)
            current = current[token.value]
        elif token.kind == "index":
            if not isinstance(current, list):
                raise IndexError(token.value)
            if token.value >= len(current):
                raise IndexError(token.value)
            current = current[token.value]
        else:
            raise PathSyntaxError(f"标量映射不支持路径片段: {token.kind}")
    return current


def resolve_many(value: Any, path: CompiledPath) -> list[Any]:
    """解析子数组：沿展开段之前的段定位数组，逐元素返回。"""
    if path.wildcard_index is None:
        raise PathSyntaxError("resolve_many 需要一个数组展开段")
    current = value
    for token in path.tokens[: path.wildcard_index]:
        if token.kind == "dollar":
            continue
        if token.kind == "field":
            if not isinstance(current, dict) or token.value not in current:
                raise KeyError(token.value)
            current = current[token.value]
        elif token.kind == "index":
            if not isinstance(current, list) or token.value >= len(current):
                raise IndexError(token.value)
            current = current[token.value]
        else:
            raise PathSyntaxError(f"resolve_many 不支持路径片段: {token.kind}")
    if not isinstance(current, list):
        raise TypeError("展开段不是数组")
    return list(current)


def _tokenize(value: str) -> list[PathToken]:
    tokens: list[PathToken] = []
    i = 0
    n = len(value)
    while i < n:
        ch = value[i]
        if ch == "$":
            tokens.append(PathToken("dollar"))
            i += 1
        elif value.startswith("..", i):
            tokens.append(PathToken("dotdot"))
            i += 2
        elif ch == ".":
            tokens.append(PathToken("dot"))
            i += 1
        elif ch == "*":
            tokens.append(PathToken("star"))
            i += 1
        elif ch == "[":
            tokens.append(PathToken("lbracket"))
            i += 1
        elif ch == "]":
            tokens.append(PathToken("rbracket"))
            i += 1
        elif ch == "(":
            tokens.append(PathToken("lparen"))
            i += 1
        elif ch == ")":
            tokens.append(PathToken("rparen"))
            i += 1
        elif ch == "-":
            tokens.append(PathToken("dash"))
            i += 1
        elif ch.isdigit():
            j = i
            while j < n and value[j].isdigit():
                j += 1
            tokens.append(PathToken("number", value[i:j]))
            i = j
        elif ch.isalpha() or ch == "_":
            j = i
            while j < n and (value[j].isalnum() or value[j] == "_"):
                j += 1
            tokens.append(PathToken("field", value[i:j]))
            i = j
        else:
            raise PathSyntaxError(f"非法字符: {ch!r}")
    return tokens
