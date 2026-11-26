"""Strict model-output contracts for the manuscript review agents."""
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Evidence(StrictOutput):
    source_id: str = Field(min_length=1, max_length=160)
    quote: str = Field(min_length=1, max_length=1000)


class ProposedFinding(StrictOutput):
    title: str = Field(min_length=1, max_length=200)
    severity: Literal["low", "medium", "high"]
    quote: str = Field(max_length=4000)
    replacement: Optional[str] = Field(max_length=4000)
    rationale: str = Field(min_length=1, max_length=1500)
    evidence: List[Evidence] = Field(max_length=3)
    verdict: Literal["supported", "needs_evidence", "conflict"]


class AgentOutput(StrictOutput):
    summary: str = Field(min_length=1, max_length=3000)
    findings: List[ProposedFinding] = Field(max_length=6)


class FindingAssessment(StrictOutput):
    finding_id: str = Field(min_length=1, max_length=100)
    verdict: Literal["supported", "needs_evidence", "conflict"]
    rationale: str = Field(min_length=1, max_length=1500)


class Challenge(StrictOutput):
    agent_id: Literal["methods", "innovation", "norms", "comparator"]
    finding_id: Optional[str] = Field(max_length=100)
    question: str = Field(min_length=1, max_length=1500)


class JudgeOutput(StrictOutput):
    summary: str = Field(min_length=1, max_length=3000)
    assessments: List[FindingAssessment] = Field(max_length=12)
    challenges: List[Challenge] = Field(max_length=3)
