"""
AI News Aggregator - FastAPI Backend
Локальный агрегатор новостей с JSON-кэшированием (TTL 36 часов)
"""

import json
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import feedparser
import httpx
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI(title="AI News Aggregator")

# Пути к файлам
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
CACHE_FILE = BASE_DIR / "cache.json"
STATIC_DIR = BASE_DIR / "static"

# Монтируем статику
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def load_config() -> dict:
    """Загрузка конфигурации"""
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {
        "subreddits": ["MachineLearning", "artificial"],
        "rss_feeds": [],
        "hackernews_keywords": ["AI", "GPT"],
        "cache_ttl_hours": 36,
        "refresh_interval_minutes": 15
    }


def save_config(config: dict):
    """Сохранение конфигурации"""
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def load_cache() -> dict:
    """Загрузка кэша"""
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {"items": [], "last_updated": None}


def save_cache(cache: dict):
    """Сохранение кэша"""
    CACHE_FILE.write_text(json.dumps(cache, indent=2, default=str))


def clean_old_items(items: list, ttl_hours: int) -> list:
    """Удаление элементов старше TTL"""
    cutoff = datetime.now() - timedelta(hours=ttl_hours)
    cleaned = []
    for item in items:
        try:
            item_time = datetime.fromisoformat(item.get("fetched_at", ""))
            if item_time > cutoff:
                cleaned.append(item)
        except (ValueError, TypeError):
            # Если не можем распарсить дату - пропускаем
            pass
    return cleaned


async def fetch_reddit(subreddits: list) -> list:
    """Получение постов из Reddit"""
    items = []
    async with httpx.AsyncClient() as client:
        for subreddit in subreddits:
            try:
                url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=10"
                headers = {"User-Agent": "NewsAggregator/1.0"}
                resp = await client.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    for post in data.get("data", {}).get("children", []):
                        p = post["data"]
                        items.append({
                            "id": f"reddit_{p['id']}",
                            "source": "reddit",
                            "source_name": f"r/{subreddit}",
                            "title": p.get("title", ""),
                            "description": p.get("selftext", "")[:300] or None,
                            "url": f"https://reddit.com{p.get('permalink', '')}",
                            "author": p.get("author"),
                            "published_at": datetime.fromtimestamp(p.get("created_utc", 0)).isoformat(),
                            "fetched_at": datetime.now().isoformat()
                        })
            except Exception as e:
                print(f"Reddit error ({subreddit}): {e}")
    return items


async def fetch_hackernews(keywords: list) -> list:
    """Получение постов из Hacker News через Algolia API"""
    items = []
    async with httpx.AsyncClient() as client:
        for keyword in keywords[:3]:  # Ограничим количество запросов
            try:
                url = f"https://hn.algolia.com/api/v1/search_by_date?query={keyword}&tags=story&hitsPerPage=10"
                resp = await client.get(url, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    for hit in data.get("hits", []):
                        item_id = f"hn_{hit.get('objectID', '')}"
                        # Проверяем дубликаты
                        if not any(i["id"] == item_id for i in items):
                            items.append({
                                "id": item_id,
                                "source": "hackernews",
                                "source_name": "Hacker News",
                                "title": hit.get("title", ""),
                                "description": None,
                                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                                "author": hit.get("author"),
                                "published_at": hit.get("created_at"),
                                "fetched_at": datetime.now().isoformat()
                            })
            except Exception as e:
                print(f"HN error ({keyword}): {e}")
    return items


async def fetch_rss(feeds: list) -> list:
    """Получение постов из RSS лент"""
    items = []
    async with httpx.AsyncClient() as client:
        for feed in feeds:
            try:
                resp = await client.get(feed["url"], timeout=10)
                if resp.status_code == 200:
                    parsed = feedparser.parse(resp.text)
                    for entry in parsed.entries[:10]:
                        pub_date = None
                        if hasattr(entry, 'published_parsed') and entry.published_parsed:
                            pub_date = datetime(*entry.published_parsed[:6]).isoformat()
                        
                        items.append({
                            "id": f"rss_{hash(entry.get('link', '') + feed['name'])}",
                            "source": "blog",
                            "source_name": feed["name"],
                            "title": entry.get("title", ""),
                            "description": entry.get("summary", "")[:300] if entry.get("summary") else None,
                            "url": entry.get("link", ""),
                            "author": entry.get("author"),
                            "published_at": pub_date,
                            "fetched_at": datetime.now().isoformat()
                        })
            except Exception as e:
                print(f"RSS error ({feed['name']}): {e}")
    return items


async def fetch_arxiv() -> list:
    """Получение статей из arXiv (категория cs.AI и cs.LG)"""
    items = []
    async with httpx.AsyncClient() as client:
        try:
            url = "https://export.arxiv.org/api/query?search_query=cat:cs.AI+OR+cat:cs.LG&start=0&max_results=20&sortBy=submittedDate&sortOrder=descending"
            resp = await client.get(url, timeout=15)
            if resp.status_code == 200:
                parsed = feedparser.parse(resp.text)
                for entry in parsed.entries:
                    pub_date = None
                    if hasattr(entry, 'published_parsed') and entry.published_parsed:
                        pub_date = datetime(*entry.published_parsed[:6]).isoformat()
                    
                    # Получаем имя первого автора
                    author = None
                    if hasattr(entry, 'authors') and entry.authors:
                        author = entry.authors[0].get('name', '')
                    
                    items.append({
                        "id": f"arxiv_{entry.get('id', '').split('/')[-1]}",
                        "source": "arxiv",
                        "source_name": "arXiv",
                        "title": entry.get("title", "").replace("\n", " "),
                        "description": entry.get("summary", "")[:400].replace("\n", " ") if entry.get("summary") else None,
                        "url": entry.get("link", ""),
                        "author": author,
                        "published_at": pub_date,
                        "fetched_at": datetime.now().isoformat()
                    })
        except Exception as e:
            print(f"arXiv error: {e}")
    return items


async def fetch_all_sources() -> list:
    """Параллельный сбор из всех источников"""
    config = load_config()
    
    tasks = [
        fetch_reddit(config.get("subreddits", [])),
        fetch_hackernews(config.get("hackernews_keywords", [])),
        fetch_rss(config.get("rss_feeds", [])),
        fetch_arxiv()
    ]
    
    results = await asyncio.gather(*tasks)
    all_items = []
    for result in results:
        all_items.extend(result)
    
    # Сортировка по дате публикации (новые первыми)
    all_items.sort(
        key=lambda x: x.get("published_at") or x.get("fetched_at") or "",
        reverse=True
    )
    
    return all_items


@app.get("/")
async def index():
    """Главная страница"""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/news")
async def get_news(
    source: Optional[str] = Query(None, description="Фильтр по источнику"),
    force_refresh: bool = Query(False, description="Принудительное обновление")
):
    """Получение новостей"""
    config = load_config()
    cache = load_cache()
    
    # Проверяем нужно ли обновить кэш
    should_refresh = force_refresh
    if not should_refresh and cache.get("last_updated"):
        try:
            last_update = datetime.fromisoformat(cache["last_updated"])
            refresh_interval = timedelta(minutes=config.get("refresh_interval_minutes", 15))
            if datetime.now() - last_update > refresh_interval:
                should_refresh = True
        except ValueError:
            should_refresh = True
    
    if should_refresh or not cache.get("items"):
        # Получаем свежие данные
        items = await fetch_all_sources()
        
        # Очищаем старые и сохраняем
        ttl = config.get("cache_ttl_hours", 36)
        items = clean_old_items(items, ttl)
        
        cache = {
            "items": items,
            "last_updated": datetime.now().isoformat()
        }
        save_cache(cache)
    
    items = cache.get("items", [])
    
    # Фильтрация по источнику
    if source:
        items = [i for i in items if i.get("source") == source]
    
    return {
        "items": items,
        "last_updated": cache.get("last_updated"),
        "total": len(items)
    }


@app.post("/api/refresh")
async def refresh_news():
    """Принудительное обновление новостей"""
    return await get_news(force_refresh=True)


@app.get("/api/config")
async def get_config():
    """Получение конфигурации"""
    return load_config()


@app.post("/api/config")
async def update_config(config: dict):
    """Обновление конфигурации"""
    save_config(config)
    return {"status": "ok"}


# Translation support
try:
    from deep_translator import GoogleTranslator
    TRANSLATOR_AVAILABLE = True
except ImportError:
    TRANSLATOR_AVAILABLE = False
    print("⚠️ deep-translator not installed. Translation disabled.")


@app.post("/api/translate")
async def translate_news(data: dict):
    """Перевод новостей на русский язык"""
    if not TRANSLATOR_AVAILABLE:
        return {"error": "Translator not available", "items": data.get("items", [])}
    
    items = data.get("items", [])
    target_lang = data.get("target_lang", "ru")
    
    if target_lang == "en":
        # Возвращаем оригинал
        return {"items": items}
    
    translated_items = []
    translator = GoogleTranslator(source='en', target=target_lang)
    
    for item in items:
        try:
            translated_item = item.copy()
            
            # Переводим заголовок
            if item.get("title"):
                translated_item["title_original"] = item["title"]
                translated_item["title"] = translator.translate(item["title"])
            
            # Переводим описание
            if item.get("description"):
                translated_item["description_original"] = item["description"]
                translated_item["description"] = translator.translate(item["description"])
            
            translated_items.append(translated_item)
        except Exception as e:
            print(f"Translation error: {e}")
            translated_items.append(item)
    
    return {"items": translated_items}


if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting AI News Aggregator...")
    print("📍 Open http://localhost:8000 in your browser")
    uvicorn.run(app, host="0.0.0.0", port=8000)
