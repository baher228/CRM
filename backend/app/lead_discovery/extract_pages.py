from collections import defaultdict

from app.lead_discovery.models import ExtractedCompanyPage, SelectedUrl
from app.lead_enrichment.clients.tavily_client import TavilyClient


async def extractPages(
    urls: list[SelectedUrl],
    tavily_client: TavilyClient,
) -> list[ExtractedCompanyPage]:
    selected_by_url = {item.url: item for item in urls}
    urls_by_depth: dict[str, list[str]] = defaultdict(list)
    for item in urls:
        urls_by_depth[item.extract_depth].append(item.url)

    extracted: list[ExtractedCompanyPage] = []
    for depth, batch_urls in urls_by_depth.items():
        results, failed = await tavily_client.extract(
            batch_urls,
            extract_depth=depth,
            query=(
                "Extract public procurement notice details: contract title, buyer, portal, source URL, "
                "contract value, deadline, location, procurement stage, status, CPV codes, buyer website, "
                "contact details, and a short reason this matches the searched niche."
            ),
        )
        extracted.extend(
            ExtractedCompanyPage(
                url=item.get("url", ""),
                domain=selected_by_url[item.get("url", "")].domain,
                page_type=selected_by_url[item.get("url", "")].page_type,
                title=selected_by_url[item.get("url", "")].title,
                content=item.get("raw_content") or item.get("content") or "",
            )
            for item in results
            if item.get("url") in selected_by_url
        )
        extracted.extend(
            ExtractedCompanyPage(
                url=item.get("url", ""),
                domain=selected_by_url[item.get("url", "")].domain,
                page_type=selected_by_url[item.get("url", "")].page_type,
                failed=True,
                error=item.get("error") or "Extraction failed",
            )
            for item in failed
            if item.get("url") in selected_by_url
        )
    return extracted
