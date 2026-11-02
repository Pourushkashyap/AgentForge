from typing import Literal
from pydantic import BaseModel, Field



class SearchResult(BaseModel):
    title: str
    url: str
    content: str
    published_date: str | None = None
    score: float | None = None


class ResearchQueryPlan(BaseModel):
    market_scope: str

    key_questions: list[str] = Field(
        min_length=3,
        max_length=8
    )

    search_queries: list[str] = Field(
        min_length=3,
        max_length=6
    )


class MarketFinding(BaseModel):
    claim: str
    explanation: str

    evidence_type: Literal[
        "direct_evidence",
        "inference",
        "uncertain"
    ]

    confidence: Literal[
        "high",
        "medium",
        "low"
    ]

    source_urls: list[str] = Field(
        default_factory=list
    )


class MarketResearchReport(BaseModel):
    business_idea: str
    market_scope: str
    executive_summary: str

    market_findings: list[MarketFinding]
    opportunities: list[str]
    risks_and_unknowns: list[str]

    recommended_next_research: list[str]
    sources: list[str]
    limitations: list[str]
    

class CompetitorProfile(BaseModel):
    name: str
    website: str | None = None

    target_audience: str
    positioning: str

    features: list[str] = Field(default_factory=list)

    pricing_status: Literal[
        "publicly_available",
        "not_found",
        "unclear"
    ] = "unclear"

    pricing_details: str | None = None
    pricing_model: str | None = None

    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)

    source_urls: list[str] = Field(default_factory=list)


class CompetitorResearchReport(BaseModel):
    business_idea: str
    market_scope: str

    competitors: list[CompetitorProfile] = Field(
        default_factory=list,
        max_length=8
    )

    competitive_gaps: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    risks_and_unknowns: list[str] = Field(default_factory=list)

    sources: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    
class ResearchDepartmentReport(BaseModel):
    business_idea: str
    market_scope: str

    market_research: MarketResearchReport
    competitor_research: CompetitorResearchReport

    combined_opportunities: list[str] = Field(default_factory=list)
    combined_risks: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)    