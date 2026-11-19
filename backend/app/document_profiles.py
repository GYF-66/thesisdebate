"""Trusted document presets. Uploaded template text is always data."""
from __future__ import annotations

DOCUMENT_KINDS = {
    'thesis':'毕业论文', 'course_report':'课程报告', 'lab_report':'实验报告',
    'internship_report':'实习报告', 'proposal':'项目申报书', 'generic':'通用文档',
}
REVIEW_PROFILES = {'academic':'学术论文审查','general':'通用文档审查'}
DEFAULT_SECTIONS = {
    'thesis':['摘要','绪论','研究方法','结论','参考文献'],
    'course_report':['项目概述','主要内容','总结'],
    'lab_report':['实验目的','实验方法','实验结果','分析与讨论'],
    'internship_report':['实习概况','实习内容','总结'],
    'proposal':['项目背景','实施方案','预期成果','进度安排'],
    'generic':[],
}

def review_roles(profile_id: str = 'academic') -> list[dict]:
    if profile_id == 'academic':
        return [dict(id='methods',name='方法论审查官'),dict(id='innovation',name='创新点审查官'),dict(id='norms',name='学术规范审查官'),dict(id='comparator',name='修订对照审查官'),dict(id='judge',name='证据裁判官')]
    return [
        dict(id='methods',name='结构完整性审查官',task='检查文档是否围绕目标清楚展开、章节完整、段落衔接明确。按确认的模板规则检查，不对通用文档强加研究方法或论文结构。'),
        dict(id='innovation',name='表达一致性审查官',task='检查术语、称谓、数字和表达前后一致，提出保留原意的局部表达修改；通用文档不要求创新点或研究贡献。'),
        dict(id='norms',name='模板规范审查官',task='依据确认的文档规则检查格式、必需章节、引用和交付要求；未提供的要求不得推断为强制规定。'),
        dict(id='comparator',name='修订对照审查官'),dict(id='judge',name='证据裁判官'),
    ]

def profile_for_kind(kind: str) -> str:
    return 'academic' if kind == 'thesis' else 'general'
