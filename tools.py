"""
Tools for the Peter AI assistant.

- get_weather       — fetch current weather for a city via wttr.in
- search_web        — web search (DuckDuckGo) with fetched page content
- search_recent_news— dated, recent news headlines via Google News RSS
- fact_check        — cross-reference web + news to verify a claim
- store_memory      — save facts/preferences to long-term memory (Mem0)
- retrieve_memory   — recall what Peter has been told across sessions (Mem0)
"""

import logging
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, quote_plus

import requests
from ddgs import DDGS

from livekit.agents import RunContext, function_tool
from mem0 import Memory

import file_storage
import pending

# ──────────────────────────────────────────────────────────────
# Long-term memory (Mem0)
# ──────────────────────────────────────────────────────────────
# With MEM0_API_KEY set → uses Mem0 Cloud (shared across devices).
# Without it → falls back to a local store in ./memory_store so Peter
# remembers you across conversations on this computer.
#
# Local mode uses Google Gemini for BOTH the LLM and embeddings (the
# project has GOOGLE_API_KEY set; no OpenAI key required).
def _build_memory() -> Memory:
    try:
        api_key = os.getenv("MEM0_API_KEY")
        if api_key:
            return Memory.from_config(
                {"version": "v2", "api_key": api_key}
            )
        return Memory.from_config(
            {
                "version": "v1.1",
                "vector_store": {
                    "provider": "qdrant",
                    "config": {
                        "path": "./memory_store",
                        # Must match the Gemini embedding output size (768).
                        "embedding_model_dims": 768,
                    },
                },
                "llm": {
                    "provider": "gemini",
                    "config": {
                        "model": "gemini-2.0-flash",
                        "api_key": os.getenv("GOOGLE_API_KEY", ""),
                    },
                },
                "embedder": {
                    "provider": "gemini",
                    "config": {
                        "model": "models/gemini-embedding-001",
                        "api_key": os.getenv("GOOGLE_API_KEY", ""),
                    },
                },
            }
        )
    except Exception as e:
        logging.warning(f"Memory init failed: {e}")
        return None


_memory = _build_memory()


@function_tool()
async def store_memory(
    context: RunContext,  # type: ignore
    fact: str) -> str:
    """
    Store a fact or personal detail in long-term memory.

    Use this whenever the user shares something about themselves — their
    name, preferences, routines, or anything they might want remembered in
    a future conversation.
    """
    if _memory is None:
        return "I couldn't initialize my memory this session, sorry."
    try:
        # infer=False stores the raw fact verbatim (embedding-only, no LLM
        # extraction), so it works even when the Gemini text quota is tight.
        _memory.add(fact, user_id="orlando", infer=False)
        logging.info(f"Stored memory: {fact}")
        return f"Got it, I'll remember that: {fact}"
    except Exception as e:
        logging.error(f"store_memory error: {e}")
        return f"I couldn't store that memory: {e}"


@function_tool()
async def retrieve_memory(
    context: RunContext,  # type: ignore
    query: str) -> str:
    """
    Search long-term memory for something Peter has been told before.

    Use this when the user asks "do you remember...", "what's my name?", or
    references anything they previously shared.
    """
    if _memory is None:
        return "I don't have access to my memories this session."
    try:
        results = _memory.search(
            query,
            filters={"user_id": "orlando"},
        )
        items = results.get("results", []) if isinstance(results, dict) else results or []
        if not items:
            return "I don't think we've covered that before."
        texts = []
        for item in items:
            if isinstance(item, dict):
                texts.append(str(item.get("memory", item.get("text", ""))))
            else:
                texts.append(str(item))
        return "Here's what I remember: " + "; ".join(texts)
    except Exception as e:
        logging.error(f"retrieve_memory error: {e}")
        return f"I couldn't recall that: {e}"


@function_tool()
async def get_weather(
    context: RunContext,  # type: ignore
    city: str) -> str:
    """
    Get the current weather for a given city.
    """
    try:
        response = requests.get(
            f"https://wttr.in/{city}?format=3")
        if response.status_code == 200:
            logging.info(f"Weather for {city}: {response.text.strip()}")
            return response.text.strip()
        else:
            logging.error(f"Failed to get weather for {city}: {response.status_code}")
            return f"Could not retrieve weather for {city}."
    except Exception as e:
        logging.error(f"Error retrieving weather for {city}: {e}")
        return f"An error occurred while retrieving weather for {city}."


# ──────────────────────────────────────────────────────────────
# Fact-checking & current-information tools
# ──────────────────────────────────────────────────────────────
_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    )
}


@function_tool()
async def get_current_date(
    context: RunContext,  # type: ignore
) -> str:
    """
    Get today's exact date and current time.

    ALWAYS call this FIRST before answering anything that depends on time or
    recency — e.g. "what movies came out last week", "what's new this week",
    "today", "this weekend", "recently", "breaking news". Without knowing
    today's date, Peter has no reference point and cannot reason about when
    "last week" or "recent" was.
    """
    now = datetime.now()
    return (
        f"Today is {now.strftime('%A, %B %d, %Y')}. "
        f"The current time is {now.strftime('%I:%M %p')}."
    )


def _ddg_search(query: str, max_results: int = 6) -> list:
    """DuckDuckGo search via DDGS — returns a list of {title, link, snippet}.

    Uses the DDGS client directly (it handles DuckDuckGo's bot challenges),
    which is far more reliable than the raw HTML endpoint or the old
    langchain wrapper. Falls back to the HTML endpoint if DDGS is unavailable.
    """
    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                title = (r.get("title") or "").strip()
                link = (r.get("href") or r.get("url") or "").strip()
                snippet = (r.get("body") or "").strip()
                if link:
                    results.append(
                        {"title": title, "link": link, "snippet": snippet}
                    )
    except Exception as e:
        logging.warning(f"DuckDuckGo DDGS search failed for '{query}': {e}")
        # Fallback: raw HTML endpoint (older duckduckgo-search versions)
        try:
            resp = requests.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers=_UA,
                timeout=12,
            )
            resp.raise_for_status()
            from lxml import html as lxhtml
            doc = lxhtml.fromstring(resp.content)
            for res in doc.xpath("//div[contains(@class,'result')]")[:max_results]:
                a = res.xpath(".//a[contains(@class,'result__a')]")
                sn = res.xpath(".//a[contains(@class,'result__snippet')]")
                if not a:
                    continue
                title = " ".join((a[0].text_content() or "").split())
                link = a[0].get("href", "")
                snippet = " ".join((sn[0].text_content() if sn else "").split())
                results.append(
                    {"title": title, "link": link, "snippet": snippet}
                )
        except Exception as e2:
            logging.warning(f"DuckDuckGo HTML fallback failed too: {e2}")
    return results


def _extract_page_text(url: str, max_chars: int = 3000) -> str:
    """Fetch a URL and return trimmed readable text (title + body)."""
    try:
        resp = requests.get(url, timeout=12, headers=_UA)
        resp.raise_for_status()
        from lxml import html as lxhtml

        doc = lxhtml.fromstring(resp.content)
        # Title
        title = doc.findtext(".//title") or url
        title = " ".join(title.split())
        # Description
        desc = ""
        for meta in doc.xpath(".//meta"):
            name = (meta.get("name") or "").lower()
            prop = (meta.get("property") or "").lower()
            if "description" in f"{name} {prop}":
                desc = (meta.get("content") or "").strip()
                break
        for tag in doc.xpath("//script | //style | //noscript"):
            tag.getparent().remove(tag)
        body = re.sub(r"\s+", " ", doc.text_content()).strip()
        if len(body) > max_chars:
            body = body[:max_chars] + "… [truncated]"
        parts = [f"Title: {title}"]
        if body:
            parts.append(body)
        if desc:
            parts.append(f"Page description: {desc}")
        return "\n\n".join(parts)
    except Exception as e:
        logging.warning(f"Page extract failed for {url}: {e}")
        return ""


@function_tool()
async def search_web(
    context: RunContext,  # type: ignore
    query: str) -> str:
    """
    Search the web for current, real information.

    Use this whenever the user asks a factual question, especially about
    something recent or that may have changed — never rely on your training
    data for current events. Returns dated search snippets PLUS extracted
    content from the top result pages, so you have actual text to cite.
    """
    out = []

    # 1) DuckDuckGo search snippets (reliable DDGS client)
    try:
        res = _ddg_search(query, max_results=6)
        if res:
            lines = ["[Top results by relevance]"]
            for i, r in enumerate(res[:6], 1):
                lines.append(
                    f"{i}. {r['title']} — {r['link']}\n   {r['snippet']}"
                )
            out.append("\n".join(lines))
    except Exception as e:
        logging.warning(f"DuckDuckGo search failed for '{query}': {e}")

    # 2) Fetch real content from the top result pages (dates + text)
    try:
        res = _ddg_search(query, max_results=4)
        if res:
            out.append("[Fetched page content]")
            for r in res[:3]:
                if not r["link"]:
                    continue
                text = _extract_page_text(r["link"])
                out.append(f"--- {r['title']} ---\n{text}")
    except Exception as e:
        logging.warning(f"Content fetch failed for '{query}': {e}")

    if not out:
        return f"I couldn't find reliable results for '{query}'."
    result = "\n\n".join(out)
    logging.info(f"search_web '{query}' -> {len(result)} chars")
    return result


def _google_news_rss(query: str, max_items: int = 8) -> list:
    """Pull dated recent headlines from Google News RSS (free, no key)."""
    items = []
    try:
        # topic-based vs search-based
        if query.strip().lower() in {"", "top", "headlines", "breaking"}:
            url = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
        else:
            url = (
                "https://news.google.com/rss/search?"
                "q=" + quote_plus(query) + "&hl=en-US&gl=US&ceid=US:en"
            )
        resp = requests.get(url, headers=_UA, timeout=12)
        resp.raise_for_status()
        import feedparser
        feed = feedparser.parse(resp.content)
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "")
            link = entry.get("link", "")
            pub = entry.get("published", entry.get("updated", ""))
            try:
                dt = parsedate_to_datetime(pub)
                when = dt.strftime("%b %d, %Y %I:%M %p")
            except Exception:
                when = pub
            items.append(f"{when} — {title} ({link})")
    except Exception as e:
        logging.warning(f"Google News RSS failed for '{query}': {e}")
    return items


@function_tool()
async def search_recent_news(
    context: RunContext,  # type: ignore
    query: str) -> str:
    """
    Search for RECENT news on a topic (default: today's top headlines).

    Use this for breaking news, sports scores, newest movies/releases, current
    events, "what happened this week", etc. Returns dated headlines so Peter
    can see how recent each item is. If the user wants general info that isn't
    time-sensitive, prefer search_web instead.
    """
    items = _google_news_rss(query)
    if not items:
        return f"I couldn't find recent news about '{query}'."
    result = "\n".join(items)
    logging.info(f"search_recent_news '{query}' -> {len(items)} items")
    return result


@function_tool()
async def fact_check(
    context: RunContext,  # type: ignore
    claim: str) -> str:
    """
    Verify a factual claim by cross-referencing recent news AND general web
    search. Use this when Peter is unsure about a fact, or to confirm/deny
    something a user claims is true. Returns corroborating evidence from
    multiple sources so Peter can give an accurate, sourced answer instead
    of guessing or relying on training data.
    """
    out = []
    news = _google_news_rss(claim)
    if news:
        out.append("[Recent news / headlines]")
        out.extend(news[:5])

    try:
        res = _ddg_search(claim)
        if res:
            out.append("[Top web results]")
            for i, r in enumerate(res[:3], 1):
                out.append(
                    f"{i}. {r['title']} — {r['link']}\n   {r['snippet']}"
                )
    except Exception as e:
        logging.warning(f"fact_check search failed for '{claim}': {e}")

    if not out:
        return (
            f"I couldn't corroborate the claim '{claim}' across sources. "
            "I should hedge my answer and note it's unverified."
        )
    return "\n\n".join(out)


@function_tool()
async def get_active_file(
    context: RunContext,  # type: ignore
) -> str:
    """
    Read the content of the user's currently-selected file (e.g. their resume).

    Call this when the user references "my resume", "the file", "my file", or
    asks you to work with / analyze / improve a file they gave you. Returns the
    file's name plus its readable text content so Peter can analyze it. If no
    file is selected, returns a message telling Peter to ask the user to pick a
    file in the app's Files menu.
    """
    name = file_storage.get_active_file()
    if not name:
        return (
            "You haven't selected a file yet. Please give me a file in the "
            "app's Files menu (drop it or pick it), then ask me again."
        )
    content = file_storage.read_file_content(name)
    if not content:
        return (
            f"The active file '{name}' is empty or couldn't be read. "
            "Tell the user to try a different file."
        )
    return f"[Active file: {name}]{chr(10)}{chr(10)}{content}"


# ──────────────────────────────────────────────────────────────
# Pending URL ("go peter") tools
# ──────────────────────────────────────────────────────────────
@function_tool()
async def get_pending_url(
    context: RunContext,  # type: ignore
) -> str:
    """
    Read the URL the user typed into the desktop app's URL box.

    Call this when the user says "go peter" (or otherwise clearly instructs
    you to act on the URL they pasted). Returns the pending URL string, or an
    empty string / "I don't see any URL" message if the box is blank. If the
    URL is blank, do NOT try to act on it — just respond naturally like a
    normal conversation.
    """
    url = pending.get_pending_url()
    if not url:
        return "NO_URL"
    return url


@function_tool()
async def fetch_url(
    context: RunContext,  # type: ignore
    url: str) -> str:
    """
    Fetch and extract readable text content from a web page or video page.

    Use this when the user wants you to look at / learn from / act on a URL.
    Returns a trimmed plain-text snapshot (title + body) so you can analyze
    it. YouTube/video pages return metadata like the title and description.
    """
    url = (url or "").strip()
    if not url:
        return "No URL was provided."

    # Limit how much text we return so we don't blow up the model context.
    MAX_CHARS = 12000

    # Videos often publish metadata via oEmbed (YouTube, Vimeo, etc.).
    try:
        oe = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": url, "format": "json"},
            timeout=8,
        )
        if oe.status_code == 200:
            data = oe.json()
            title = data.get("title", "")
            author = data.get("author_name", "")
            if title:
                return (
                    f"[Video] Title: {title} | Author: {author}\n\n"
                    "Note: This is the video's metadata. I couldn't pull the "
                    "spoken transcript from here directly."
                )
    except Exception:
        pass

    try:
        parsed = urlparse(url)
        if not parsed.scheme:
            url = "https://" + url  # allow bare "example.com"
            parsed = urlparse(url)

        resp = requests.get(
            url,
            timeout=10,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                )
            },
        )
        resp.raise_for_status()

        from lxml import html as lxhtml

        doc = lxhtml.fromstring(resp.content)

        # Title
        title = doc.findtext(".//title") or parsed.netloc
        title = " ".join(title.split())

        # Description meta
        desc = ""
        for meta in doc.xpath(".//meta"):
            name = (meta.get("name") or "").lower()
            prop = (meta.get("property") or "").lower()
            if "description" in f"{name} {prop}":
                desc = (meta.get("content") or "").strip()
                break

        # Visible body text
        for tag in doc.xpath("//script | //style | //noscript"):
            tag.getparent().remove(tag)
        body = doc.text_content()
        body = re.sub(r"\s+", " ", body).strip()
        if len(body) > MAX_CHARS:
            body = body[:MAX_CHARS] + "… [truncated]"

        parts = [f"Title: {title}"]
        if desc:
            parts.append(f"Description: {desc}")
        if body:
            parts.append(f"Content:\n{body}")
        return "\n\n".join(parts)
    except Exception as e:
        logging.error(f"fetch_url error for {url}: {e}")
        return f"I couldn't fetch that URL ({url}). Error: {e}"

