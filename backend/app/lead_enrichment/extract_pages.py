from app.lead_enrichment.clients.tavily_client import TavilyClient
from app.lead_enrichment.models import ExtractedPage, FoundPage


async def extractPages(
    pages: list[FoundPage],
    tavily_client: TavilyClient,
) -> list[ExtractedPage]:
    if not pages:
        return []
    by_url = {page.url: page for page in pages}
    results, failed = await tavily_client.extract(list(by_url))
    extracted = [
        ExtractedPage(
            url=item.get("url", ""),
            title=by_url.get(item.get("url", ""), FoundPage(url=item.get("url", ""))).title,
            content=item.get("raw_content") or item.get("content") or "",
            page_type=by_url.get(item.get("url", ""), FoundPage(url=item.get("url", ""))).page_type,
        )
        for item in results
        if item.get("url")
    ]
    extracted.extend(
        ExtractedPage(
            url=item.get("url", ""),
            page_type=by_url.get(item.get("url", ""), FoundPage(url=item.get("url", ""))).page_type,
            failed=True,
            error=item.get("error") or "Extraction failed",
        )
        for item in failed
        if item.get("url")
    )
    return extracted

