# pyright: reportTypedDictNotRequiredAccess=false, reportOptionalSubscript=false


# from deepbrowser.models.cdp import (
#     DocumentSnapshot,
#     DomSnapshot,
#     NodeTreeSnapshot,
#     NodeType,
#     StringIndexed,
# )

# from . import Rect, annotation_font
from enum import StrEnum

from pydantic import BaseModel

from deepbrowser.tagging.shapes import Rect


class Elements(StrEnum):
    INPUT = "INPUT"
    A = "A"
    DIV = "DIV"
    SELECT = "SELECT"
    OPTION = "OPTION"


# @dataclass(frozen=True)
class Element(BaseModel):
    parent_id: int
    node_id: int
    frame_id: str
    backend_node_id: int
    name: str
    attributes: dict[str, str | None]
    styles: dict[str, str]
    bounds: Rect
    is_clickable: bool
    can_scroll_up: bool
    can_scroll_down: bool
    can_scroll_left: bool
    can_scroll_right: bool


# @dataclass(frozen=True)
class TextInputElement(Element):
    text: str | None = None


class SelectInputElement(Element):
    is_multi_select: bool
    # the text content of each option
    options: list[str]
    # the value attribute of each option
    option_values: list[str]
    selected_option_indices: list[int]


class CheckableInputElement(Element):
    # used for <input type="radio"> and <input type="checkbox">
    value: str
    selected: bool
