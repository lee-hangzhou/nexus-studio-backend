from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from pydantic.alias_generators import to_camel
from pydantic.json_schema import GetJsonSchemaHandler
from pydantic_core import CoreSchema


class TurnMediaOrigin(StrEnum):
    """媒体块语义来源, 不映射第二张资源表"""

    LIBRARY = "library"
    UPLOAD = "upload"


class TurnMediaType(StrEnum):
    """媒体类型"""

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    FILE = "file"


_TURN_BLOCK_CONFIG = ConfigDict(
    extra="forbid",
    populate_by_name=True,
    alias_generator=to_camel,
)


class _TurnBlockBase(BaseModel):
    """块基类：Python 侧可保留 type 默认值，对外 JSON Schema 强制 type 必填以便 discriminator"""

    model_config = _TURN_BLOCK_CONFIG

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: CoreSchema,
        handler: GetJsonSchemaHandler,
    ) -> dict[str, Any]:
        json_schema = handler(core_schema)
        props = json_schema.get("properties")
        if isinstance(props, dict) and "type" in props:
            type_schema = props["type"]
            if isinstance(type_schema, dict):
                type_schema.pop("default", None)
            required = list(json_schema.get("required") or [])
            if "type" not in required:
                required.append("type")
            json_schema["required"] = required
        return json_schema


class TurnTextBlock(_TurnBlockBase):
    """文本内容块"""

    type: Literal["text"] = "text"
    text: str


class TurnSkillBlock(_TurnBlockBase):
    """技能引用内容块"""

    type: Literal["skill"] = "skill"
    path: str = Field(min_length=1)


class TurnMediaBlock(_TurnBlockBase):
    """图片 / 视频 / 音频 / 文件块; url 由 BFF enrich 写入"""

    type: Literal["image", "video", "audio", "file"]
    origin: TurnMediaOrigin
    asset_id: int = Field(ge=1, alias="assetId")
    media_type: TurnMediaType | None = Field(default=None, alias="mediaType")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    url: str | None = Field(default=None, min_length=1)
    preview_url: str | None = Field(default=None, min_length=1, alias="previewUrl")

    @model_validator(mode="after")
    def _validate_media_block(self) -> TurnMediaBlock:
        """校验 type 与 media_type 一致, preview 仅视频可用"""
        expected = {
            "image": TurnMediaType.IMAGE,
            "video": TurnMediaType.VIDEO,
            "audio": TurnMediaType.AUDIO,
            "file": TurnMediaType.FILE,
        }[self.type]
        if self.media_type is not None and self.media_type != expected:
            raise ValueError(f"media_type {self.media_type} does not match block type {self.type}")
        if self.preview_url is not None and self.type != "video":
            raise ValueError("preview_url is only allowed on video blocks")
        return self


class TurnNodeBlock(_TurnBlockBase):
    """画布节点引用块"""

    type: Literal["node"] = "node"
    node_id: str = Field(min_length=1, alias="nodeId")


TurnContentBlock = Annotated[
    TurnTextBlock | TurnSkillBlock | TurnMediaBlock | TurnNodeBlock,
    Field(discriminator="type"),
]

TurnMaterialBlock = Annotated[
    TurnMediaBlock | TurnNodeBlock,
    Field(discriminator="type"),
]

_TURN_CONTENT_ADAPTER: TypeAdapter[list[TurnContentBlock]] = TypeAdapter(list[TurnContentBlock])
_TURN_MATERIAL_ADAPTER: TypeAdapter[list[TurnMaterialBlock]] = TypeAdapter(list[TurnMaterialBlock])


class TurnUserInput(BaseModel):
    """结构化 turn 用户输入"""

    model_config = _TURN_BLOCK_CONFIG

    content: list[TurnContentBlock] = Field(min_length=1)
    materials: list[TurnMaterialBlock] = Field(default_factory=list)

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: CoreSchema,
        handler: GetJsonSchemaHandler,
    ) -> dict[str, Any]:
        json_schema = handler(core_schema)
        # materials 运行时有 default_factory，对外契约仍要求显式数组，避免 FE 可选与空数组双语义
        required = list(json_schema.get("required") or [])
        if "materials" not in required:
            required.append("materials")
        json_schema["required"] = required
        # 去掉 minItems，避免 json2ts 生成 tuple；非空仍由 Pydantic min_length 校验
        props = json_schema.get("properties")
        if isinstance(props, dict):
            content_schema = props.get("content")
            if isinstance(content_schema, dict):
                content_schema.pop("minItems", None)
        return json_schema


class TurnReferenceAsset(BaseModel):
    """引用索引中的素材指针"""

    model_config = _TURN_BLOCK_CONFIG

    origin: TurnMediaOrigin
    asset_id: int = Field(ge=1, alias="assetId")
    media_type: TurnMediaType = Field(alias="mediaType")


class TurnReferenceSection(BaseModel):
    """一组素材与节点引用"""

    model_config = _TURN_BLOCK_CONFIG

    assets: list[TurnReferenceAsset] = Field(default_factory=list)
    nodes: list[str] = Field(default_factory=list)


class TurnReferenceIndex(BaseModel):
    """区分输入区 inline 与底部 materials 栏"""

    model_config = _TURN_BLOCK_CONFIG

    inline: TurnReferenceSection = Field(default_factory=TurnReferenceSection)
    materials: TurnReferenceSection = Field(default_factory=TurnReferenceSection)


@dataclass(frozen=True, slots=True)
class CompiledTurnInput:
    """compile_turn_input 产物"""

    human_message: str
    reference_index: TurnReferenceIndex
    persist_input: TurnUserInput
    tool_asset_ids: tuple[int, ...]
    tool_asset_index: tuple[TurnReferenceAsset, ...]


def validate_turn_user_input(
    input: TurnUserInput,
    *,
    allow_node: bool = True,
) -> TurnUserInput:
    """校验 turn 输入; 须含非空文本; Chat 拒绝 node"""
    has_text = any(
        isinstance(block, TurnTextBlock) and block.text.strip()
        for block in input.content
    )
    if not has_text:
        raise ValueError("content_required_after_skill_refs")
    if not allow_node:
        for block in (*input.content, *input.materials):
            if isinstance(block, TurnNodeBlock):
                raise ValueError("node blocks are not supported on this surface")
    return input


def extract_skill_paths(content: list[TurnContentBlock]) -> list[str]:
    """从内容块提取去重后的技能路径"""
    seen: set[str] = set()
    paths: list[str] = []
    for block in content:
        if isinstance(block, TurnSkillBlock):
            if block.path not in seen:
                seen.add(block.path)
                paths.append(block.path)
    return paths


def compile_human_text(content: list[TurnContentBlock]) -> str:
    """将内容块编译为 HumanMessage 文本, 媒体与节点为占位符"""
    parts: list[str] = []
    for block in content:
        if isinstance(block, TurnTextBlock):
            parts.append(block.text)
        elif isinstance(block, TurnSkillBlock):
            parts.append(f"[skill:{block.path}]")
        elif isinstance(block, TurnNodeBlock):
            parts.append(f"[node:{block.node_id}]")
        elif isinstance(block, TurnMediaBlock):
            parts.append(f"[{block.type}:asset_id={block.asset_id}]")
    return "\n".join(parts)


def _reference_asset_from_block(block: TurnMediaBlock) -> TurnReferenceAsset:
    """从媒体块构建引用指针; 要求 enrich 后 media_type 已齐全"""
    if block.media_type is None:
        raise ValueError(f"media_type required after enrich for asset_id={block.asset_id}")
    return TurnReferenceAsset(
        origin=block.origin,
        asset_id=block.asset_id,
        media_type=block.media_type,
    )


def _compile_reference_section(
    blocks: list[TurnContentBlock] | list[TurnMaterialBlock],
) -> TurnReferenceSection:
    """编译一节引用索引"""
    assets: list[TurnReferenceAsset] = []
    nodes: list[str] = []
    seen_asset_ids: set[int] = set()
    seen_node_ids: set[str] = set()
    for block in blocks:
        if isinstance(block, TurnMediaBlock):
            if block.asset_id in seen_asset_ids:
                continue
            seen_asset_ids.add(block.asset_id)
            assets.append(_reference_asset_from_block(block))
            continue
        if isinstance(block, TurnNodeBlock):
            if block.node_id in seen_node_ids:
                continue
            seen_node_ids.add(block.node_id)
            nodes.append(block.node_id)
    return TurnReferenceSection(assets=assets, nodes=nodes)


def compile_turn_input(user_input: TurnUserInput) -> CompiledTurnInput:
    """将 enrich 后的输入编译为文本与引用索引"""
    human_message = compile_human_text(user_input.content)
    if not any(isinstance(b, TurnTextBlock) and b.text.strip() for b in user_input.content):
        raise ValueError("content_required_after_skill_refs")
    inline_section = _compile_reference_section(user_input.content)
    materials_section = _compile_reference_section(user_input.materials)
    tool_index: list[TurnReferenceAsset] = []
    seen: set[int] = set()
    for asset in (*inline_section.assets, *materials_section.assets):
        if asset.asset_id in seen:
            continue
        seen.add(asset.asset_id)
        tool_index.append(asset)
    return CompiledTurnInput(
        human_message=human_message,
        reference_index=TurnReferenceIndex(
            inline=inline_section,
            materials=materials_section,
        ),
        persist_input=user_input,
        tool_asset_ids=tuple(a.asset_id for a in tool_index),
        tool_asset_index=tuple(tool_index),
    )


def format_turn_references_block(index: TurnReferenceIndex) -> str:
    """格式化注入 system/turn 的 Turn References 文本块"""
    lines: list[str] = ["## Turn References"]
    has_any = False
    has_visual = False

    def _append_section(title: str, section: TurnReferenceSection) -> None:
        nonlocal has_any, has_visual
        if not section.assets and not section.nodes:
            return
        has_any = True
        lines.append(f"### {title}")
        for asset in section.assets:
            if asset.media_type in {TurnMediaType.IMAGE, TurnMediaType.VIDEO}:
                has_visual = True
            lines.append(
                f"- origin={asset.origin.value} asset_id={asset.asset_id} media_type={asset.media_type.value}"
            )
        for node_id in section.nodes:
            lines.append(f"- node_id={node_id}")

    _append_section("inline", index.inline)
    _append_section("materials", index.materials)
    if not has_any:
        return ""
    if has_visual:
        lines.append(
            "Use inspect_turn_media for visual understanding of allowlisted image/video asset_ids. "
            "Use get_asset for metadata. Do not invent asset ids outside this list."
        )
    else:
        lines.append(
            "Use get_asset for metadata. Do not invent asset ids outside this list."
        )
    return "\n".join(lines)


def input_snapshot_dict(input: TurnUserInput) -> dict[str, Any]:
    """生成可持久化的输入快照"""
    return input.model_dump(mode="json", by_alias=True)


def parse_turn_content_blocks(raw: list[Any]) -> list[TurnContentBlock]:
    """解析并校验内容块列表"""
    return _TURN_CONTENT_ADAPTER.validate_python(raw)


def parse_turn_material_blocks(raw: list[Any]) -> list[TurnMaterialBlock]:
    """解析并校验 materials 块列表"""
    return _TURN_MATERIAL_ADAPTER.validate_python(raw)
