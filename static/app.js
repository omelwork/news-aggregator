/**
 * AI News Aggregator - Frontend JavaScript
 * With Theme & Language Toggle Support
 */

// State
let currentFilter = 'all';
let config = {};
let newsData = [];
let originalNewsData = []; // Оригинальные данные для перевода
let currentLang = localStorage.getItem('lang') || 'en';
let currentTheme = localStorage.getItem('theme') || 'dark';

// DOM Elements
const newsGrid = document.getElementById('newsGrid');
const filterBtns = document.querySelectorAll('.filter-btn');
const settingsBtn = document.getElementById('settingsBtn');
const refreshBtn = document.getElementById('refreshBtn');
const themeBtn = document.getElementById('themeBtn');
const langBtn = document.getElementById('langBtn');
const modalOverlay = document.getElementById('modalOverlay');
const modalClose = document.getElementById('modalClose');
const cancelBtn = document.getElementById('cancelBtn');
const saveBtn = document.getElementById('saveBtn');

// Translations for UI
const translations = {
    en: {
        loading: 'Loading news...',
        error: 'Error loading news',
        noNews: 'No news',
        settings: 'Settings',
        refresh: 'Refresh',
        translating: 'Translating...'
    },
    ru: {
        loading: 'Загрузка новостей...',
        error: 'Ошибка загрузки новостей',
        noNews: 'Нет новостей',
        settings: 'Настройки',
        refresh: 'Обновить',
        translating: 'Перевод...'
    }
};

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    applyTheme(currentTheme);
    updateLangButton();
    loadNews();
    loadConfig();
    setupEventListeners();
});

function applyTheme(theme) {
    if (theme === 'light') {
        document.documentElement.setAttribute('data-theme', 'light');
        themeBtn.textContent = '☀️';
    } else {
        document.documentElement.removeAttribute('data-theme');
        themeBtn.textContent = '🌙';
    }
    localStorage.setItem('theme', theme);
    currentTheme = theme;
}

function updateLangButton() {
    langBtn.textContent = currentLang.toUpperCase();
    if (currentLang === 'ru') {
        langBtn.classList.add('active');
    } else {
        langBtn.classList.remove('active');
    }
}

function setupEventListeners() {
    // Theme toggle
    themeBtn.addEventListener('click', () => {
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
        applyTheme(newTheme);
    });

    // Language toggle
    langBtn.addEventListener('click', async () => {
        const newLang = currentLang === 'en' ? 'ru' : 'en';
        currentLang = newLang;
        localStorage.setItem('lang', newLang);
        updateLangButton();

        if (newLang === 'ru' && originalNewsData.length > 0) {
            await translateNews();
        } else if (newLang === 'en') {
            newsData = [...originalNewsData];
            renderNews();
        }
    });

    // Filter buttons
    filterBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            filterBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentFilter = btn.dataset.source;
            renderNews();
        });
    });

    // Refresh button
    refreshBtn.addEventListener('click', () => {
        refreshBtn.disabled = true;
        refreshBtn.innerHTML = '<span class="btn-icon">⏳</span> Loading...';
        loadNews(true).finally(() => {
            refreshBtn.disabled = false;
            refreshBtn.innerHTML = '<span class="btn-icon">↻</span> Refresh';
        });
    });

    // Settings modal
    settingsBtn.addEventListener('click', openSettings);
    modalClose.addEventListener('click', closeSettings);
    cancelBtn.addEventListener('click', closeSettings);
    saveBtn.addEventListener('click', saveSettings);
    modalOverlay.addEventListener('click', (e) => {
        if (e.target === modalOverlay) closeSettings();
    });

    // Add buttons
    document.getElementById('addSubreddit').addEventListener('click', () => addSettingsItem('subreddits'));
    document.getElementById('addRssFeed').addEventListener('click', () => addSettingsItem('rss_feeds'));
    document.getElementById('addKeyword').addEventListener('click', () => addSettingsItem('hackernews_keywords'));
}

// API Functions
async function loadNews(forceRefresh = false) {
    try {
        const t = translations[currentLang];
        newsGrid.innerHTML = `<div class="loading">${t.loading}</div>`;
        const url = forceRefresh ? '/api/news?force_refresh=true' : '/api/news';
        const response = await fetch(url);
        const data = await response.json();
        originalNewsData = data.items || [];
        newsData = [...originalNewsData];

        // If Russian is selected, translate
        if (currentLang === 'ru') {
            await translateNews();
        } else {
            renderNews();
        }
    } catch (error) {
        console.error('Error loading news:', error);
        const t = translations[currentLang];
        newsGrid.innerHTML = `<div class="loading error">${t.error}</div>`;
    }
}

async function translateNews() {
    const t = translations[currentLang];
    newsGrid.innerHTML = `<div class="loading">${t.translating}</div>`;

    try {
        // Translate in batches of 10 to avoid timeout
        const batchSize = 10;
        const translatedItems = [];

        for (let i = 0; i < originalNewsData.length; i += batchSize) {
            const batch = originalNewsData.slice(i, i + batchSize);
            const response = await fetch('/api/translate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ items: batch, target_lang: 'ru' })
            });
            const data = await response.json();
            translatedItems.push(...(data.items || batch));

            // Update progress
            const progress = Math.min(100, Math.round((i + batchSize) / originalNewsData.length * 100));
            newsGrid.innerHTML = `<div class="loading">${t.translating} ${progress}%</div>`;
        }

        newsData = translatedItems;
        renderNews();
    } catch (error) {
        console.error('Translation error:', error);
        newsData = [...originalNewsData];
        renderNews();
    }
}

async function loadConfig() {
    try {
        const response = await fetch('/api/config');
        config = await response.json();
    } catch (error) {
        console.error('Error loading config:', error);
    }
}

// Render Functions
function renderNews() {
    const t = translations[currentLang];
    const filtered = currentFilter === 'all'
        ? newsData
        : newsData.filter(item => item.source === currentFilter);

    if (filtered.length === 0) {
        newsGrid.innerHTML = `<div class="loading">${t.noNews}</div>`;
        return;
    }

    newsGrid.innerHTML = filtered.map(item => createNewsCard(item)).join('');

    // Add click handlers
    document.querySelectorAll('.news-card').forEach(card => {
        card.addEventListener('click', () => {
            window.open(card.dataset.url, '_blank');
        });
    });
}

function createNewsCard(item) {
    const timeAgo = formatTimeAgo(item.published_at || item.fetched_at);
    const description = item.description ? escapeHtml(item.description) : '';
    const author = item.author ? `<div class="card-author">${escapeHtml(item.author)}</div>` : '';

    return `
        <article class="news-card" data-url="${escapeHtml(item.url)}">
            <div class="card-header">
                <div class="card-source">
                    <span class="source-badge ${item.source}">${item.source.toUpperCase()}</span>
                    <span class="source-name">${escapeHtml(item.source_name)}</span>
                </div>
                <span class="card-time">${timeAgo}</span>
            </div>
            <h3 class="card-title">${escapeHtml(item.title)}</h3>
            ${description ? `<p class="card-description">${description}</p>` : ''}
            ${author}
        </article>
    `;
}

// Settings Functions
function openSettings() {
    renderSettingsLists();
    modalOverlay.classList.add('active');
}

function closeSettings() {
    modalOverlay.classList.remove('active');
}

function renderSettingsLists() {
    // Subreddits
    const subredditsList = document.getElementById('subredditsList');
    subredditsList.innerHTML = (config.subreddits || []).map((sub, i) => `
        <div class="settings-item">
            <input type="text" class="settings-input" value="${escapeHtml(sub)}" data-type="subreddits" data-index="${i}">
            <button class="btn btn-remove" onclick="removeSettingsItem('subreddits', ${i})">Remove</button>
        </div>
    `).join('');

    // RSS Feeds
    const rssFeedsList = document.getElementById('rssFeedsList');
    rssFeedsList.innerHTML = (config.rss_feeds || []).map((feed, i) => `
        <div class="settings-item">
            <input type="text" class="settings-input name" value="${escapeHtml(feed.name)}" data-type="rss_name" data-index="${i}" placeholder="Name">
            <input type="text" class="settings-input" value="${escapeHtml(feed.url)}" data-type="rss_url" data-index="${i}" placeholder="URL">
            <button class="btn btn-remove" onclick="removeSettingsItem('rss_feeds', ${i})">Remove</button>
        </div>
    `).join('');

    // Keywords
    const keywordsList = document.getElementById('keywordsList');
    keywordsList.innerHTML = (config.hackernews_keywords || []).map((kw, i) => `
        <div class="settings-item">
            <input type="text" class="settings-input" value="${escapeHtml(kw)}" data-type="hackernews_keywords" data-index="${i}">
            <button class="btn btn-remove" onclick="removeSettingsItem('hackernews_keywords', ${i})">Remove</button>
        </div>
    `).join('');
}

function addSettingsItem(type) {
    if (type === 'subreddits') {
        config.subreddits = config.subreddits || [];
        config.subreddits.push('');
    } else if (type === 'rss_feeds') {
        config.rss_feeds = config.rss_feeds || [];
        config.rss_feeds.push({ name: '', url: '' });
    } else if (type === 'hackernews_keywords') {
        config.hackernews_keywords = config.hackernews_keywords || [];
        config.hackernews_keywords.push('');
    }
    renderSettingsLists();
}

window.removeSettingsItem = function (type, index) {
    if (type === 'subreddits') {
        config.subreddits.splice(index, 1);
    } else if (type === 'rss_feeds') {
        config.rss_feeds.splice(index, 1);
    } else if (type === 'hackernews_keywords') {
        config.hackernews_keywords.splice(index, 1);
    }
    renderSettingsLists();
};

async function saveSettings() {
    // Collect values from inputs
    document.querySelectorAll('.settings-input').forEach(input => {
        const type = input.dataset.type;
        const index = parseInt(input.dataset.index);
        const value = input.value.trim();

        if (type === 'subreddits') {
            config.subreddits[index] = value;
        } else if (type === 'rss_name') {
            config.rss_feeds[index].name = value;
        } else if (type === 'rss_url') {
            config.rss_feeds[index].url = value;
        } else if (type === 'hackernews_keywords') {
            config.hackernews_keywords[index] = value;
        }
    });

    // Filter out empty values
    config.subreddits = config.subreddits.filter(s => s);
    config.rss_feeds = config.rss_feeds.filter(f => f.name && f.url);
    config.hackernews_keywords = config.hackernews_keywords.filter(k => k);

    try {
        await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });
        closeSettings();
        loadNews(true); // Refresh with new settings
    } catch (error) {
        console.error('Error saving config:', error);
        alert('Ошибка сохранения настроек');
    }
}

// Utility Functions
function formatTimeAgo(dateString) {
    if (!dateString) return '';

    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (currentLang === 'ru') {
        if (diffMins < 1) return 'только что';
        if (diffMins < 60) return `${diffMins} мин назад`;
        if (diffHours < 24) return `${diffHours} ч назад`;
        if (diffDays < 30) return `${diffDays} дн назад`;
        return date.toLocaleDateString('ru-RU');
    } else {
        if (diffMins < 1) return 'just now';
        if (diffMins < 60) return `${diffMins}m ago`;
        if (diffHours < 24) return `${diffHours}h ago`;
        if (diffDays < 30) return `${diffDays}d ago`;
        return date.toLocaleDateString();
    }
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
