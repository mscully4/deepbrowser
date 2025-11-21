from enum import IntEnum
from typing import Any, Literal, NewType, NotRequired, Protocol, TypedDict

from pydantic import BaseModel, Field


StringIndex = NewType("StringIndex", int)


class StringIndexed(Protocol):
    def __getitem__(self, item: StringIndex) -> str: ...


class RareBooleanData(TypedDict):
    index: list[int]


class NodeType(IntEnum):
    # enum values used by CDP, from the HTML spec
    # https://dom.spec.whatwg.org/#ref-for-dom-node-nodetype%E2%91%A0
    ELEMENT_NODE = 1
    ATTRIBUTE_NODE = 2
    TEXT_NODE = 3
    CDATA_SECTION_NODE = 4
    PROCESSING_INSTRUCTION_NODE = 7
    COMMENT_NODE = 8
    DOCUMENT_NODE = 9
    DOCUMENT_TYPE_NODE = 10
    DOCUMENT_FRAGMENT_NODE = 11


NodeTypeLiteral = Literal[
    NodeType.ELEMENT_NODE,
    NodeType.ATTRIBUTE_NODE,
    NodeType.TEXT_NODE,
    NodeType.CDATA_SECTION_NODE,
    NodeType.PROCESSING_INSTRUCTION_NODE,
    NodeType.COMMENT_NODE,
    NodeType.DOCUMENT_NODE,
    NodeType.DOCUMENT_TYPE_NODE,
    NodeType.DOCUMENT_FRAGMENT_NODE,
]


class RareIntegerData(TypedDict):
    index: list[int]
    value: list[int]


class RareStringData(TypedDict):
    index: list[int]
    value: list[StringIndex]


class NodeTreeSnapshot(TypedDict):
    parentIndex: NotRequired[list[int]]
    nodeType: NotRequired[list[NodeTypeLiteral]]
    nodeName: NotRequired[list[StringIndex]]
    nodeValue: NotRequired[list[StringIndex]]
    textValue: NotRequired[RareStringData]
    inputValue: NotRequired[RareStringData]
    inputChecked: NotRequired[RareBooleanData]
    optionSelected: NotRequired[RareBooleanData]
    backendNodeId: NotRequired[list[int]]
    contentDocumentIndex: NotRequired[RareIntegerData]
    attributes: NotRequired[list[list[StringIndex]]]
    isClickable: NotRequired[RareBooleanData]


class LayoutTreeSnapshot(TypedDict):
    nodeIndex: list[int]
    styles: list[list[StringIndex]]
    text: list[StringIndex]
    bounds: list[tuple[int, int, int, int]]
    offsetRects: list[tuple[int, int, int, int]]
    scrollRects: list[tuple[int, int, int, int]]
    clientRects: list[tuple[int, int, int, int]]


class DocumentSnapshot(TypedDict):
    nodes: NodeTreeSnapshot
    layout: LayoutTreeSnapshot
    frameId: StringIndex
    contentWidth: int
    contentHeight: int


class DomSnapshot(TypedDict):
    documents: list[DocumentSnapshot]
    strings: list[str]


class RemoteObject(TypedDict):
    type: str
    subtype: NotRequired[str]
    className: NotRequired[str]
    description: NotRequired[str]
    objectId: NotRequired[str]


class CallFrame(TypedDict):
    functionName: str
    scriptId: str
    url: str
    lineNumber: int
    columnNumber: int


class HeaderEntry(TypedDict):
    name: str
    value: str


class PostDataEntry(TypedDict):
    bytes: NotRequired[str]


class TrustTokenParams(TypedDict):
    operation: Literal["Issuance", "Redemption", "Signing"]
    refreshPolicy: Literal["UseCached", "Refresh"]
    issuers: NotRequired[list[str]]


class Request(TypedDict):
    url: str
    method: str
    headers: dict[str, str]
    initialPriority: Literal["VeryLow", "Low", "Medium", "High", "VeryHigh"]
    referrerPolicy: Literal[
        "unsafe-url",
        "no-referrer-when-downgrade",
        "no-referrer",
        "origin",
        "origin-when-cross-origin",
        "same-origin",
        "strict-origin",
        "strict-origin-when-cross-origin",
    ]
    urlFragment: NotRequired[str]
    hasPostData: NotRequired[bool]
    postDataEntries: NotRequired[list[PostDataEntry]]
    mixedContentType: NotRequired[str]
    isLinkPreload: NotRequired[bool]
    trustTokenParams: NotRequired[TrustTokenParams]
    isSameSite: NotRequired[bool]


class RequestPausedEvent(TypedDict):
    requestId: str
    request: Request
    frameId: str
    resourceType: Literal[
        "Document",
        "Stylesheet",
        "Image",
        "Media",
        "Font",
        "Script",
        "TextTrack",
        "XHR",
        "Fetch",
        "Prefetch",
        "EventSource",
        "WebSocket",
        "Manifest",
        "SignedExchange",
        "Ping",
        "CSPViolationReport",
        "Preflight",
        "Other",
    ]
    responseErrorReason: NotRequired[str]
    responseStatusCode: NotRequired[int]
    responseStatusText: NotRequired[str]
    responseHeaders: NotRequired[list[HeaderEntry]]
    networkId: NotRequired[str]
    redirectedRequestId: NotRequired[str]


class Initiator(TypedDict):
    type: Literal["parser", "script", "preload", "SignedExchange", "preflight", "other"]
    stack: NotRequired[dict[str, Any]]
    url: NotRequired[str]
    lineNumber: NotRequired[float]
    columnNumber: NotRequired[float]
    requestId: NotRequired[str]


class StackTrace(TypedDict):
    callFrames: list[CallFrame]


class PausedRequest(BaseModel):
    request_id: str
    url: str
    headers: dict[str, str] = {}
    has_post_data: bool = False
    post_data: str | None = Field(
        ...,
        description=(
            "Post data body as string. May be None, even if has_post_data is"
            " True, if the body is too long or can't be decoded to a string"
        ),
    )


class RequestInfo(TypedDict):
    hasUserGesture: bool
    initiator: Initiator
    event: RequestPausedEvent


class MockResponse(BaseModel):
    status_code: int
    headers: list[tuple[str, str]] = []
    body: str


class ResponseInfo(TypedDict):
    event: RequestPausedEvent
    body: bytes | None


class PausedMessage(BaseModel):
    """Represents a paused network request or response."""

    request_id: str
    message_type: Literal["request", "response"]
    url: str
    method: str
    request_headers: dict[str, str] = {}
    has_post_data: bool = False
    post_data: str | None = Field(
        ...,
        description=(
            "Post data body as string. May be None, even if has_post_data is"
            " True, if the body is too long or can't be decoded to a string"
        ),
    )
    response_code: int | None = None
    response_headers: list[tuple[str, str]] | None = None
    response_body: bytes | None = None
