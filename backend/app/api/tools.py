"""Tools catalog HTTP surface (v0.11.0).

Endpoints::

    POST   /tools                    -> upload + static-validate a new tool
    GET    /tools                    -> list catalog (filter by kind/lane/status)
    GET    /tools/{id}               -> read one row
    POST   /tools/{id}/approve       -> admin: promote draft -> approved
    POST   /tools/{id}/revoke        -> admin: mark revoked (past runs preserved)
    DELETE /tools/{id}               -> admin: hard delete (only draft/revoked)

v0.11.0 rules for who does what:

- Upload is admin-only for now (via ``/settings/tools``). v0.12 opens
  an analyst-upload path once the invocation runtime lands, so the
  analyst has somewhere useful to send the tool afterwards.
- List/read is any authenticated user — the catalog is not sensitive
  in itself (source is stored server-side; the API doesn't leak it).
- Approve/revoke/delete are admin-only.

Validation flow inside POST /tools:

1. Parse the YAML manifest (``tool_manifest.parse_manifest``). Bad
   YAML or schema failure returns 400.
2. If ``spec.kind == python`` and a source file was uploaded, run the
   AST allow-list (``tool_ast_check.check_python_source``). Any
   disallowed import or banned attribute is captured in
   ``validation.ast`` but does *not* prevent the row from being
   created — the admin sees the failure verbatim and decides.
3. Persist a row in status=``draft``. Admin uses POST
   /tools/{id}/approve to promote (v0.13 adds LLM review as a second
   gate; v0.14 admits the binary lane).

Every mutation writes an ``audit_log`` row (``tool.uploaded``,
``tool.approved``, ``tool.revoked``, ``tool.deleted``).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select

from app.api.deps import (
    CurrentAdminUser,
    CurrentNonGuestUser,
    CurrentUser,
    DbSession,
    RedisClient,
)
from app.models import (
    ActorType,
    AuditLog,
    Tool,
    ToolKind,
    ToolLane,
    ToolStatus,
)
from app.schemas.tool import (
    ToolApproveRequest,
    ToolRead,
    ToolUploadResponse,
)
from app.services.tool_ast_check import check_python_source, infer_python_deps
from app.services.tool_image_ref import ImageRefError, parse_image_ref
from app.services.tool_llm_review import review_tool_source
from app.services.tool_manifest import (
    ManifestParseError,
    manifest_to_jsonb,
    parse_manifest,
)
from app.services.tool_manifest_infer import (
    infer_from_python_source,
    inferred_to_manifest_yaml,
)
from app.services.tool_shell_check import check_shell_source

router = APIRouter()

_MAX_SOURCE_BYTES = 200_000  # 200 kB is plenty for a first-party tool


def _tool_to_read(row: Tool) -> ToolRead:
    return ToolRead(
        id=row.id,
        name=row.name,
        description=row.description,
        kind=row.kind,
        lane=row.lane,
        risk_level=row.risk_level,
        task_kind=row.task_kind,
        status=row.status,
        manifest=dict(row.manifest or {}),
        validation=dict(row.validation or {}),
        has_artifact=row.artifact_ref is not None,
        version=row.version,
        created_by_user_id=row.created_by_user_id,
        approved_by_user_id=row.approved_by_user_id,
        approved_at=row.approved_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _audit(
    session: DbSession,
    user_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    session.add(
        AuditLog(
            engagement_id=None,
            actor_type=ActorType.user,
            actor_id=str(user_id),
            event_type=event_type,
            payload=payload,
        )
    )


@router.post(
    "/tools",
    response_model=ToolUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_tool(
    session: DbSession,
    redis_client: RedisClient,
    user: CurrentNonGuestUser,
    manifest: Annotated[str, Form(description="YAML manifest text")],
    source: Annotated[UploadFile | None, File()] = None,
) -> ToolUploadResponse:
    """Register a new tool.

    Multipart form: ``manifest`` is the required YAML text; ``source``
    is optional (required for Python/shell kinds, not used for binary).

    Non-guest analysts can upload (v0.12+); admin still approves via
    ``POST /tools/{id}/approve`` before an engagement can invoke.
    """
    try:
        parsed = parse_manifest(manifest)
    except ManifestParseError as exc:
        raise HTTPException(status_code=400, detail={"manifest_errors": exc.errors}) from exc

    validation: dict[str, Any] = {"manifest_ok": True}
    validation_errors: list[str] = []

    # Source required for the non-binary kinds; forbidden for binary.
    source_bytes: bytes | None = None
    if source is not None:
        source_bytes = await source.read()
        if len(source_bytes) > _MAX_SOURCE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"source file exceeds {_MAX_SOURCE_BYTES} bytes",
            )
    if parsed.spec.kind in (ToolKind.python, ToolKind.shell) and source_bytes is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"kind={parsed.spec.kind.value} requires a source file upload"
            ),
        )

    # Layer 1a: AST allow-list for Python.
    source_text: str | None = None
    if source_bytes is not None:
        try:
            source_text = source_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=400, detail=f"source is not UTF-8: {exc}"
            ) from exc

    if parsed.spec.kind == ToolKind.python and source_text is not None:
        ast_result = check_python_source(source_text)
        validation["ast"] = ast_result.to_json()
        if not ast_result.ok:
            if ast_result.syntax_error:
                validation_errors.append(
                    f"AST: python syntax error — {ast_result.syntax_error}"
                )
            for imp in ast_result.disallowed_imports:
                validation_errors.append(f"AST: disallowed import '{imp}'")
            for call in ast_result.banned_calls:
                validation_errors.append(f"AST: banned call '{call}'")
        # v0.15.1 fix: auto-populate python_deps from third-party imports
        # the analyst forgot to declare. Sandbox base is python:3.12-slim
        # with nothing pre-installed — without this, `import httpx`
        # succeeds through the AST allow-list but blows up at runtime.
        # Silent merge: whatever the analyst declared plus whatever the
        # AST saw. Recorded on the row so admin can see what happened.
        inferred_deps = infer_python_deps(ast_result.imports_seen)
        if inferred_deps:
            declared = list(parsed.spec.python_deps or [])
            declared_lower = {d.lower() for d in declared}
            added = [
                d for d in inferred_deps if d.lower() not in declared_lower
            ]
            if added:
                parsed.spec.python_deps = sorted(set(declared + added))
                validation["python_deps_auto_added"] = added

    # Layer 1b: shell heuristic scanner (v0.13.0). Same shape as the AST
    # check so the admin approve UI renders it identically.
    if parsed.spec.kind == ToolKind.shell and source_text is not None:
        shell_result = check_shell_source(source_text)
        validation["shell"] = shell_result.to_json()
        if not shell_result.ok:
            for m in shell_result.matches:
                validation_errors.append(
                    f"shell: {m.pattern} at line {m.line} — {m.hint}"
                )

    # Layer 3: LLM safety review (v0.13.0). Runs for the analyst lane
    # on Python + shell kinds; binary skips (LLM can't audit a compiled
    # artifact). Uploader's BYO Redis-cached key satisfies the call;
    # missing key falls through with a "skipped" verdict on the row so
    # the admin sees the gap explicitly.
    if (
        parsed.spec.lane == ToolLane.analyst
        and parsed.spec.kind in (ToolKind.python, ToolKind.shell)
        and source_text is not None
    ):
        review = review_tool_source(
            session,
            redis_client,
            source=source_text,
            kind=parsed.spec.kind.value,
            manifest=manifest_to_jsonb(parsed),
            tool_name=parsed.metadata.name,
            acting_user_id=user.id,
        )
        validation["llm_review"] = review.to_json()
        if review.skipped is None and not review.safe:
            validation_errors.append(
                f"LLM review: {review.reason}"
            )
        if review.skipped is None and not review.matches_stated_intent:
            validation_errors.append(
                "LLM review: code does not match stated intent "
                "(manifest task_kind / risk_level / egress vs. actual behaviour)"
            )

    # Binary lane requires an admin-declared artifact_ref (OCI image
    # tag). v0.14.0 makes this a first-class flow: parse + validate the
    # OCI reference and refuse a source file (admin is confused if they
    # attach one).
    if parsed.spec.kind == ToolKind.binary:
        if parsed.spec.lane != ToolLane.admin:
            raise HTTPException(
                status_code=400,
                detail="binary kind requires lane=admin",
            )
        if source_bytes is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "binary kind takes an OCI image tag in spec.entrypoint, "
                    "not a source file — omit the source upload"
                ),
            )
        try:
            image_ref = parse_image_ref(parsed.spec.entrypoint)
        except ImageRefError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"invalid OCI image reference: {exc}",
            ) from exc
        validation["image_ref"] = image_ref.to_json()
        if not image_ref.is_pinned:
            # Non-blocking warning — allow tag-based refs (most admins
            # will use them), but surface the reproducibility risk on
            # the row so the admin sees it at approve time.
            validation.setdefault("warnings", []).append(
                "image reference uses a tag, not a digest — the image "
                "content can change out from under you. Pin with "
                "@sha256:… when reproducibility matters."
            )

    # v0.11.0 artifact storage is a placeholder: the source bytes land in
    # the DB row as a Postgres text blob under artifact_ref='inline:...'.
    # v0.12 swaps this for real blob storage once we know the invocation
    # runtime's I/O shape. Keeping the shape stable via the same column.
    artifact_ref: str | None = None
    if parsed.spec.kind == ToolKind.binary:
        artifact_ref = parsed.spec.entrypoint  # OCI image tag
    elif source_bytes is not None:
        # For v0.11 we stash the source into `validation.source` as
        # base64 so admin approve can inspect it. It moves out to blob
        # storage in v0.12. This is a placeholder path — real Python
        # tools >>200kB are rare in the recon space, so the tradeoff is
        # fine short-term.
        import base64 as _b64

        validation["source_b64"] = _b64.b64encode(source_bytes).decode("ascii")
        artifact_ref = f"inline:{parsed.metadata.name}"

    # Duplicate-name check — enforced by uq_tools_name_version. For v0.11
    # we always start at version=1 and reject re-uploads with the same
    # name; version bumping is a v0.12 nicety.
    existing = session.execute(
        select(Tool).where(Tool.name == parsed.metadata.name)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"tool named '{parsed.metadata.name}' already exists "
                f"(id={existing.id}); revoke it before re-uploading"
            ),
        )

    row = Tool(
        name=parsed.metadata.name,
        description=parsed.metadata.description,
        kind=parsed.spec.kind,
        lane=parsed.spec.lane,
        risk_level=parsed.spec.risk_level,
        task_kind=parsed.spec.task_kind,
        status=ToolStatus.draft,
        manifest=manifest_to_jsonb(parsed),
        validation=validation,
        artifact_ref=artifact_ref,
        version=1,
        created_by_user_id=user.id,
    )
    session.add(row)
    _audit(
        session,
        user.id,
        "tool.uploaded",
        {
            "tool_name": row.name,
            "kind": row.kind.value,
            "lane": row.lane.value,
            "validation_ok": not validation_errors,
        },
    )
    session.commit()
    session.refresh(row)
    return ToolUploadResponse(
        tool=_tool_to_read(row),
        validation_ok=not validation_errors,
        validation_errors=validation_errors,
    )


@router.post("/tools/infer")
async def infer_manifest_from_source(
    _user: CurrentNonGuestUser,
    source: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    """Read a Python source and return the manifest fields the backend
    could infer, plus a list of ``missing`` required fields the upload
    wizard should ask the analyst to fill.

    This is the "auto-detect" upload path — the frontend calls this on
    file pick, shows a preview, and either lets the analyst confirm and
    submit (going through the normal POST /tools with the inferred YAML)
    or falls back to the guided form when required fields are missing.
    """
    try:
        raw = await source.read()
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400, detail=f"python source is not UTF-8: {exc}"
        ) from exc
    if len(raw) > _MAX_SOURCE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"source file exceeds {_MAX_SOURCE_BYTES} bytes",
        )
    inferred = infer_from_python_source(text, filename=source.filename or "main.py")
    return {
        "name": inferred.name,
        "description": inferred.description,
        "entrypoint": inferred.entrypoint,
        "kind": inferred.kind,
        "lane": inferred.lane,
        "fields": inferred.fields,
        "missing": inferred.missing,
        "warnings": inferred.warnings,
        "manifest_yaml": inferred_to_manifest_yaml(inferred),
    }


@router.get("/tools", response_model=list[ToolRead])
def list_tools(
    session: DbSession,
    _user: CurrentUser,
    kind: Annotated[ToolKind | None, Query()] = None,
    lane: Annotated[ToolLane | None, Query()] = None,
    tool_status: Annotated[ToolStatus | None, Query(alias="status")] = None,
) -> list[ToolRead]:
    """Catalog list — any authenticated user."""
    stmt = select(Tool).order_by(Tool.created_at.desc())
    if kind is not None:
        stmt = stmt.where(Tool.kind == kind)
    if lane is not None:
        stmt = stmt.where(Tool.lane == lane)
    if tool_status is not None:
        stmt = stmt.where(Tool.status == tool_status)
    rows = session.execute(stmt).scalars()
    return [_tool_to_read(r) for r in rows]


@router.get("/tools/{tool_id}", response_model=ToolRead)
def get_tool(
    tool_id: uuid.UUID,
    session: DbSession,
    _user: CurrentUser,
) -> ToolRead:
    row = session.get(Tool, tool_id)
    if row is None:
        raise HTTPException(status_code=404, detail="tool not found")
    return _tool_to_read(row)


@router.post("/tools/{tool_id}/approve", response_model=ToolRead)
def approve_tool(
    tool_id: uuid.UUID,
    body: ToolApproveRequest,
    session: DbSession,
    user: CurrentAdminUser,
) -> ToolRead:
    """Admin promotes a draft tool to approved. Requires ``validation_ok``
    unless ``override_validation`` is set — the override is audit-logged
    so a later reviewer can see the escape hatch was used."""
    row = session.get(Tool, tool_id)
    if row is None:
        raise HTTPException(status_code=404, detail="tool not found")
    if row.status == ToolStatus.approved:
        return _tool_to_read(row)
    if row.status == ToolStatus.revoked:
        raise HTTPException(
            status_code=409,
            detail="tool is revoked; re-upload as a new row to re-approve",
        )

    ast_result = row.validation.get("ast", {}) or {}
    shell_result = row.validation.get("shell", {}) or {}
    llm_result = row.validation.get("llm_review", {}) or {}
    validation_ok = (
        not ast_result.get("disallowed_imports")
        and not ast_result.get("banned_calls")
        and not shell_result.get("matches")
        # LLM verdict counts only when it actually ran; a skipped review
        # (no BYO key at upload time) doesn't block approval on its own.
        and (
            llm_result.get("skipped")
            or (
                llm_result.get("safe", True)
                and llm_result.get("matches_stated_intent", True)
            )
        )
    )
    if not validation_ok and not body.override_validation:
        raise HTTPException(
            status_code=409,
            detail=(
                "validation flagged the tool; set override_validation=true "
                "to approve anyway (recorded in audit_log)"
            ),
        )

    row.status = ToolStatus.approved
    row.approved_by_user_id = user.id
    row.approved_at = datetime.now(tz=UTC)
    _audit(
        session,
        user.id,
        "tool.approved",
        {
            "tool_id": str(row.id),
            "tool_name": row.name,
            "override_validation": body.override_validation,
            "note": body.note,
        },
    )
    session.commit()
    session.refresh(row)
    return _tool_to_read(row)


@router.post("/tools/{tool_id}/revoke", response_model=ToolRead)
def revoke_tool(
    tool_id: uuid.UUID,
    session: DbSession,
    user: CurrentAdminUser,
) -> ToolRead:
    row = session.get(Tool, tool_id)
    if row is None:
        raise HTTPException(status_code=404, detail="tool not found")
    if row.status == ToolStatus.revoked:
        return _tool_to_read(row)
    row.status = ToolStatus.revoked
    _audit(
        session,
        user.id,
        "tool.revoked",
        {"tool_id": str(row.id), "tool_name": row.name},
    )
    session.commit()
    session.refresh(row)
    return _tool_to_read(row)


@router.delete(
    "/tools/{tool_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_tool(
    tool_id: uuid.UUID,
    session: DbSession,
    user: CurrentAdminUser,
) -> Response:
    """Hard-delete a tool. Only allowed while the row is draft or
    revoked — approved tools stay in the DB so past invocations keep
    their FK. Practically this is only useful for cleaning up bad
    uploads before anyone approves them."""
    row = session.get(Tool, tool_id)
    if row is None:
        raise HTTPException(status_code=404, detail="tool not found")
    if row.status == ToolStatus.approved:
        raise HTTPException(
            status_code=409,
            detail="cannot delete an approved tool; revoke it first",
        )
    tool_name = row.name
    session.delete(row)
    _audit(
        session,
        user.id,
        "tool.deleted",
        {"tool_id": str(tool_id), "tool_name": tool_name},
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
