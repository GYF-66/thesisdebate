"""Read models and persisted UI selections for the Chinese research workbench."""
from __future__ import annotations

from io import BytesIO
from functools import lru_cache
from threading import Lock
import re
from urllib.parse import quote
from zipfile import ZipFile, ZIP_DEFLATED
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from app.agent_api import get_store, main

router = APIRouter()


class RuleSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: list[str] = Field(max_length=100)


def profile_or_404(profile_id):
    profile = main().resolve_format_profile(profile_id)
    if profile is None:
        raise HTTPException(404, '学校规范不存在。')
    return profile


@router.get('/format-profiles/{profile_id}/rule-selection')
def read_rule_selection(profile_id: str):
    profile = profile_or_404(profile_id)
    saved = get_store().get_preference('rules:' + profile_id)
    allowed = {rule.id for rule in profile.rules}
    return {'enabled': [rule for rule in saved['enabled'] if rule in allowed] if saved else [rule.id for rule in profile.rules if rule.enabled_by_default]}


@router.put('/format-profiles/{profile_id}/rule-selection')
def save_rule_selection(profile_id: str, payload: RuleSelection):
    allowed = {rule.id for rule in profile_or_404(profile_id).rules}
    if not set(payload.enabled).issubset(allowed):
        raise HTTPException(422, '所选规则不属于当前规范。')
    result = {'enabled': sorted(set(payload.enabled))}
    get_store().save_preference('rules:' + profile_id, result)
    return result


def rules_match(run):
    profile = profile_or_404(run.profile_id)
    applied = run.requested_rules if run.rules_explicit or run.requested_rules else [r.id for r in profile.rules if r.enabled_by_default]
    return set(applied) == set(read_rule_selection(run.profile_id)['enabled'])


@router.get('/format-runs/{run_id}/rule-selection-status')
def rule_selection_status(run_id: str):
    with main().REVISION_LOCK:
        run = main().RUNS.get(run_id)
        if run is None:
            raise HTTPException(404, '扫描记录不存在。')
        return {'matches': rules_match(run)}


@router.get('/workspace/statistics')
def workspace_statistics():
    store, module = get_store(), main()
    documents = store.workspace()['documents']
    counts = {'total': len(documents), 'scanned': 0, 'pending': 0, 'accepted': 0, 'rejected': 0}
    types, rows = {}, []
    with module.REVISION_LOCK:
        for document in documents:
            suffix = document['name'].rsplit('.', 1)[-1].lower()
            kind = {'docx': 'Word 文档', 'pdf': 'PDF 文档', 'md': 'Markdown', 'markdown': 'Markdown', 'txt': '文本文件'}.get(suffix, '论文文稿')
            types[kind] = types.get(kind, 0) + 1
            run = module.RUNS.get(document['last_run_id'])
            changes = [item for item in module.CHANGES.values() if run and item.run_id == run.id]
            fresh = False
            if run and run.document_id == document['id']:
                preview, _ = module.build_preview(module.SOURCES[run.id], changes, run.document_version_id)
                fresh = store.get_workspace_document(document['id'])['content'] == preview and rules_match(run)
            stats = {key: sum(item.status.value == status for item in changes) if fresh else 0 for key, status in [('pending', 'pending'), ('accepted', 'accepted'), ('rejected', 'rejected')]}
            if fresh:
                counts['scanned'] += 1
                for key, value in stats.items():
                    counts[key] += value
            rows.append({**document, **stats, 'source_type': kind, 'scan_status': 'scanned' if fresh else 'stale' if run else 'unscanned'})
    return {**counts, 'types': [{'label': key, 'value': value} for key, value in types.items()], 'documents': rows, 'exports': len(store.list_exports())}


@router.get('/exports')
def exports():
    return get_store().list_exports()


def attachment(data: bytes, media: str, filename: str):
    return Response(data, media_type=media, headers={'Content-Disposition': "attachment; filename=download; filename*=UTF-8''" + quote(filename, safe=''), 'Cache-Control': 'no-store'})


@router.get('/exports/{identity}/download')
def download_export(identity: str):
    value = get_store().get_export(identity)
    if value is None:
        raise HTTPException(404, '导出记录不存在。')
    return attachment(value['data'], value['media_type'], value['filename'])


class ExportSelection(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)


@router.post('/exports/download')
def download_exports(payload: ExportSelection):
    values = [get_store().get_export(identity) for identity in dict.fromkeys(payload.ids)]
    if any(value is None for value in values):
        raise HTTPException(404, '部分导出记录已不存在，请刷新列表。')
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for index, value in enumerate(values, 1):
            name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', value['filename'])
            archive.writestr(f'{index:02d}-{name}', value['data'])
    return attachment(output.getvalue(), 'application/zip', '研辩台-导出文件.zip')


def profile_pdf(profile_id: str):
    from app.document_export import export_pdf
    from app.document_layout import DocumentStyle
    profile = profile_or_404(profile_id)
    text = '# ' + profile.name + '\n\n' + profile.institution + '\n\n' + profile.description + '\n\n## 规范条目\n\n' + '\n\n'.join(f'### {rule.property}\n\n{rule.description}' for rule in profile.rules)
    return export_pdf(text, profile.document_style or DocumentStyle()), profile.name


@router.get('/format-profiles/{profile_id}/document')
def profile_document(profile_id: str):
    data, name = profile_pdf(profile_id)
    return attachment(data, 'application/pdf', name + '.pdf')


_render_lock = Lock()  # PDFium is not thread-safe; serialize native rendering.


@lru_cache(maxsize=24)
def render_thumbnail(data: bytes):
    import pypdfium2 as pdfium
    with _render_lock, pdfium.PdfDocument(data) as document:
        page = document[0]
        try:
            bitmap = page.render(scale=0.8)
            try:
                image = bitmap.to_pil()
                try:
                    output = BytesIO()
                    image.save(output, format='PNG')
                    return output.getvalue()
                finally:
                    image.close()
            finally:
                bitmap.close()
        finally:
            page.close()


@router.get('/format-profiles/{profile_id}/thumbnail')
def profile_thumbnail(profile_id: str):
    data, _ = profile_pdf(profile_id)
    return Response(render_thumbnail(data), media_type='image/png', headers={'Cache-Control': 'no-cache'})
