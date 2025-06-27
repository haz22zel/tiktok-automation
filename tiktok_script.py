import asyncio
import nest_asyncio
import json
import os
from datetime import datetime
from playwright.async_api import async_playwright
from TikTokApi import TikTokApi
import psycopg2

nest_asyncio.apply()

# ✅ FREE PROXY LIST (Webshare)
proxy_list = [
    {"ip": "198.23.239.134", "port": 6540},
    {"ip": "207.244.217.165", "port": 6712},
    {"ip": "107.172.163.27", "port": 6543},
    {"ip": "23.94.138.75", "port": 6349},
    {"ip": "216.155.158.159", "port": 6837}
]

# ✅ COMMON DESKTOP USER AGENT
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

async def get_single_ms_token(playwright, proxy=None):
    ip = proxy["ip"]
    port = proxy["port"]
    username = os.getenv("PROXY_USER")
    password = os.getenv("PROXY_PASS")

    proxy_config = {
        "server": f"http://{ip}:{port}",
        "username": username,
        "password": password
    }

    print(f"🌐 Opening TikTok with proxy: ***{ip}:{port}")
    try:
        browser = await playwright.chromium.launch(
            headless=True,
            proxy=proxy_config
        )
        context = await browser.new_context(
            user_agent=USER_AGENT
        )
        page = await context.new_page()

        await page.goto("https://www.tiktok.com", timeout=60000)
        await page.wait_for_timeout(15000)
        cookies = await context.cookies()
        await browser.close()

        ms_tokens = [c["value"] for c in cookies if c["name"] == "msToken"]
        return ms_tokens[-1] if ms_tokens else None
    except Exception as e:
        print(f"❌ Proxy failed: ***{ip}:{port} → {e}")
        return None

async def collect_ms_tokens(n=6):
    tokens = []
    async with async_playwright() as p:
        for i in range(n):
            proxy = proxy_list[i % len(proxy_list)]
            print(f"\n🔁 Session {i+1} using proxy: {proxy}")
            token = await get_single_ms_token(p, proxy=proxy)
            if token:
                print(f"✅ Token #{i+1}: {token[:50]}...")
                tokens.append(token)
            else:
                print(f"❌ Failed to retrieve token #{i+1}")
    return tokens

async def main():
    ms_token_list = await collect_ms_tokens(6)
    all_data = []

    async with TikTokApi() as api:
        await api.create_sessions(
            ms_tokens=ms_token_list,
            num_sessions=len(ms_token_list),
            browser="chromium",
            headless=True
        )

        for i, session in enumerate(api.sessions):
            print(f"\n📅 Scraping with session #{i+1}")
            count = 0
            try:
                async for video in api.trending.videos(session=session, count=30):
                    all_data.append(video.as_dict)
                    count += 1
                print(f"✅ Retrieved {count} videos from session #{i+1}")
            except Exception as e:
                print(f"⚠️ Failed on session #{i+1}: {e}")

    print(f"\n📊 Total videos collected: {len(all_data)}")

    unique_videos = {}
    for v in all_data:
        video_id = v.get("id")
        author_id = v.get("author", {}).get("uniqueId")

        if not video_id or video_id in unique_videos:
            continue

        unique_videos[video_id] = {
            "video_id": video_id,
            "author_id": author_id,
            "video_url": f"https://www.tiktok.com/@{author_id}/video/{video_id}" if author_id else None,
            "description": v.get("desc"),
            "create_time": datetime.fromtimestamp(v.get("createTime")).isoformat() if v.get("createTime") else None,
            "author_name": v.get("author", {}).get("nickname"),
            "likes": v.get("stats", {}).get("diggCount"),
            "views": v.get("stats", {}).get("playCount"),
            "comments": v.get("stats", {}).get("commentCount"),
            "shares": v.get("stats", {}).get("shareCount"),
            "music_title": v.get("music", {}).get("title"),
            "music_author_name": v.get("music", {}).get("authorName"),
            "video_duration": v.get("video", {}).get("duration"),
            "cover_image": v.get("video", {}).get("cover"),
            "hashtags": [tag.get("hashtagName") for tag in v.get("textExtra", []) if "hashtagName" in tag],
            "challenges": [c.get("title") for c in v.get("challenges", []) if "title" in c]
        }

    deduped_cleaned = list(unique_videos.values())
    print("📦 Number of unique videos after deduplication:", len(deduped_cleaned))

    db_config = {
        "dbname": os.getenv("DB_NAME"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "host": os.getenv("DB_HOST"),
        "port": os.getenv("DB_PORT", "5432")
    }

    try:
        conn = psycopg2.connect(**db_config)
        cursor = conn.cursor()

        insert_query = """
        INSERT INTO tiktok_videos (
            video_id, author_id, video_url, description, create_time,
            author_name, likes, views, comments, shares,
            music_title, music_author_name, video_duration,
            cover_image, hashtags, challenges
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (video_id) DO NOTHING
        """

        for video in deduped_cleaned:
            cursor.execute(insert_query, (
                video.get("video_id"),
                video.get("author_id"),
                video.get("video_url"),
                video.get("description"),
                video.get("create_time"),
                video.get("author_name"),
                video.get("likes"),
                video.get("views"),
                video.get("comments"),
                video.get("shares"),
                video.get("music_title"),
                video.get("music_author_name"),
                video.get("video_duration"),
                video.get("cover_image"),
                json.dumps(video.get("hashtags")),
                json.dumps(video.get("challenges"))
            ))

        conn.commit()
        cursor.close()
        conn.close()
        print("✅ All videos inserted into PostgreSQL database.")

    except Exception as e:
        print(f"\n🚨 DB Upload Error: {e}")

    output_file = "tiktok_trending_cleaned.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(deduped_cleaned, f, ensure_ascii=False, indent=2)

    print(f"✅ Cleaned data saved to: {output_file}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"\n🚨 Unhandled exception: {e}")
        import traceback
        traceback.print_exc()
