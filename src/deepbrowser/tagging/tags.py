import json
from collections.abc import Mapping
from typing import Any, Literal, NewType, cast

from pydantic import BaseModel, Field
from pydantic_core._pydantic_core import ValidationError


class BaseTag(BaseModel):
    tag_name: str
    annotation_number: int
    inner_text: str
    aria_label: str | None = None
    html_id: str | None = None
    html_class: str | None = None
    can_scroll_up: bool = False
    can_scroll_down: bool = False
    can_scroll_left: bool = False
    can_scroll_right: bool = False

    class Config:
        by_alias = False


class AnchorTag(BaseTag):
    tag_name: Literal["A"]
    role: str | None = None


class DivTag(BaseTag):
    tag_name: Literal["DIV"]
    role: str | None = None
    has_on_click_event: bool | None = None


class InputTag(BaseTag):
    tag_name: Literal["INPUT"]
    type: str | None = None
    value: str | None = None
    placeholder: str | None = None


class SelectTag(BaseTag):
    tag_name: Literal["SELECT"]
    options: list[str]


class ButtonTag(BaseTag):
    tag_name: Literal["BUTTON"]


TagToXPath = NewType("TagToXPath", dict[BaseTag, str])


class _UnionTag(BaseModel):
    tag: AnchorTag | InputTag | DivTag | ButtonTag = Field(discriminator="tag_name")


def create_tag_from_json(data: str | Mapping[str, Any]) -> BaseTag:
    if not isinstance(data, (str, Mapping)):
        raise TypeError("data field must be str or Mapping")

    dct = json.loads(data) if isinstance(data, str) else data

    # Use a discriminated union to create the correct specific tag, if a specific tag
    # can't be used, try BaseTag
    try:
        # Cast dct to the expected type to satisfy mypy
        typed_dct = cast(AnchorTag | InputTag | DivTag | ButtonTag, dct)
        return _UnionTag(tag=typed_dct).tag
    except ValidationError:
        return BaseTag(**dct)
