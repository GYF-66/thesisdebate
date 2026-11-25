"""Local template analysis: observed formatting and quoted requirements, never prompts."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import PurePosixPath
import re

from app.document_layout import DocumentStyle, HeadingAppearance
from app.document_fonts import validate_font_name


def analyze_docx_style(reader, root, source_name="DOCX"):
    from app.document_import import _attr, _child, _local, _resolve_part
    from app.template_rules import rule
    warnings, sources = [], []
    styles, defaults = {}, {}
    main = reader.main_part()
    path = PurePosixPath(main)
    relations = reader.relationships(str(path.parent / "_rels" / (path.name + ".rels")))
    styles_part = next((_resolve_part(main, rel.get("Target", "")) for rel in relations
                        if rel.get("Type", "").endswith("/styles") and rel.get("TargetMode", "").lower() != "external"), None)

    def numeric(value, scale=1):
        try:
            return round(float(value) / scale, 3)
        except (ValueError, TypeError):
            return None

    def props(node):
        if node is None:
            return {}
        result = {}
        run, paragraph = _child(node, "rPr"), _child(node, "pPr")
        fonts = _child(run, "rFonts") if run is not None else None
        if fonts is not None:
            value = _attr(fonts, "eastAsia") or _attr(fonts, "ascii") or _attr(fonts, "hAnsi")
            if value:
                try:
                    result["font_family"] = validate_font_name(value)
                except ValueError:
                    warnings.append("字体名称未通过校验，请核对原文。")
        size = _child(run, "sz") if run is not None else None
        if size is not None:
            result["font_size_pt"] = numeric(_attr(size, "val"), 2)
        spacing = _child(paragraph, "spacing") if paragraph is not None else None
        if spacing is not None:
            for attr, field in (("before", "space_before_pt"), ("after", "space_after_pt")):
                if _attr(spacing, attr):
                    result[field] = numeric(_attr(spacing, attr), 20)
            if _attr(spacing, "line"):
                kind = _attr(spacing, "lineRule")
                if kind in {"", "auto"}:
                    result.update(line_spacing=numeric(_attr(spacing, "line"), 240), line_spacing_pt=0.0)
                elif kind == "exact":
                    result["line_spacing_pt"] = numeric(_attr(spacing, "line"), 20)
                else:
                    warnings.append("发现最小值行距：仅固定磅值和倍数行距可自动填充，请核对该项。")
        indent = _child(paragraph, "ind") if paragraph is not None else None
        if indent is not None:
            if _attr(indent, "firstLineChars"):
                result["indent_chars"] = numeric(_attr(indent, "firstLineChars"), 100)
            elif _attr(indent, "firstLine"):
                result["first_line_indent_pt"] = numeric(_attr(indent, "firstLine"), 20)
        return {key: value for key, value in result.items() if value is not None}

    if styles_part:
        tree = reader.read_xml(styles_part)
        default_node = _child(tree, "docDefaults")
        if default_node is not None:
            for child in default_node:
                defaults.update(props(child))
        styles = {_attr(node, "styleId"): node for node in tree if _local(node.tag) == "style"}

    def effective(identity, seen=()):
        if identity in seen or len(seen) > 30 or identity not in styles:
            return dict(defaults)
        node = styles[identity]
        parent = _child(node, "basedOn")
        values = effective(_attr(parent, "val"), (*seen, identity)) if parent is not None else dict(defaults)
        values.update(props(node))
        return values

    normal = next((key for key, node in styles.items() if _attr(node, "type") == "paragraph" and _attr(node, "default") == "1"), "Normal")
    votes, quotes = defaultdict(Counter), {}
    body_count, heading_counts = 0, Counter()
    body = _child(root, "body")
    # Tables, cover labels, captions, reference entries and code are not body samples.
    paragraphs = list(body) if body is not None else []
    for paragraph in paragraphs:
        if _local(paragraph.tag) != "p":
            continue
        text = "".join(node.text or "" for node in paragraph.iter() if _local(node.tag) == "t").strip()
        if not text:
            continue
        if re.fullmatch(r"摘要|Abstract|参考文献|致谢|目录", re.sub(r"\s+", "", text), re.I):
            continue
        ppr = _child(paragraph, "pPr")
        pstyle = _child(ppr, "pStyle") if ppr is not None else None
        identity = _attr(pstyle, "val") if pstyle is not None else normal
        outline = _child(ppr, "outlineLvl") if ppr is not None else None
        level = reader.heading_styles.get(identity)
        if outline is not None and _attr(outline, "val") in {str(i) for i in range(6)}:
            level = int(_attr(outline, "val")) + 1
        if level and not 1 <= level <= 6:
            continue
        if not level and (len(text) < 35 or re.match(r"(?:图|表)\s*\d|\[\d+\]|<|function\b|var\b", text)
                          or re.search(r"caption|toc|bibliography|参考文献|目录", identity, re.I)):
            continue
        prefix = f"document_style.headings.{level - 1}." if level else "document_style."
        values = effective(identity)
        values.update(props(paragraph))
        run_votes = defaultdict(Counter)
        has_han = bool(re.search(r"[\u4e00-\u9fff]", text))
        for run in paragraph.iter():
            if _local(run.tag) != "r":
                continue
            content = "".join(node.text or "" for node in run.iter() if _local(node.tag) == "t")
            weight = len(re.findall(r"[\u4e00-\u9fff]", content)) if has_han else len(content.strip())
            if not weight:
                continue
            rpr = _child(run, "rPr")
            rstyle = _child(rpr, "rStyle") if rpr is not None else None
            formatting = dict(values)
            if rstyle is not None:
                # Character styles override only their own explicit/inherited properties.
                formatting.update({k: v for k, v in effective(_attr(rstyle, "val")).items() if k not in defaults or defaults[k] != v})
            formatting.update(props(run))
            for field in ("font_family", "font_size_pt"):
                if field in formatting:
                    run_votes[field][formatting[field]] += weight
        for field, choices in run_votes.items():
            winner, weight = choices.most_common(1)[0]
            if weight / choices.total() >= .65:
                values[field] = winner
        if "indent_chars" in values and "font_size_pt" in values:
            values["first_line_indent_pt"] = values.pop("indent_chars") * values["font_size_pt"]
        for key, value in values.items():
            field = key if level else {"space_before_pt": "paragraph_before_pt", "space_after_pt": "paragraph_after_pt"}.get(key, key)
            if field not in (HeadingAppearance.model_fields if level else DocumentStyle.model_fields):
                continue
            field = prefix + field
            votes[field][value] += min(len(text), 300)
            quotes.setdefault((field, value), text[:150])
        if level:
            heading_counts[level] += 1
        else:
            body_count += 1

    # Style definitions are evidence only when no actual paragraph sample exists.
    for level in range(7):
        if (level == 0 and body_count) or heading_counts[level]:
            continue
        identities = [normal] if not level else [key for key, value in reader.heading_styles.items() if value == level]
        for identity in identities:
            for key, value in effective(identity).items():
                field = key if level else {"space_before_pt": "paragraph_before_pt", "space_after_pt": "paragraph_after_pt"}.get(key, key)
                if field in (HeadingAppearance.model_fields if level else DocumentStyle.model_fields):
                    field = (f"document_style.headings.{level - 1}." if level else "document_style.") + field
                    votes[field][value] += 1
                    quotes[field, value] = f"Word 样式定义：{identity}（尚无对应正文样本）"
    for section in root.iter():
        if _local(section.tag) != "sectPr":
            continue
        margin = _child(section, "pgMar")
        if margin is None:
            continue
        for edge in ("top", "bottom", "left", "right"):
            value = numeric(_attr(margin, edge), 1440 / 25.4)
            if value is not None:
                # Word's integer twips can differ by 0.018 mm after round trips.
                value = round(value, 1)
                field = "document_style.margin_" + edge + "_mm"
                votes[field][value] += 1
                quotes[field, value] = "Word 分节页面设置（毫米，按 0.1 mm 合并转换舍入误差）"

    from app.school_templates import SchoolTemplateDraft
    from app.template_rules import _set
    draft = SchoolTemplateDraft(name="样式分析", heading_numbering=["preserve"] * 6)
    for field, choices in votes.items():
        winner, weight = choices.most_common(1)[0]
        ambiguous = len(choices) > 1 and (weight / choices.total() < .65 or "margin_" in field)
        candidates = choices.most_common(4) if ambiguous else [(winner, weight)]
        for value, count in candidates:
            try:
                trial = _set(draft, field, value)
            except ValueError:
                warnings.append(f"{field} 的值 {value} 超出可调范围，留待核对。")
                continue
            if not ambiguous:
                draft = trial
            sources.append(rule(source_name, quotes[field, value], field, value, "conflict" if ambiguous else "inferred",
                                f"样例排版统计，支持比例 {count / choices.total():.0%}。"))
    warnings.insert(0, f"已识别 {len(sources)} 条排版依据，来自 {body_count} 个正文段落和 {sum(heading_counts.values())} 个标题；封面、表格和短标签未计入正文。")
    warnings.append("没有识别依据的项目保留可编辑默认值；样例排版不等于学校的明文规定。")
    return draft.document_style, list(dict.fromkeys(warnings))[:20], sources


def identity_sources(text, name):
    """Only cover/header-sized lines; never institutions mentioned in references."""
    from app.template_rules import rule
    candidates = []
    for index, raw in enumerate(text.splitlines()[:80]):
        line = re.sub(r"^\[第\d+页\]\s*|^#+\s*", "", raw).strip()
        compact = re.sub(r"\s+", "", line)
        if re.match(r"^(摘要|ABSTRACT|目录|参考文献|致谢|1[.、\s])", compact, re.I):
            break
        if len(compact) > 70 or re.search(r"[。；;\[\]《》]|例如|示例|如：|\*|某大学|某学院", compact):
            continue
        label = re.match(r"^(?:学校|院校|学院|院系|所属学院)(?:名称)?[:：]?(.+(?:大学|学院|学校))(?:$|[（(])", compact)
        title = re.match(r"^([\u4e00-\u9fff]{2,35}(?:大学|学院|学校))(?:$|本科|毕业|研究生)", compact)
        match = label or title
        if match:
            value = match[1]
            candidates.append((value, line, index, bool(label)))
    if not candidates:
        return []
    universities = [item for item in candidates if "大学" in item[0]]
    selected = universities or candidates
    values = list(dict.fromkeys(item[0] for item in selected))
    return [rule(name, next(item[1] for item in selected if item[0] == value), "institution", value,
                 "explicit" if len(values) == 1 else "conflict", "来自封面或页首学校/学院名称。") for value in values[:4]]


def fill_recognized(draft, additional=()):
    """Fill unique evidence; explicit requirements outrank observed sample styling."""
    from app.template_rules import _set
    draft = draft.model_copy(deep=True)
    sources = list(draft.rule_sources) + list(additional)
    sources = list({item.id: item for item in sources}.values())[:200]
    grouped = defaultdict(list)
    for item in sources:
        if not item.note.startswith("未采用"):
            grouped[item.field].append(item)
    for field, items in grouped.items():
        explicit = [item for item in items if item.status == "explicit"]
        # Conflicting textual requirements retain their conflict status.
        if explicit and not any(item.status == "conflict" and "样例排版统计" not in item.note for item in items):
            for item in items:
                if item not in explicit:
                    item.note = "未采用：已有明文要求，保留样例依据供对照。"
            items = explicit
        values = {json.dumps(item.value, ensure_ascii=False, sort_keys=True) for item in items}
        if len(values) != 1:
            for item in items:
                item.status = "conflict"
            continue
        draft = _set(draft, field, items[0].value)
        for item in items:
            if not item.note.startswith("已自动填入"):
                item.note = "已自动填入；" + item.note
    draft.rule_sources = sources
    draft.warnings = [warning for warning in draft.warnings if "只有选择并确认" not in warning]
    return draft


def pdf_sample_sources(runs, name):
    """Use repeated long text runs; PDF page geometry is not a Word margin rule."""
    from app.document_fonts import ALL_ALIASES
    from app.template_rules import rule
    votes, examples = defaultdict(Counter), {}
    samples = [run for run in runs if len(run[0].strip()) >= 35]
    if len(samples) < 3 or sum(len(run[0]) for run in samples) < 200:
        return []
    aliases = {alias.replace(' ', '').casefold(): family for family, names in ALL_ALIASES.items() for alias in (family, *names)}
    for text, basefont, size, page in samples:
        family = re.sub(r'^[A-Z]{6}\+', '', basefont.lstrip('/'))
        family = aliases.get(family.replace(' ', '').casefold(), family)
        try:
            validate_font_name(family)
        except ValueError:
            continue
        for field, value in [('font_family', family), ('font_size_pt', round(size, 2))]:
            if field == 'font_size_pt' and not 8 <= value <= 36:
                continue
            votes[field][value] += min(len(text), 300)
            examples[field, value] = (text[:150], page)
    result = []
    for field, choices in votes.items():
        value, count = choices.most_common(1)[0]
        if count / choices.total() < .65:
            continue
        text, page = examples[field, value]
        result.append(rule(f'{name} · 第{page}页', text, 'document_style.' + field, value, 'inferred',
                           '来自 PDF 正文文字层的实际字体/字号统计；不是学校明文要求。'))
    return result
