from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Confidence = Literal["confirmed", "likely", "unknown"]


class Evidence(StrictModel):
    source_id: str
    location: str
    detail: str


class ApiParameter(StrictModel):
    name: str
    location: Literal["path", "query", "header", "body"]
    required: bool
    description: str


class ApiEndpoint(StrictModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str
    summary: str
    description: str = ""
    parameters: list[ApiParameter] = Field(default_factory=list)
    response_shape: str = ""
    response_fields: list[str] = Field(default_factory=list)
    record_selector: str
    primary_key_candidates: list[str] = Field(default_factory=list)
    cursor_candidates: list[str] = Field(default_factory=list)
    pagination_hint: str
    confidence: Confidence
    evidence: list[Evidence] = Field(default_factory=list)


class ApiKnowledge(StrictModel):
    provider_name: str = ""
    title: str
    summary: str
    base_urls: list[str] = Field(default_factory=list)
    auth_methods: list[str] = Field(default_factory=list)
    endpoints: list[ApiEndpoint] = Field(default_factory=list)
    pagination_patterns: list[str] = Field(default_factory=list)
    rate_limits: list[str] = Field(default_factory=list)
    error_patterns: list[str] = Field(default_factory=list)
    common_headers: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class PlanKeyValue(StrictModel):
    key: str
    value: str


class PlanInput(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    title: str
    type: Literal["string", "integer", "number", "boolean"]
    secret: bool
    required: bool
    description: str


class PlanAuth(StrictModel):
    method: Literal["none", "api_key", "bearer", "basic", "oauth2", "jwt", "session_token"]
    header: str
    inject_into: Literal["header", "request_parameter"]
    token_url: str
    scopes: str
    grant_type: str
    login_path: str
    token_path: str
    session_header: str
    jwt_algorithm: str
    confidence: Confidence
    evidence: list[Evidence] = Field(default_factory=list)


class PlanPagination(StrictModel):
    mode: Literal["none", "page", "offset", "cursor", "link_header"]
    page_param: str
    size_param: str
    page_size: int
    cursor_path: str
    confidence: Confidence
    evidence: list[Evidence] = Field(default_factory=list)


class PlanRequestBody(StrictModel):
    mode: Literal["json", "form"] = "json"
    entries: list[PlanKeyValue] = Field(default_factory=list)


class PlanPartition(StrictModel):
    mode: Literal["none", "list", "parent"] = "none"
    values: str = ""
    param: str = ""
    inject_into: Literal["request_parameter", "header", "body_data", "body_json"] = "request_parameter"
    cursor_field: str = ""
    parent_stream: str = ""
    parent_key: str = ""
    partition_field: str = ""
    incremental_parent: bool = False
    confidence: Confidence = "unknown"
    evidence: list[Evidence] = Field(default_factory=list)


class PlanTransformation(StrictModel):
    type: Literal["add", "remove"]
    path: str
    value: str = ""


class PlanBackoff(StrictModel):
    mode: Literal["none", "constant", "exponential", "header"] = "none"
    seconds: int = 0
    factor: int = 0
    header: str = ""


class PlanResponseFilter(StrictModel):
    http_codes: list[int] = Field(default_factory=list)
    predicate: str = ""
    action: Literal["RETRY", "RATE_LIMITED", "IGNORE", "FAIL"]
    message: str = ""


class PlanErrorHandler(StrictModel):
    max_retries: int = 0
    backoff: PlanBackoff = Field(default_factory=PlanBackoff)
    filters: list[PlanResponseFilter] = Field(default_factory=list)


class PlanStream(StrictModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    path: str
    http_method: Literal["GET", "POST"]
    record_selector: str
    primary_key: str
    incremental: bool
    cursor_field: str
    cursor_param: str
    cursor_end_param: str = ""
    cursor_format: str = ""
    cursor_inject_into: Literal["request_parameter", "header", "body_data", "body_json"] = "request_parameter"
    cursor_filter_mode: Literal["server", "client"] = "server"
    step: str = ""
    lookback: str = ""
    query_params: list[PlanKeyValue] = Field(default_factory=list)
    headers: list[PlanKeyValue] = Field(default_factory=list)
    request_body: PlanRequestBody = Field(default_factory=PlanRequestBody)
    pagination: PlanPagination
    partition: PlanPartition = Field(default_factory=PlanPartition)
    transformations: list[PlanTransformation] = Field(default_factory=list)
    error_handler: PlanErrorHandler = Field(default_factory=PlanErrorHandler)
    confidence: Confidence
    evidence: list[Evidence] = Field(default_factory=list)


class ConnectorPlan(StrictModel):
    provider_name: str = ""
    name: str
    description: str
    icon: Literal[
        "api", "database", "users", "commerce", "finance", "analytics",
        "workflow", "support", "files", "custom",
    ]
    base_url: str
    auth: PlanAuth
    user_inputs: list[PlanInput] = Field(default_factory=list)
    streams: list[PlanStream] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unsupported_features: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class AgentOperation(StrictModel):
    op: Literal["add", "replace", "remove"]
    path: str
    # JSON encoded as a string keeps the tool schema strict while still
    # allowing an operation to carry a list or object.
    value_json: str
    label: str


class AgentAnswer(StrictModel):
    assistant_message: str
    change_summary: str
    operations: list[AgentOperation] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


def plan_to_definition(plan: ConnectorPlan) -> dict:
    user_inputs = [
        {
            "key": item.key,
            "title": item.title,
            "type": item.type,
            "secret": item.secret,
            "required": item.required,
            "description": item.description,
        }
        for item in plan.user_inputs
    ]
    streams = []
    for stream in plan.streams:
        pagination: dict = {"mode": stream.pagination.mode}
        if stream.pagination.mode in {"page", "offset"}:
            if stream.pagination.page_param:
                pagination["page_param"] = stream.pagination.page_param
            if stream.pagination.size_param:
                pagination["size_param"] = stream.pagination.size_param
            if stream.pagination.page_size > 0:
                pagination["page_size"] = stream.pagination.page_size
        if stream.pagination.mode == "cursor" and stream.pagination.cursor_path:
            pagination["cursor_path"] = stream.pagination.cursor_path
        compiled_stream = {
            "name": stream.name,
            "path": stream.path,
            "http_method": stream.http_method,
            "record_selector": stream.record_selector,
            "record_filter": "",
            "primary_key": stream.primary_key,
            "pagination": pagination,
            "incremental": stream.incremental,
            "query_params": [item.model_dump() for item in stream.query_params],
            "headers": [item.model_dump() for item in stream.headers],
            "request_body": {
                "mode": stream.request_body.mode,
                "entries": [item.model_dump() for item in stream.request_body.entries],
            },
            "partition": {
                "mode": stream.partition.mode,
                **({"values": stream.partition.values} if stream.partition.values else {}),
                **({"param": stream.partition.param} if stream.partition.param else {}),
                **({"inject_into": stream.partition.inject_into}
                   if stream.partition.mode != "none" else {}),
                **({"cursor_field": stream.partition.cursor_field}
                   if stream.partition.cursor_field else {}),
                **({"parent_stream": stream.partition.parent_stream}
                   if stream.partition.parent_stream else {}),
                **({"parent_key": stream.partition.parent_key}
                   if stream.partition.parent_key else {}),
                **({"partition_field": stream.partition.partition_field}
                   if stream.partition.partition_field else {}),
                **({"incremental_parent": stream.partition.incremental_parent}
                   if stream.partition.incremental_parent else {}),
            },
            "transformations": [
                {"type": item.type, "path": item.path,
                 **({"value": item.value} if item.type == "add" else {})}
                for item in stream.transformations
            ],
            "error_handler": {
                "max_retries": stream.error_handler.max_retries,
                "backoff": {
                    "mode": stream.error_handler.backoff.mode,
                    **({"seconds": stream.error_handler.backoff.seconds}
                       if stream.error_handler.backoff.mode == "constant" else {}),
                    **({"factor": stream.error_handler.backoff.factor}
                       if stream.error_handler.backoff.mode == "exponential" else {}),
                    **({"header": stream.error_handler.backoff.header}
                       if stream.error_handler.backoff.mode == "header" else {}),
                },
                "filters": [item.model_dump() for item in stream.error_handler.filters],
            },
        }
        if stream.cursor_field:
            compiled_stream["cursor_field"] = stream.cursor_field
        if stream.cursor_param:
            compiled_stream["cursor_param"] = stream.cursor_param
        if stream.cursor_end_param:
            compiled_stream["cursor_end_param"] = stream.cursor_end_param
        if stream.cursor_format:
            compiled_stream["cursor_format"] = stream.cursor_format
        if stream.cursor_param or stream.cursor_end_param:
            compiled_stream["cursor_inject_into"] = stream.cursor_inject_into
            compiled_stream["cursor_filter_mode"] = stream.cursor_filter_mode
        if stream.step:
            compiled_stream["step"] = stream.step
        if stream.lookback:
            compiled_stream["lookback"] = stream.lookback
        streams.append(compiled_stream)
    auth: dict = {"method": plan.auth.method}
    if plan.auth.header:
        auth["header"] = plan.auth.header
    if plan.auth.method in {"api_key", "bearer"}:
        auth["inject_into"] = plan.auth.inject_into
    if plan.auth.method == "oauth2":
        auth["oauth"] = {
            "token_url": plan.auth.token_url,
            "scopes": plan.auth.scopes,
            "grant_type": plan.auth.grant_type or "refresh_token",
        }
    if plan.auth.method == "session_token":
        auth["session"] = {
            "login_path": plan.auth.login_path,
            "token_path": plan.auth.token_path,
            "header": plan.auth.session_header,
        }
    if plan.auth.method == "jwt":
        auth["jwt"] = {
            "algorithm": plan.auth.jwt_algorithm or "HS256", "token_duration": 1200,
        }
    return {
        "name": plan.name,
        "base_url": plan.base_url,
        "auth": auth,
        "user_inputs": user_inputs,
        "streams": streams,
    }
