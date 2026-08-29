from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import yaml

from app.services.builder_ai.schemas import (
    ApiEndpoint, ApiKnowledge, ApiParameter, Evidence,
)


class _HTMLText(HTMLParser):
    def __init__(self, base_url: str = "") -> None:
        super().__init__()
        self.base_url = base_url
        self.text: list[str] = []
        self.links: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(urljoin(self.base_url, href))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored and data.strip():
            self.text.append(data.strip())


def html_text_and_links(document: str, base_url: str = "") -> tuple[str, list[str]]:
    parser = _HTMLText(base_url)
    parser.feed(document)
    return "\n".join(parser.text), parser.links


def textual_content(name: str, mime_type: str | None, content: bytes) -> str | None:
    suffix = Path(name).suffix.lower()
    if (mime_type or "").startswith("text/") or suffix in {
        ".txt", ".md", ".html", ".htm", ".json", ".yaml", ".yml",
    }:
        decoded = content.decode("utf-8", errors="replace")
        if suffix in {".html", ".htm"} or mime_type == "text/html":
            return html_text_and_links(decoded)[0]
        return decoded
    return None


def _load_document(name: str, text: str) -> dict[str, Any] | list[Any] | None:
    try:
        if Path(name).suffix.lower() == ".json" or text.lstrip().startswith(("{", "[")):
            value = json.loads(text)
        else:
            value = yaml.safe_load(text)
    except (ValueError, yaml.YAMLError):
        return None
    return value if isinstance(value, (dict, list)) else None


def _resolve_schema(document: dict[str, Any], schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/"):
        current: Any = document
        for part in reference[2:].split("/"):
            if not isinstance(current, dict):
                return {}
            current = current.get(part.replace("~1", "/").replace("~0", "~"))
        return _resolve_schema(document, current)
    if isinstance(schema.get("allOf"), list):
        properties: dict[str, Any] = {}
        for item in schema["allOf"]:
            properties.update(_resolve_schema(document, item).get("properties") or {})
        return {**schema, "properties": properties}
    return schema


def _record_schema(
    document: dict[str, Any], schema: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    resolved = _resolve_schema(document, schema)
    if resolved.get("type") == "array" or "items" in resolved:
        return _resolve_schema(document, resolved.get("items")), "", "array"
    for key, value in (resolved.get("properties") or {}).items():
        candidate = _resolve_schema(document, value)
        if candidate.get("type") == "array" or "items" in candidate:
            return _resolve_schema(document, candidate.get("items")), str(key), "object envelope"
    return resolved, "", str(resolved.get("type") or "object")


def _schema_candidates(schema: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    if not schema:
        return [], []
    properties = schema.get("properties") or {}
    primary = [key for key in properties if key.lower() in {"id", "uuid", "key"}]
    cursors = [
        key for key in properties
        if key.lower() in {"updated_at", "updatedat", "modified_at", "created_at", "timestamp"}
    ]
    return primary[:3], cursors[:3]


def _schema_error_patterns(
    document: dict[str, Any],
    schema: dict[str, Any],
) -> list[str]:
    resolved = _resolve_schema(document, schema)
    properties = resolved.get("properties") or {}
    if "code" not in properties or "message" not in properties:
        return []
    descriptions = [
        str(resolved.get("description") or ""),
        str((properties.get("code") or {}).get("description") or ""),
        str((properties.get("message") or {}).get("description") or ""),
    ]
    text = " ".join(descriptions).casefold()
    if re.search(r"code\s*[:=]?\s*0.*(fail|error|invalid|reject|not accepted)", text):
        return [
            "HTTP 200 can still be an API-level failure when response.get('code') == 0; "
            "handle it with a response predicate.",
        ]
    if re.search(r"(fail|error|invalid|reject|not accepted).*code\s*[:=]?\s*0", text):
        return [
            "HTTP 200 can still be an API-level failure when response.get('code') == 0; "
            "handle it with a response predicate.",
        ]
    if "success" in text and re.search(r"code\s*[:=]?\s*1", text):
        return [
            "HTTP 200 response body carries code/message status fields; verify non-success "
            "codes with a response predicate.",
        ]
    return [
        "HTTP 200 response body carries code/message status fields; verify whether the "
        "API reports failures in the body.",
    ]


def parse_openapi(source_id: str, document: dict[str, Any]) -> ApiKnowledge | None:
    if not (document.get("openapi") or document.get("swagger")):
        return None
    info = document.get("info") or {}
    servers = document.get("servers") or []
    base_urls = [item.get("url", "") for item in servers if isinstance(item, dict) and item.get("url")]
    if not base_urls and document.get("host"):
        schemes = document.get("schemes") or ["https"]
        base_path = document.get("basePath") or ""
        base_urls = [f"{schemes[0]}://{document['host']}{base_path}"]

    security_schemes = ((document.get("components") or {}).get("securitySchemes") or {})
    if not security_schemes:
        security_schemes = document.get("securityDefinitions") or {}
    auth_methods = [
        str((value or {}).get("type") or key)
        for key, value in security_schemes.items()
    ]
    endpoints: list[ApiEndpoint] = []
    error_patterns: list[str] = []
    for path, path_item in (document.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            parameters: list[ApiParameter] = []
            for item in [*(path_item.get("parameters") or []), *(operation.get("parameters") or [])]:
                if not isinstance(item, dict) or "$ref" in item:
                    continue
                where = item.get("in", "query")
                if where not in {"path", "query", "header", "body"}:
                    where = "body"
                parameters.append(ApiParameter(
                    name=str(item.get("name") or "value"), location=where,
                    required=bool(item.get("required")),
                    description=str(item.get("description") or ""),
                ))
            response_schema: dict[str, Any] = {}
            responses = operation.get("responses") or {}
            success = responses.get("200") or responses.get("201") or responses.get("default") or {}
            if isinstance(success, dict):
                content = success.get("content") or {}
                media = content.get("application/json") or (next(iter(content.values()), {}) if content else {})
                response_schema = (media or {}).get("schema") or success.get("schema") or {}
            error_patterns.extend(_schema_error_patterns(document, response_schema))
            record_schema, record_selector, response_shape = _record_schema(document, response_schema)
            candidates, cursors = _schema_candidates(record_schema)
            response_fields = list((record_schema.get("properties") or {}).keys())[:100]
            pagination_hint = ""
            parameter_names = {item.name.casefold() for item in parameters}
            if parameter_names & {"cursor", "after", "next_cursor", "page_token"}:
                pagination_hint = "cursor parameter"
            elif parameter_names & {"offset", "skip"}:
                pagination_hint = "offset parameter"
            elif parameter_names & {"page", "page_number", "pagenumber"}:
                pagination_hint = "page number parameter"
            evidence = [Evidence(
                source_id=source_id, location=f"paths.{path}.{method}",
                detail="Endpoint declared in the API specification",
            )]
            endpoints.append(ApiEndpoint(
                method=method.upper(), path=str(path),
                summary=str(operation.get("summary") or operation.get("operationId") or ""),
                description=str(operation.get("description") or ""),
                parameters=parameters, record_selector=record_selector,
                response_shape=response_shape, response_fields=response_fields,
                primary_key_candidates=candidates, cursor_candidates=cursors,
                pagination_hint=pagination_hint, confidence="confirmed", evidence=evidence,
            ))
    return ApiKnowledge(
        title=str(info.get("title") or "Imported API"),
        summary=str(info.get("description") or ""), base_urls=base_urls,
        auth_methods=auth_methods, endpoints=endpoints, unknowns=[],
        pagination_patterns=sorted({item.pagination_hint for item in endpoints if item.pagination_hint}),
        error_patterns=sorted(set(error_patterns)),
        evidence=[Evidence(
            source_id=source_id, location="root",
            detail="Parsed deterministically as OpenAPI/Swagger",
        )],
    )


def _postman_items(items: list[Any], prefix: str = "") -> list[tuple[str, dict[str, Any]]]:
    output: list[tuple[str, dict[str, Any]]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = f"{prefix}/{item.get('name', '')}".strip("/")
        if isinstance(item.get("item"), list):
            output.extend(_postman_items(item["item"], name))
        elif isinstance(item.get("request"), dict):
            output.append((name, item["request"]))
    return output


def parse_postman(source_id: str, document: dict[str, Any]) -> ApiKnowledge | None:
    schema = str((document.get("info") or {}).get("schema") or "")
    if "schema.getpostman.com" not in schema and not isinstance(document.get("item"), list):
        return None
    endpoints: list[ApiEndpoint] = []
    base_urls: list[str] = []
    auth_methods: list[str] = []
    for location, request in _postman_items(document.get("item") or []):
        method = str(request.get("method") or "GET").upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            continue
        raw_url = request.get("url")
        parameters: list[ApiParameter] = []
        if isinstance(raw_url, dict):
            for item in raw_url.get("query") or []:
                if isinstance(item, dict) and item.get("key"):
                    parameters.append(ApiParameter(
                        name=str(item["key"]), location="query",
                        required=not bool(item.get("disabled")),
                        description=str(item.get("description") or ""),
                    ))
            raw_url = raw_url.get("raw") or ""
        raw_url = str(raw_url or "")
        path = raw_url
        match = re.match(r"https?://[^/]+(?P<path>/[^?#]*)", raw_url)
        if match:
            path = match.group("path")
            base = raw_url[:match.start("path")]
            if base not in base_urls:
                base_urls.append(base)
        for item in request.get("header") or []:
            if isinstance(item, dict) and item.get("key"):
                parameters.append(ApiParameter(
                    name=str(item["key"]), location="header",
                    required=not bool(item.get("disabled")),
                    description=str(item.get("description") or ""),
                ))
        body = request.get("body") or {}
        body_mode = str(body.get("mode") or "") if isinstance(body, dict) else ""
        body_rows = body.get(body_mode) or [] if isinstance(body, dict) else []
        for item in body_rows:
            if isinstance(item, dict) and item.get("key"):
                key = str(item["key"])
                parameters.append(ApiParameter(
                    name=key, location="body",
                    required=not bool(item.get("disabled")),
                    description=str(item.get("description") or ""),
                ))
                if key.casefold() in {
                    "access_token", "access_token_v2", "api_key", "apikey", "token",
                }:
                    body_auth = f"{body_mode or 'body'} parameter: {key}"
                    if body_auth not in auth_methods:
                        auth_methods.append(body_auth)
        auth_type = str((request.get("auth") or {}).get("type") or "")
        if auth_type and auth_type not in auth_methods:
            auth_methods.append(auth_type)
        endpoints.append(ApiEndpoint(
            method=method, path=path or "/", summary=location,
            parameters=parameters, record_selector="", primary_key_candidates=[], cursor_candidates=[],
            pagination_hint="", confidence="confirmed",
            evidence=[Evidence(
                source_id=source_id, location=location,
                detail="Request parsed deterministically from Postman collection",
            )],
        ))
    return ApiKnowledge(
        title=str((document.get("info") or {}).get("name") or "Postman API"),
        summary=str((document.get("info") or {}).get("description") or ""),
        base_urls=base_urls, auth_methods=auth_methods, endpoints=endpoints, unknowns=[],
        evidence=[Evidence(source_id=source_id, location="root", detail="Parsed as Postman collection")],
    )


def parse_json_schema_bundle(
    source_id: str, name: str, document: dict[str, Any],
) -> ApiKnowledge | None:
    """Keep response-only JSON Schemas factual instead of asking a model for endpoints."""
    if document.get("$schema") and isinstance(document.get("properties"), dict):
        schemas = {"response": document}
    else:
        schemas = {
            str(key): value for key, value in document.items()
            if isinstance(value, dict) and isinstance(value.get("properties"), dict)
        }
        if not schemas or len(schemas) != len(document):
            return None

    concepts: list[str] = []
    evidence: list[Evidence] = []
    for schema_name, schema in schemas.items():
        fields = list((schema.get("properties") or {}).keys())[:100]
        primary, cursors = _schema_candidates(schema)
        detail = f"Observed response schema '{schema_name}' fields: {', '.join(fields)}"
        if primary:
            detail += f". Primary key candidates: {', '.join(primary)}"
        if cursors:
            detail += f". Cursor candidates: {', '.join(cursors)}"
        concepts.append(detail)
        evidence.append(Evidence(
            source_id=source_id, location=schema_name,
            detail="Parsed deterministically as a response JSON Schema",
        ))
    return ApiKnowledge(
        title=f"Response schemas: {Path(name).name}",
        summary="Observed response field contracts without endpoint or authentication metadata.",
        concepts=concepts,
        unknowns=["Endpoint mapping, record selectors and authentication are not present in this schema."],
        evidence=evidence,
    )


def deterministic_knowledge(source_id: str, name: str, text: str | None) -> ApiKnowledge | None:
    if not text:
        return None
    document = _load_document(name, text)
    if not isinstance(document, dict):
        return None
    return (
        parse_openapi(source_id, document)
        or parse_postman(source_id, document)
        or parse_json_schema_bundle(source_id, name, document)
    )
