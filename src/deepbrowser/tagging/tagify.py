# pyright: reportTypedDictNotRequiredAccess=false, reportOptionalSubscript=false

import importlib.resources
import io
import re
from collections import defaultdict
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from deepbrowser.browser.cdp.types import (
    DocumentSnapshot,
    DomSnapshot,
    NodeTreeSnapshot,
    NodeType,
    StringIndexed,
)
from deepbrowser.tagging import font as annotation_font
from deepbrowser.tagging.elements import (
    CheckableInputElement,
    Element,
    Elements,
    SelectInputElement,
    TextInputElement,
)
from deepbrowser.tagging.shapes import Rect


_ELEMENT_COLORS = {
    "input": ("#00FF00", "black"),
    "a": ("#FF69B4", "white"),
    "div": ("#00FFFF", "black"),
}
_DEFAULT_COLOR = ("#FF0000", "white")


INTERACTABLE_ELEMENTS = {
    "a",
    "button",
    "details",
    "embed",
    "input",
    "menu",
    "menuitem",
    "object",
    "select",
    "textarea",
    "summary",
    "label",
}

INTERACTIVE_ROLES = {
    "button",
    "menu",
    "menuitem",
    "link",
    "checkbox",
    "radio",
    "slider",
    "tab",
    "tabpanel",
    "combobox",
    "textbox",
    "grid",
    "listbox",
    "option",
    "progressbar",
    "scrollbar",
    "searchbox",
    "switch",
    "tree",
    "treeitem",
    "spinbutton",
    "tooltip",
}


# Borrowed from (MIT-licensed)
# https://github.com/browser-use/browser-use/blob/0d8ecd690a7de51c13e0402fcefcae6bb1fc0870/browser_use/dom/buildDomTree.js#L656
INTERACTIVE_CURSORS = {
    "pointer",
    "move",
    "text",
    "grab",
    "grabbing",
    "cell",
    "copy",
    "alias",
    "all-scroll",
    "col-resize",
    "context-menu",
    "crosshair",
    "e-resize",
    "ew-resize",
    "help",
    "n-resize",
    "ne-resize",
    "nesw-resize",
    "ns-resize",
    "nw-resize",
    "nwse-resize",
    "row-resize",
    "s-resize",
    "se-resize",
    "sw-resize",
    "vertical-text",
    "w-resize",
    "zoom-in",
    "zoom-out",
}
NON_INTERACTIVE_CURSORS = {
    "not-allowed",
    "no-drop",
    "wait",
    "progress",
    "initial",
    "inherit",
}


def is_commonly_interactable_element(element: Element) -> bool:
    return element.name.lower() not in ("html", "body") and (
        element.is_clickable
        or element.name.lower() in INTERACTABLE_ELEMENTS
        or element.attributes.get("role") in INTERACTIVE_ROLES
        or element.attributes.get("aria-role") in INTERACTIVE_ROLES
    )


def has_pointer_style(element: Element) -> bool:
    return element.styles.get("cursor") in INTERACTIVE_CURSORS


def is_disabled_element(element: Element) -> bool:
    return (
        element.attributes.get("disabled") is not None
        or element.attributes.get("aria-disabled") == "true"
        or element.styles.get("cursor") in NON_INTERACTIVE_CURSORS
    )


def extract_frame_bounds(
    snapshot: DomSnapshot, queried_styles: list[str]
) -> dict[str, Rect | None]:
    """
    Return a mapping of frameId -> absolute bounds of the frame content (relative to the root frame)

    This computes the visible content rectangle for every frame and iframe in the DOMSnapshot,
    adjusting for borders, padding, and scroll offsets so all coordinates are expressed in the
    root document's coordinate space.
    """
    strings = snapshot["strings"]

    # Maps of frame hierarchy and geometry data
    frame_children: dict[str, list[Any]] = {}  # parent_frame_id -> [child_frame_ids]
    frame_relative_bounds: dict[str, Rect | None] = {}  # child_frame_id -> Rect relative to parent
    frame_scroll_rect: dict[str, Rect] = {}  # frame_id -> its scrollable rect

    # Iterate through each document (main frame + iframes)
    for doc in snapshot["documents"]:
        doc_frame_id = strings[doc["frameId"]]
        frame_children[doc_frame_id] = []

        # If this doc doesn't have any iframes, continue
        if (content_doc_index := doc["nodes"].get("contentDocumentIndex")) is None:
            continue

        # Record this frame’s scrollable rect for later offset adjustments
        if (root_node_idx := _get_root_node_idx(doc)) is not None:
            if (
                rects := _get_client_and_scroll_rects_by_node_idx(
                    document=doc, node_idx=root_node_idx
                )
            ) is not None:
                frame_scroll_rect[doc_frame_id] = rects[1]

        # Iterate through each iframe node and its associated child document
        for iframe_node_idx, iframe_doc_idx in zip(
            content_doc_index["index"], content_doc_index["value"]
        ):
            iframe_id = strings[snapshot["documents"][iframe_doc_idx]["frameId"]]
            frame_children[doc_frame_id].append(iframe_id)
            try:
                # Find this iframe node in the layout tree to get its bounds
                layout_idx = doc["layout"]["nodeIndex"].index(iframe_node_idx)
                iframe_box = Rect.from_cdp(doc["layout"]["bounds"][layout_idx])

                # Retrieve computed style values (padding, border widths, etc.)
                iframe_styles = {
                    style: strings[s_idx]
                    for style, s_idx in zip(queried_styles, doc["layout"]["styles"][layout_idx])
                }
                # the iframe_box bounding box starts at top-left corner, before any
                # padding/border. We want the frame bounds to represent where the _content_ starts
                # and ends
                offsets = {}
                for style in ("padding", "border"):
                    for side in ("top", "left", "right", "bottom"):
                        style_suffix = "-width" if style == "border" else ""
                        style_value = iframe_styles[f"{style}-{side}{style_suffix}"]
                        offsets[f"{style}-{side}{style_suffix}"] = int(style_value.rstrip("px"))

                # Subtract padding/border widths from total frame box to get content box
                frame_width = (
                    iframe_box.width
                    - offsets["border-left-width"]
                    - offsets["border-right-width"]
                    - offsets["padding-left"]
                    - offsets["padding-right"]
                )

                # Compute content box origin (inside the top-left padding/border)
                frame_height = (
                    iframe_box.height
                    - offsets["border-top-width"]
                    - offsets["border-bottom-width"]
                    - offsets["padding-top"]
                    - offsets["padding-bottom"]
                )
                frame_relative_bounds[iframe_id] = Rect(
                    x=iframe_box.x + offsets["border-left-width"] + offsets["padding-left"],
                    y=iframe_box.y + offsets["border-top-width"] + offsets["padding-top"],
                    width=frame_width,
                    height=frame_height,
                )
            except ValueError:
                # Iframe not in layout tree (e.g., display:none), mark as None
                frame_relative_bounds[iframe_id] = None

    # Now walk DAG from root, converting relative to absolute bounds
    frame_bounds: dict[str, Rect | None] = {}

    # The root document is always the first entry in snapshot["documents"]
    root_doc = snapshot["documents"][0]
    root_frame_id = strings[root_doc["frameId"]]

    # Start with root frame at (0,0)
    queue: list[tuple[str, Rect | None]] = [
        (
            root_frame_id,
            Rect.from_cdp((0, 0, root_doc["contentWidth"], root_doc["contentHeight"])),
        )
    ]

    # Breadth-first traversal of frame hierarchy
    while queue:
        frame_id, abs_bounding_box = queue.pop(0)
        frame_bounds[frame_id] = abs_bounding_box
        scroll_rect = frame_scroll_rect.get(frame_id)

        # Compute child frame absolute positions
        for child_id in frame_children[frame_id]:
            if (
                abs_bounding_box is not None
                and (rect := frame_relative_bounds[child_id]) is not None
            ):
                # Translate child’s relative bounds by parent’s absolute position
                child_bounds = rect.translate(abs_bounding_box.x, abs_bounding_box.y)
                if scroll_rect is not None and frame_id != root_frame_id:
                    child_bounds = child_bounds.translate(-scroll_rect.x, -scroll_rect.y)
            else:
                child_bounds = None

            queue.append((child_id, child_bounds))

    return frame_bounds


def _apply_overflow_viewport_propagation(
    *,
    body_element: Element,
    elements_by_id: dict[int, Element],
    root_client_rect: Rect,
    root_scroll_rect: Rect,
) -> Element | None:
    """
    Propagate the <body> element's scrollable overflow properties to the <html> root element.

    In many documents, the <body> scrolls instead of <html>. However, the CDP DOMSnapshot
    sometimes reports scrolling behavior only on <body>, even though user-visible scrolling
    happens at the viewport (root <html>) level.

    This function detects that situation and copies the scrollable directions
    (up/down/left/right) from <body> to <html> so the root element is marked as scrollable.
    """
    # Get the parent of <body> (should be <html>)
    parent_element = elements_by_id.get(body_element.parent_id)
    if parent_element is None or parent_element.name != "HTML":
        return None

    # If either <body> or <html> is hidden or the <html> overflow is not visible,
    # then the body’s overflow does not propagate to the viewport and therefore there is nothing
    # to do
    if (
        body_element.styles["display"] == "none"
        or parent_element.styles["display"] == "none"
        or (parent_element.styles["overflow-x"], parent_element.styles["overflow-y"])
        != ("visible", "visible")
    ):
        return None

    # Identify which directions the <body> can scroll based on overflow styles.
    scrollable_styles = ("visible", "auto", "scroll")

    # --- Vertical scrolling ---
    y_scrollable = body_element.styles["overflow-y"] in scrollable_styles
    can_scroll_up = y_scrollable and root_scroll_rect.y > 0
    can_scroll_down = (
        y_scrollable and (root_scroll_rect.y + root_client_rect.height) < root_scroll_rect.height
    )

    # --- Horizontal scrolling ---
    x_scrollable = body_element.styles["overflow-x"] in scrollable_styles
    can_scroll_left = x_scrollable and root_scroll_rect.x > 0
    can_scroll_right = (
        x_scrollable and (root_scroll_rect.x + root_client_rect.width) < root_scroll_rect.width
    )

    updated_html_element = parent_element.model_copy(
        update={
            "can_scroll_up": can_scroll_up,
            "can_scroll_down": can_scroll_down,
            "can_scroll_left": can_scroll_left,
            "can_scroll_right": can_scroll_right,
        }
    )
    return updated_html_element
    # element_by_id[body_element.parent_id] = updated_html_element


def _get_root_node_idx(document: DocumentSnapshot) -> int | None:
    """
    Find and return the node index of the document's root element (<html>).
    """
    nodes = document["nodes"]
    # find the root element, which should be HTML
    for node_idx, node_type in enumerate(nodes["nodeType"]):
        # The root element should:
        # 1. Be an ELEMENT_NODE (not a text, comment, etc.)
        # 2. Have a parentIndex of 0 — meaning its parent is the document root
        if node_type == NodeType.ELEMENT_NODE and nodes["parentIndex"][node_idx] == 0:
            return node_idx

    # If no node matches (unusual, but possible for malformed documents), return None
    return None


def _get_client_and_scroll_rects_by_node_idx(
    *, document: DocumentSnapshot, node_idx: int
) -> tuple[Rect, Rect] | None:
    """
    Given a node index (usually the root <html> node), return its client and scroll rectangles.

    Each node that participates in layout has corresponding rectangles in the document["layout"]
    arrays. This function finds the matching layout entry for the given node index and extracts:
      • clientRects → the visible area (content box currently in view)
      • scrollRects → the total scrollable area of the element

    These rects are used to compute scrolling boundaries and viewport clipping later in the pipeline
    """
    layout = document["layout"]
    for layout_idx, cur_node_idx in enumerate(layout["nodeIndex"]):
        if cur_node_idx == node_idx:
            return (
                Rect.from_cdp(layout["clientRects"][layout_idx]),
                Rect.from_cdp(layout["scrollRects"][layout_idx]),
            )

    return None


def _make_input_element(
    nodes: NodeTreeSnapshot, base_element: Element, strings: StringIndexed
) -> TextInputElement | CheckableInputElement:
    """
    Convert a generic <input> or <textarea> Element into a more specific subclass
    (TextInputElement or CheckableInputElement) with richer metadata.

    The DOMSnapshot flattens all elements into a single structure, so <input>,
    <textarea>, and similar elements start as generic Elements. This function inspects
    their type and fills in extra properties such as 'text', 'value', or 'selected'.
    """
    # Determine the input type (e.g., "text", "radio", "checkbox", etc.)
    input_type = str(base_element.attributes.get("type", ""))
    node_idx = base_element.node_id

    base_attrs = base_element.model_dump()
    base_attrs["bounds"] = Rect(**base_attrs["bounds"])

    # Handle checkable inputs like radio buttons and checkboxes
    if input_type.lower() in ("radio", "checkbox"):
        # default value of an <input type="radio"> or <input type="checkbox"> is "on"
        value = str(base_element.attributes.get("value", "on"))
        # Determine if the input is currently selected/checked
        selected = node_idx in nodes["inputChecked"]["index"]
        return CheckableInputElement(
            **base_attrs,
            value=value,
            selected=selected,
        )

    # Treat everything else as text-like <input> (e.g., <input type="text" | "email" | "password">)
    text = ""
    # with suppress(ValueError):
    input_val_idx = nodes["inputValue"]["index"].index(node_idx)
    string_idx = nodes["inputValue"]["value"][input_val_idx]
    if string_idx != -1:
        text = strings[string_idx]
    return TextInputElement(
        **base_attrs,
        text=text,
    )


def _node_name(nodes: NodeTreeSnapshot, node_idx: int, strings: StringIndexed) -> str | None:
    """
    Return the DOM tag name for the given node index (e.g., "DIV", "INPUT"), or None.

    The DOMSnapshot stores strings in a shared table. For each node, `nodes["nodeName"][i]`
    is an index into that table. This helper resolves that index to the actual string.
    """
    # Look up the string-table index for this node's name and resolve it to the actual string.
    # If the index is -1 or otherwise invalid, the caller may treat the result as None.
    return strings[nodes["nodeName"][node_idx]]


def _is_being_rendered(node_layout_idx: dict[int, int], node_idx: int) -> bool:
    """
    Return whether the node is being rendered
    """
    return node_idx in node_layout_idx


def _descendant_text_content(
    document: DocumentSnapshot,
    strings: StringIndexed,
    adjacency_list: dict[int, list[int]],
    node_idx: int,
    max_length: int | None = None,
) -> str:
    """
    Recursively collect and concatenate all text content from a node's descendants.

    This function performs a depth-first traversal of the DOM subtree rooted at `node_idx`,
    collecting the text of all TEXT_NODE children (and deeper descendants) in document order.

    It's used when an element itself doesn't have visible layout info (e.g., not rendered),
    so we still want to extract some representative text for labeling or analysis.

    Args:
        document:  The DocumentSnapshot containing node and layout arrays.
        strings:   Shared string table used to decode string indices.
        adjacency_list:  Mapping of node_id -> list of child node_ids.
        node_idx:  The node index to start from (typically an element).
        max_length: Optional max number of characters to return (for truncation).

    Returns:
        str: Concatenated text of all descendant text nodes, truncated if needed.
    """
    buf = io.StringIO()
    node_idx_stack = [node_idx]

    # Perform a DFS traversal of all descendant nodes
    while node_idx_stack and (max_length is None or buf.tell() < max_length):
        node_idx = node_idx_stack.pop()
        node_type = document["nodes"]["nodeType"][node_idx]
        if node_type == NodeType.TEXT_NODE:
            # Append the raw string value for this text node
            buf.write(strings[document["nodes"]["nodeValue"][node_idx]])
        elif node_type == NodeType.ELEMENT_NODE:
            # Push children of this element onto the stack to continue traversal
            for child_idx in reversed(adjacency_list[node_idx]):
                node_idx_stack.append(child_idx)

    # Combine all text fragments into a single string
    text = buf.getvalue()

    # Apply optional length truncation for long text blocks
    if max_length is not None and len(text) > max_length:
        text = text[:max_length] + "..."

    return text


def _rendered_text_collection(
    *,
    document: DocumentSnapshot,
    strings: StringIndexed,
    queried_styles: list[str],
    node_layout_idx: dict[int, int],
    adjacency_list: dict[int, list[int]],
    node_idx: int,
) -> list[int | str]:
    """
    Recursively collect a mixed list of rendered text fragments and layout markers
    (e.g., newlines, tabs, paragraph breaks) for a DOM subtree.

    Unlike _descendant_text_content, this function respects computed styles and layout
    — it only includes text that is actually visible (display != none, visibility != hidden),
    and inserts special markers (integers) to represent structural breaks between elements.

    Returns a list containing:
        • str entries for visible text fragments
        • int entries representing layout breaks (1=newline, 2=paragraph break, etc.)

    This intermediate representation is later flattened and converted into readable
    text by `text_content()`.
    """
    nodes = document["nodes"]
    items: list[str | int] = []

    # --- Recurse into children first (depth-first traversal) ---
    for child in adjacency_list.get(node_idx, []):
        items.extend(
            _rendered_text_collection(
                document=document,
                strings=strings,
                queried_styles=queried_styles,
                node_layout_idx=node_layout_idx,
                adjacency_list=adjacency_list,
                node_idx=child,
            )
        )

    # Resolve this node's name (e.g., "DIV", "P", "OPTION")
    node_name = _node_name(nodes, node_idx, strings)
    # Skip elements that are not rendered (no layout entry) unless they are select-related nodes.
    if node_name not in ("SELECT", "OPTGROUP", "OPTION") and not _is_being_rendered(
        node_layout_idx, node_idx
    ):
        return items

    node_type = nodes["nodeType"][node_idx]
    # Retrieve the node's computed styles if layout info exists
    if (layout_idx := node_layout_idx.get(node_idx)) is not None:
        node_computed_styles = {
            style: strings[s_idx]
            for style, s_idx in zip(queried_styles, document["layout"]["styles"][layout_idx])
        }
    else:
        node_computed_styles = {}

    # Extract visibility-related properties
    computed_visibility = node_computed_styles.get("visibility")
    computed_display = node_computed_styles.get("display")

    # --- TEXT_NODEs: append visible text ---
    if (
        node_type == NodeType.TEXT_NODE
        and computed_visibility != "hidden"
        and computed_display != "none"
    ):
        # Determine text content from layout or node value
        if layout_idx is not None:
            text = strings[document["layout"]["text"][layout_idx]]
        else:
            text = strings[nodes["nodeValue"][node_idx]]

        # Normalize whitespace unless 'white-space: pre'
        if node_computed_styles.get("white-space") != "pre":
            text = re.sub(r"\s+", " ", text)

        items.append(text)

    # Paragraph elements trigger stronger breaks
    if node_type == NodeType.ELEMENT_NODE and node_name == "BR":
        items.append("\n")

    # simplification: always add a tab after table cells and newline after table rows
    if node_computed_styles.get("display") == "table-cell":
        items.append("\t")
    elif node_computed_styles.get("display") == "table-row":
        items.append("\n")

    # Paragraph elements trigger stronger breaks
    if node_type == NodeType.ELEMENT_NODE and node_name == "P":
        items.append(2)

    # Block-level elements add soft breaks before and after their content
    block_level_display = (
        "block",
        "flow-root",
        "flex",
        "grid",
        "table",
        "table-caption",
    )
    if node_type == NodeType.ELEMENT_NODE and computed_display in block_level_display:
        # Insert numeric markers before and after to preserve layout flow
        items.insert(0, 1)
        items.append(1)

    return items


def text_content(
    document: DocumentSnapshot,
    strings: StringIndexed,
    queried_styles: list[str],
    adjacency_list: dict[int, list[int]],
    node_idx: int,
    max_length: int | None = None,
) -> str:
    """
    Produce human-readable text for an element subtree, approximating what’s visually rendered.

    This respects layout/visibility via `_rendered_text_collection`, which returns a mixed list of:
      • str fragments (visible text)
      • int markers (layout breaks; e.g., 1=newline, 2=paragraph break)

    We then post-process that list to:
      • trim leading required breaks,
      • coalesce markers into actual '\n' insertions,
      • apply optional length truncation.
    """
    # Map each node id -> layout index for quick presence/lookup
    node_layout_idx = {
        n_idx: layout_idx for layout_idx, n_idx in enumerate(document["layout"]["nodeIndex"])
    }

    # If the root element itself isn't being rendered, fall back to a raw descendant text crawl.
    # (This ignores computed styles but ensures we still get some text for labeling.)
    # assert nodes["nodeType"][node_idx] == NodeType.ELEMENT_NODE
    if not _is_being_rendered(node_layout_idx, node_idx):
        return _descendant_text_content(document, strings, adjacency_list, node_idx, max_length)

    results: list[int | str] = []
    for child in adjacency_list.get(node_idx, []):
        results.extend(
            _rendered_text_collection(
                document=document,
                strings=strings,
                queried_styles=queried_styles,
                node_layout_idx=node_layout_idx,
                adjacency_list=adjacency_list,
                node_idx=child,
            )
        )

    # Skip leading structural markers until the first non-empty text fragment.
    try:
        start_idx = next(i for i, s in enumerate(results) if isinstance(s, str) and s)
    except StopIteration:
        return ""

    buf = io.StringIO()
    max_req_line_break: int | None = None

    for r in results[start_idx:]:
        if isinstance(r, str):
            # Emit pending line breaks before actual text
            if max_req_line_break is not None:
                buf.write("\n" * max_req_line_break)
                max_req_line_break = None

            buf.write(r)

            # Handle optional max_length truncation
            if max_length is not None and buf.tell() > max_length:
                break

        elif isinstance(r, int):
            # Integer break markers: keep the strongest (largest) until next text node
            if max_req_line_break is None or r > max_req_line_break:
                max_req_line_break = r

    # Final truncation safeguard (adds ellipsis when we exceeded max_length mid-write)
    text = buf.getvalue()
    if max_length is not None and len(text) > max_length:
        text = text[:max_length] + "..."

    return text


def _node_attributes(
    *, nodes: NodeTreeSnapshot, strings: StringIndexed, node_idx: int
) -> dict[str, str]:
    """
    Build a dict of attribute name -> value for a given element node.

    In the DOMSnapshot, attributes are stored as a flat list of string indices:
      [name_idx_0, value_idx_0, name_idx_1, value_idx_1, ...]
    where each index points into the shared `strings` table.

    This helper:
      1. Resolves those indices into actual strings,
      2. Pairs them into (name, value) tuples,
      3. Returns them as a normal Python dict.
    """
    # Resolve attribute string indices into actual strings, using None for missing (-1) entries.
    elt_attrs_list = [strings[idx] if idx != -1 else None for idx in nodes["attributes"][node_idx]]

    # Pair up [name, value, name, value, ...] into (name, value) and build a dict.
    # The * 2 trick groups the flat list into chunks of two.
    elt_attrs = {k: v for k, v in zip(*[iter(elt_attrs_list)] * 2)}

    return elt_attrs


def _fixup_select_elements(
    *,
    document: DocumentSnapshot,
    queried_styles: list[str],
    adjacency_list: dict[int, list[int]],
    strings: StringIndexed,
    pending_select_elements: list[Element],
) -> dict[int, SelectInputElement]:
    """
    Convert generic <select> Element objects into fully populated SelectInputElement objects.

    During the initial element extraction, <select> nodes are detected but not yet populated
    with their <option> children. This function performs a second pass to:

        • Find all descendant <option> nodes for each <select>,
        • Extract the visible text for each option,
        • Extract the underlying value attributes (falling back to text if missing),
        • Detect which options are selected,
        • Construct SelectInputElement instances containing:
              - is_multi_select flag
              - list of option texts
              - list of option values
              - list of selected option indices

    Returns a mapping: {select_node_id -> SelectInputElement}
    """

    nodes = document["nodes"]

    # select_node_id -> [option_node_ids]
    select_options = defaultdict(list)

    # Collect all option descendants for each pending <select> element, perform DFS
    # starting at the SELECT node and looking for OPTION nodes
    for select_element in pending_select_elements:
        stack = list(reversed(adjacency_list[select_element.node_id]))
        while stack:
            child_idx = stack.pop()
            if _node_name(nodes, child_idx, strings) == Elements.OPTION:
                select_options[select_element.node_id].append(child_idx)

            stack.extend(reversed(adjacency_list[child_idx]))

    # 2. For each <select>, transform its raw Element into a SelectInputElement with options.
    parsed_select_elements = {}
    for select_element in pending_select_elements:
        option_nodes = sorted(select_options[select_element.node_id])
        option_text = {}
        option_value = {}
        selected_option_indices: list[int] = []

        # Extract text and values for each <option>, and determine which are selected.
        for option_index, n_idx in enumerate(option_nodes):
            attrs = _node_attributes(nodes=nodes, strings=strings, node_idx=n_idx)

            # Extract the visible or computed text of this option
            option_text[n_idx] = text_content(
                document, strings, queried_styles, adjacency_list, n_idx
            )

            # Determine its value attribute, falling back to the text content if omitted
            if "value" in attrs:
                option_value[n_idx] = attrs["value"]
            else:
                option_value[n_idx] = option_text[n_idx]

            # Detect selected <option> (CDP snapshot stores selected nodes separately)
            if n_idx in nodes.get("optionSelected", {})["index"]:
                selected_option_indices.append(option_index)

        # Prepare a copy of the base Element attributes for constructing the subclass
        select_attrs = select_element.model_dump()
        select_attrs["bounds"] = Rect(**select_attrs["bounds"])

        # Build a fully populated SelectInputElement
        parsed_select_elements[select_element.node_id] = SelectInputElement(
            **select_attrs,
            is_multi_select="multiple" in select_element.attributes,
            options=[option_text[n_idx] for n_idx in option_nodes],
            option_values=[option_value[n_idx] for n_idx in option_nodes],
            selected_option_indices=selected_option_indices,
        )

    return parsed_select_elements


def parse_adjacency_list(*, document: DocumentSnapshot) -> dict[int, list[int]]:
    """
    Make "adjacency list" representation of the DOM nodes (a list of children for each node ID)
    from doc snapshot
    """
    nodes = document["nodes"]
    adjacency_list = defaultdict(list)
    for node_idx, parent_idx in enumerate(nodes["parentIndex"]):
        if parent_idx != -1:
            adjacency_list[parent_idx].append(node_idx)
    return adjacency_list


def get_elements_in_viewport(
    *,
    document: DocumentSnapshot,
    adjacency_list: dict[int, list[int]],
    strings: StringIndexed,
    computed_styles: list[str],
    doc_absolute_bounds: Rect,
    visible_rect: Rect,
    is_root_frame: bool,
) -> dict[int, Element]:
    frame_id = strings[document["frameId"]]
    nodes = document["nodes"]
    layout = document["layout"]
    element_by_id: dict[int, Element] = {}

    if (root_node_idx := _get_root_node_idx(document)) is None or (
        root_rects := _get_client_and_scroll_rects_by_node_idx(
            document=document, node_idx=root_node_idx
        )
    ) is None:
        # shouldn't happen, but if it does it's an odd case, bail out
        return {}

    root_client_rect, root_scroll_rect = root_rects
    pending_select_elements = list[Element]()
    for layout_idx, node_idx in enumerate(layout["nodeIndex"]):
        if nodes["nodeType"][node_idx] != NodeType.ELEMENT_NODE:
            continue

        elt_name = strings[nodes["nodeName"][node_idx]]
        if node_idx == root_node_idx:
            bounds = Rect.from_cdp((0, 0, document["contentWidth"], document["contentHeight"]))
        else:
            bounds = Rect.from_cdp(layout["bounds"][layout_idx])

        bounds = bounds.translate(doc_absolute_bounds.x, doc_absolute_bounds.y)
        if not is_root_frame:
            # all coordinates/bounds should be relative to the root document.
            # To get the coordinates of an element inside a scrolled iframe, we need to translate
            # both by the absolute position of the iframe in the root doc, and by the scroll
            # position of the iframe
            bounds = bounds.translate(-root_scroll_rect.x, -root_scroll_rect.y)
            bounds = bounds.clip_to(doc_absolute_bounds)

        if node_idx != root_node_idx and not bounds.intersects_with(visible_rect):
            continue

        bounds = bounds.clip_to(visible_rect)

        styles = {
            style: strings[s_idx]
            for style, s_idx in zip(computed_styles, layout["styles"][layout_idx])
        }
        area = bounds.width * bounds.height
        if styles["display"] == "none" or styles["visibility"] == "hidden" or area < 2:
            continue

        scroll_rect = Rect.from_cdp(layout["scrollRects"][layout_idx])
        client_rect = Rect.from_cdp(layout["clientRects"][layout_idx])
        scrollable_styles = ["auto", "scroll"]
        if elt_name == "HTML":
            scrollable_styles.append("visible")

        scrollable_x = styles.get("overflow-x") in scrollable_styles
        can_scroll_left = scrollable_x and scroll_rect.x > 0
        can_scroll_right = scrollable_x and (scroll_rect.x + client_rect.width) < scroll_rect.width

        scrollable_y = styles.get("overflow-x") in scrollable_styles
        can_scroll_up = scrollable_y and scroll_rect.y > 0
        can_scroll_down = scrollable_y and (scroll_rect.y + client_rect.height) < scroll_rect.height

        element = Element(
            frame_id=frame_id,
            parent_id=nodes["parentIndex"][node_idx],
            node_id=node_idx,
            backend_node_id=nodes["backendNodeId"][node_idx],
            name=elt_name,
            attributes=_node_attributes(nodes=nodes, strings=strings, node_idx=node_idx),
            styles=styles,
            bounds=bounds,
            is_clickable=node_idx in nodes.get("isClickable", {"index": []})["index"],
            can_scroll_up=can_scroll_up,
            can_scroll_down=can_scroll_down,
            can_scroll_left=can_scroll_left,
            can_scroll_right=can_scroll_right,
        )

        if elt_name in ("INPUT", "TEXTAREA"):
            element = _make_input_element(nodes, element, strings)

        element_by_id[node_idx] = element
        if element.name == "BODY":
            new_element = _apply_overflow_viewport_propagation(
                body_element=element,
                elements_by_id=element_by_id,
                root_client_rect=root_client_rect,
                root_scroll_rect=root_scroll_rect,
            )
            if new_element:
                element_by_id[element.parent_id] = new_element

        elif element.name == "SELECT":
            pending_select_elements.append(element)

    updated_selects = _fixup_select_elements(
        document=document,
        queried_styles=computed_styles,
        adjacency_list=adjacency_list,
        strings=strings,
        pending_select_elements=pending_select_elements,
    )
    element_by_id.update(updated_selects)

    return element_by_id


def annotate_screenshot(img_bytes: bytes, viewport: Rect, elements: list[Element]) -> bytes:
    with (
        io.BytesIO(img_bytes) as img_buf,
        importlib.resources.path(annotation_font, "FiraCode-Regular.ttf") as font_path,
    ):
        img = Image.open(img_buf)
        draw = ImageDraw.Draw(img)
        for i, elt in enumerate(elements):
            # bounds are relative to global page
            elt_bounds = elt.bounds.translate(-viewport.x, -viewport.y)
            bbox = [
                elt_bounds.x,
                elt_bounds.y,
                elt_bounds.x + elt_bounds.width,
                elt_bounds.y + elt_bounds.height,
            ]
            background_color, text_color = _ELEMENT_COLORS.get(elt.name.lower(), _DEFAULT_COLOR)
            draw.rectangle(bbox, outline=background_color, width=2)

            # Add text
            text = str(i)
            bbox_width_delta, bbox_height_delta = 0, 0
            font: ImageFont.ImageFont | ImageFont.FreeTypeFont
            try:
                font = ImageFont.truetype(font_path.as_posix())
                # tinkered with these values to get the bounding box to tightly hug text
                bbox_width_delta = 2
                bbox_height_delta = 5
            except Exception:
                font = ImageFont.load_default()

            # Get text size
            text_bbox = draw.textbbox((0, 0), text, font=font)
            text_width = text_bbox[2] - text_bbox[0] - bbox_width_delta
            text_height = text_bbox[3] - text_bbox[1] - bbox_height_delta

            # Calculate position (upper-right corner of bounding box)
            text_x = bbox[2] - text_width  # Right align
            text_y = bbox[1]  # Top of bounding box

            # Draw text background
            padding = 4
            draw.rectangle(
                (
                    text_x - padding,
                    text_y,
                    text_x + text_width + padding,
                    text_y + text_height + padding,
                ),
                fill=background_color,
            )

            # Draw text
            draw.text((text_x, text_y), text, font=font, fill=text_color)

        with io.BytesIO() as out_buf:
            img.save(out_buf, format="PNG")
            return out_buf.getvalue()


def _has_direct_pointer_style(e: Element, in_viewport: dict[int, Element]) -> bool:
    if not has_pointer_style(e):
        return False

    # now, check parents: if parent has pointer style, then this element doesn't have
    # direct pointer style
    if (parent_element := in_viewport.get(e.parent_id)) is not None and has_pointer_style(
        parent_element
    ):
        return False

    return True


def is_interactive(e: Element, in_viewport: dict[int, Element]) -> bool:
    if e.can_scroll_up or e.can_scroll_down or e.can_scroll_left or e.can_scroll_right:
        return True

    if is_disabled_element(e):
        return False

    return is_commonly_interactable_element(e) or _has_direct_pointer_style(e, in_viewport)
