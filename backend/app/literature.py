"""Bounded public metadata search. Only retrieved text becomes evidence."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import html
import json
import re
from typing import Literal
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field, model_validator


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class LiteratureQuery(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    year_from: int | None = Field(default=None, ge=1500, le=2100)
    year_to: int | None = Field(default=None, ge=1500, le=2100)
    page: int = Field(default=1, ge=1, le=100)
    page_size: int = Field(default=8, ge=1, le=20)

    @model_validator(mode="after")
    def validate_query(self):
        self.query = self.query.strip()
        if len(self.query) < 2:
            raise ValueError("请输入至少两个字的检索词。")
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("起始年份应早于结束年份。")
        return self


class LiteratureSource(BaseModel):
    id: str
    title: str
    authors: list[str]
    year: int | None
    doi: str | None
    url: str
    providers: list[str]
    text: str
    text_level: Literal["metadata", "abstract", "full_text"]
    text_hash: str
    retrieved_at: str
    query: str


def plain(value, limit=20_000) -> str:
    return html.unescape(re.sub(r"<[^>]*>", " ", str(value or ""))).strip()[:limit]


def normalize_doi(value) -> str | None:
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", str(value or "").strip(), flags=re.I)
    return value.lower() if re.fullmatch(r"10\.\d{4,9}/\S+", value, re.I) else None


def safe_url(value) -> str:
    value = str(value or "")
    parsed = urlparse(value)
    return value if parsed.scheme in {"https", "http"} and parsed.netloc and not parsed.username else ""


def fetch_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "ThesisDebate/0.0.2 (literature search)", "Accept": "application/json"})
    with urlopen(request, timeout=12) as response:
        data = response.read(4_000_001)
    if len(data) > 4_000_000:
        raise ValueError("response too large")
    return json.loads(data)


def _source(item, provider: str, query: str) -> dict | None:
    if provider == "Crossref":
        title = plain((item.get("title") or [""])[0], 1500)
        authors = [plain(" ".join(filter(None, [a.get("given"), a.get("family")])) or a.get("name"), 200) for a in (item.get("author") or [])[:30]]
        dates = (item.get("published") or item.get("issued") or {}).get("date-parts", [[]])
        year = dates[0][0] if dates and dates[0] else None
        text = plain(item.get("abstract"))
        doi = normalize_doi(item.get("DOI"))
        url = safe_url(item.get("URL"))
    else:
        title = plain(item.get("title") or item.get("display_name"), 1500)
        authors = [plain((a.get("author") or {}).get("display_name"), 200) for a in (item.get("authorships") or [])[:30]]
        year = item.get("publication_year")
        inverted = item.get("abstract_inverted_index") or {}
        words = {p: word for word, positions in inverted.items() for p in positions if isinstance(p, int) and 0 <= p < 10000}
        text = plain(" ".join(words[p] for p in sorted(words)))
        doi = normalize_doi(item.get("doi"))
        url = safe_url((item.get("primary_location") or {}).get("landing_page_url") or item.get("id"))
    if not title:
        return None
    key = doi or re.sub(r"\W+", "", title.casefold())
    return LiteratureSource(id="lit-" + digest(key)[:24], title=title, authors=[a for a in authors if a],
                            year=year if isinstance(year, int) else None, doi=doi,
                            url="https://doi.org/" + quote(doi, safe="/") if doi else url,
                            providers=[provider], text=text, text_level="abstract" if text else "metadata",
                            text_hash=digest(text), retrieved_at=now(), query=query).model_dump()


def _provider_search(provider: str, query: LiteratureQuery) -> dict:
    doi = normalize_doi(query.query)
    if provider == "Crossref":
        params = {"query.bibliographic": query.query, "rows": query.page_size,
                  "offset": (query.page - 1) * query.page_size}
        filters = []
        if query.year_from:
            filters.append(f"from-pub-date:{query.year_from}-01-01")
        if query.year_to:
            filters.append(f"until-pub-date:{query.year_to}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        url = "https://api.crossref.org/works/" + quote(doi, safe="") if doi else "https://api.crossref.org/works?" + urlencode(params)
        response = fetch_json(url)["message"]
        items = [response] if doi and query.page == 1 else [] if doi else response.get("items", [])
        more = not doi and response.get("total-results", 0) > query.page * query.page_size
    else:
        params = {"search": query.query, "per_page": query.page_size, "page": query.page}
        filters = []
        if query.year_from or query.year_to:
            filters.append(f"publication_year:{query.year_from or 1500}-{query.year_to or 2100}")
        if doi:
            params.pop("search")
            filters.append("doi:https://doi.org/" + doi)
        if filters:
            params["filter"] = ",".join(filters)
        response = fetch_json("https://api.openalex.org/works?" + urlencode(params))
        items = response.get("results", [])
        more = response.get("meta", {}).get("count", 0) > query.page * query.page_size
    results = []
    for item in items:
        value = _source(item, provider, query.query)
        if value and (not query.year_from or value["year"] and value["year"] >= query.year_from) and (not query.year_to or value["year"] and value["year"] <= query.year_to):
            results.append(value)
    return {"results": results, "has_more": bool(more)}


def search_literature(query: LiteratureQuery) -> dict:
    merged, warnings, statuses = {}, [], []
    has_more = False
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [(provider, pool.submit(_provider_search, provider, query)) for provider in ("Crossref", "OpenAlex")]
        for provider, future in futures:
            try:
                response = future.result()
                statuses.append({"provider": provider, "status": "ok"})
                has_more |= response["has_more"]
                for item in response["results"]:
                    key = item["id"]
                    title_key = re.sub(r"\W+", "", item["title"].casefold())
                    if key not in merged:
                        key = next((k for k, previous in merged.items() if not (previous["doi"] and item["doi"]) and
                                    re.sub(r"\W+", "", previous["title"].casefold()) == title_key), key)
                    if key not in merged:
                        merged[key] = item
                    else:
                        previous = merged[key]
                        previous["providers"] = list(dict.fromkeys(previous["providers"] + item["providers"]))
                        if not previous["doi"] and item["doi"]:
                            previous.update(doi=item["doi"], url=item["url"])
                        if len(item["text"]) > len(previous["text"]):
                            previous.update({k: item[k] for k in ("text", "text_level", "text_hash")})
            except Exception as error:
                code = error.code if isinstance(error, HTTPError) else None
                message = "请求频繁，请稍后重试。" if code == 429 else "连接超时或服务暂不可用，请重试。"
                if code == 404 and doi_query(query.query):
                    statuses.append({"provider": provider, "status": "ok"})
                else:
                    warnings.append(provider + "：" + message)
                    statuses.append({"provider": provider, "status": "error", "http_status": code})
    return {**query.model_dump(), "results": list(merged.values()), "has_more": has_more,
            "providers": statuses, "warnings": warnings, "retrieved_at": now(),
            "status": "failed" if len(warnings) == 2 else "partial" if warnings else "completed"}


def doi_query(query: str) -> bool:
    return normalize_doi(query) is not None
