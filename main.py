"""
AI News Aggregator - FastAPI Backend
Агрегатор новостей с SQLite базой данных (хранение 3 дня)
"""

import json
import sqlite3
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
DB_FILE = BASE_DIR / "news.db"
STATIC_DIR = BASE_DIR / "static"

# TTL для хранения новостей (3 дня)
NEWS_TTL_DAYS = 3

# Авторский пресет каналов
AUTHOR_PRESET = {
    "subreddits": [
        "MachineLearning",
        "artificial",
        "ArtificialIntelligence",
        "LocalLLaMA",
        "singularity"
    ],
    "rss_feeds": [
        {"name": "Google AI Blog", "url": "https://blog.google/technology/ai/rss/"},
        {"name": "HuggingFace Blog", "url": "https://huggingface.co/blog/feed.xml"},
        {"name": "AWS ML Blog", "url": "https://aws.amazon.com/blogs/machine-learning/feed/"}
    ],
    "hackernews_keywords": [
        "AI", "GPT", "LLM", "machine learning", "deep learning"
    ],
    "cache_ttl_hours": 36,
    "refresh_interval_minutes": 15
}

# Монтируем статику
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def get_db_connection():
    """Получение соединения с БД"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Инициализация базы данных"""
    conn = get_db_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_name TEXT,
            title TEXT NOT NULL,
            description TEXT,
            url TEXT NOT NULL,
            author TEXT,
            published_at TEXT,
            fetched_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_fetched_at ON news(fetched_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_source ON news(source)")
    conn.commit()
    conn.close()
    print("✅ SQLite database initialized")


# Инициализация БД при старте
init_db()


def load_config() -> dict:
    """Загрузка конфигурации"""
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {
        "subreddits": ["MachineLearning", "artificial"],
        "rss_feeds": [],
        "hackernews_keywords": ["AI", "GPT"],
        "refresh_interval_minutes": 15
    }


def save_config(config: dict):
    """Сохранение конфигурации"""
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def get_last_updated() -> Optional[str]:
    """Получение времени последнего обновления"""
    conn = get_db_connection()
    cursor = conn.execute("SELECT value FROM metadata WHERE key = 'last_updated'")
    row = cursor.fetchone()
    conn.close()
    return row["value"] if row else None


def set_last_updated(timestamp: str):
    """Установка времени последнего обновления"""
    conn = get_db_connection()
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_updated', ?)",
        (timestamp,)
    )
    conn.commit()
    conn.close()


def save_news_items(items: list):
    """Сохранение новостей в БД"""
    conn = get_db_connection()
    for item in items:
        conn.execute("""
            INSERT OR REPLACE INTO news 
            (id, source, source_name, title, description, url, author, published_at, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item["id"],
            item["source"],
            item.get("source_name"),
            item["title"],
            item.get("description"),
            item["url"],
            item.get("author"),
            item.get("published_at"),
            item["fetched_at"]
        ))
    conn.commit()
    conn.close()


def clean_old_news():
    """Удаление новостей старше TTL"""
    cutoff = (datetime.now() - timedelta(days=NEWS_TTL_DAYS)).isoformat()
    conn = get_db_connection()
    cursor = conn.execute("DELETE FROM news WHERE fetched_at < ?", (cutoff,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    if deleted > 0:
        print(f"🧹 Cleaned {deleted} old news items")


def get_news_from_db(source: Optional[str] = None) -> list:
    """Получение новостей из БД"""
    conn = get_db_connection()
    if source:
        cursor = conn.execute(
            "SELECT * FROM news WHERE source = ? ORDER BY published_at DESC",
            (source,)
        )
    else:
        cursor = conn.execute("SELECT * FROM news ORDER BY published_at DESC")
    
    items = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return items


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
    
    # Проверяем нужно ли обновить
    should_refresh = force_refresh
    last_updated = get_last_updated()
    
    if not should_refresh and last_updated:
        try:
            last_update = datetime.fromisoformat(last_updated)
            refresh_interval = timedelta(minutes=config.get("refresh_interval_minutes", 15))
            if datetime.now() - last_update > refresh_interval:
                should_refresh = True
        except ValueError:
            should_refresh = True
    
    if should_refresh or not last_updated:
        # Получаем свежие данные
        items = await fetch_all_sources()
        
        # Сохраняем в БД
        save_news_items(items)
        
        # Очищаем старые записи
        clean_old_news()
        
        # Обновляем время
        now = datetime.now().isoformat()
        set_last_updated(now)
        last_updated = now
    
    # Получаем из БД
    items = get_news_from_db(source)
    
    return {
        "items": items,
        "last_updated": last_updated,
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


@app.get("/api/config/preset")
async def get_author_preset():
    """Получение авторского пресета каналов"""
    return AUTHOR_PRESET


@app.get("/api/stats")
async def get_stats():
    """Статистика базы данных"""
    conn = get_db_connection()
    cursor = conn.execute("SELECT COUNT(*) as total FROM news")
    total = cursor.fetchone()["total"]
    
    cursor = conn.execute("SELECT source, COUNT(*) as count FROM news GROUP BY source")
    by_source = {row["source"]: row["count"] for row in cursor.fetchall()}
    
    conn.close()
    
    return {
        "total_items": total,
        "by_source": by_source,
        "ttl_days": NEWS_TTL_DAYS
    }


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
    print("🚀 Starting AI News Aggregator with SQLite...")
    print("📍 Open http://localhost:8000 in your browser")
    uvicorn.run(app, host="0.0.0.0", port=8000)
