from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import time
import os
import json
import re

BASE_URL = "https://greektube.pro"
START_URLS = [
    "https://greektube.pro/movies?order=created_at%3Adesc",
    "https://greektube.pro/movies?order=created_at%3Adesc&page=2"
]
# Ορίστηκε το όνομα που ήθελες
OUTPUT_FILE = "GrTube.m3u"

def smart_save_m3u(new_streams):
    old_entries = []
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            current_entry = {}
            for line in lines:
                line = line.strip()
                if line.startswith("#EXTINF"):
                    if current_entry: old_entries.append(current_entry)
                    title = line.split(",", 1)[1] if "," in line else "Unknown"
                    current_entry = {'title': title, 'raw_lines': [line]}
                elif line.startswith("#EXTVLCOPT") or line.startswith("http"):
                    if current_entry: current_entry['raw_lines'].append(line)
            if current_entry: old_entries.append(current_entry)
            print(f"📂 Loaded {len(old_entries)} existing movies.")
        except: pass

    new_titles = [s['title'] for s in new_streams]
    unique_old_entries = [entry for entry in old_entries if entry['title'] not in new_titles]
    print(f"♻️  Keeping {len(unique_old_entries)} older movies.")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        # New
        for s in new_streams:
            clean_title = s['title'].replace(",", " -").replace("\n", " ")
            f.write(f"#EXTINF:-1 group-title=\"Movies\",{clean_title}\n")
            f.write(f"#EXTVLCOPT:http-referrer={s['referer']}/\n")
            f.write(f"#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36\n")
            if s['subtitle']: f.write(f"#EXTVLCOPT:sub-file={s['subtitle']}\n")
            f.write(f"{s['url']}\n")
        # Old
        for entry in unique_old_entries:
            for line in entry['raw_lines']: f.write(f"{line}\n")
    print(f"✅ Playlist updated: {OUTPUT_FILE} (Total: {len(new_streams) + len(unique_old_entries)})")

def get_final_video_url(page, url):
    try:
        def handle_popup(popup):
            try: popup.close()
            except: pass
        
        page.on("popup", handle_popup)
        
        # Αυξημένο timeout για τα popups
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        
        try:
            time.sleep(1)
            page.mouse.click(200, 200)
            time.sleep(0.5)
            if page.locator("video").count() > 0:
                page.locator("video").first.click(timeout=1000)
        except: pass
        
        time.sleep(3) 
        content = page.content().replace(r'\/', '/')
        
        vid_match = re.search(r'(https?://[^"\'<>\s]+\.(?:mp4|m3u8|txt)(?:[^"\'<>\s]*)?)', content)
        sub_match = re.search(r'(https?://[^"\'<>\s]+\.(?:vtt|srt)(?:[^"\'<>\s]*)?)', content)
        
        page.remove_listener("popup", handle_popup)
        
        if vid_match:
            v = vid_match.group(1)
            if not any(x in v for x in ["google", "facebook", "w3.org"]):
                return v, sub_match.group(1) if sub_match else None
                
    except Exception as e:
        print(f"    Error in external player: {e}")
    return None, None

def process_movie(page, movie_url):
    print(f"Processing: {movie_url}")
    try:
        # Αυξημένο timeout για τη σελίδα ταινίας
        page.goto(movie_url, wait_until="domcontentloaded", timeout=40000)
        
        if "Just a moment" in page.title():
            print("    ⚠️ Cloudflare detected. Waiting...")
            time.sleep(6)
        
        # Τραβάμε τα JSON data
        bootstrap_data = page.evaluate("() => window.bootstrapData")
        
        if not bootstrap_data:
            print("    ❌ No data found.")
            return None

        try:
            title = bootstrap_data['loaders']['titlePage']['title']['name']
        except:
            title = "Unknown Movie"

        video_src = None
        loaders = bootstrap_data.get('loaders', {})
        
        # 1. Direct Video
        try:
            video_data = loaders.get('watchPage', {}).get('video', {})
            if video_data and 'src' in video_data:
                video_src = video_data['src']
        except: pass

        # 2. Video List
        if not video_src:
            videos = loaders.get('titlePage', {}).get('videos', [])
            for vid in videos:
                is_trailer = 'trailer' in vid.get('name', '').lower() or vid.get('category') == 'trailer' or 'teaser' in vid.get('name', '').lower()
                if not is_trailer:
                    if vid.get('src'):
                        video_src = vid.get('src')
                        break
                    elif vid.get('id'):
                        video_src = f"{BASE_URL}/watch/{vid['id']}"
                        break
        
        # 3. Primary Video
        if not video_src:
            primary = loaders.get('titlePage', {}).get('title', {}).get('primary_video')
            if primary and primary.get('category') == 'full':
                if primary.get('src'): video_src = primary.get('src')
                elif primary.get('id'): video_src = f"{BASE_URL}/watch/{primary['id']}"

        if video_src:
            video_src = video_src.replace(r'\/', '/')
            if video_src.startswith(BASE_URL) or video_src.startswith('/'):
                if video_src.startswith('/'): video_src = BASE_URL + video_src
                final_url, sub_url = get_final_video_url(page, video_src)
                referer = video_src
            else:
                final_url, sub_url = get_final_video_url(page, video_src)
                referer = video_src

            if final_url:
                final_url = final_url.split('"')[0].split("'")[0]
                print(f"    + Found: {final_url}")
                return {
                    'title': title,
                    'url': final_url,
                    'subtitle': sub_url,
                    'referer': referer
                }
        else:
            print("    - No video source found in data.")

    except Exception as e:
        print(f"    Error: {e}")
    return None

def main():
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
            viewport={'width': 1920, 'height': 1080},
            ignore_https_errors=True
        )
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        all_movie_urls = []
        print("🔵 Phase 1: Collecting URLs...")
        
        for list_url in START_URLS:
            try:
                # Χρησιμοποιούμε wait_until='commit' (πιο γρήγορο) και μετά περιμένουμε το element
                page.goto(list_url, wait_until="commit", timeout=60000)
                
                # Περιμένουμε συγκεκριμένα να εμφανιστούν ταινίες
                try:
                    page.wait_for_selector('a[href*="/titles/"]', timeout=30000)
                except:
                    print(f"    ⚠️ Timeout waiting for selectors on {list_url}")
                    # Αν αποτύχει, ίσως είναι Cloudflare, κάνουμε ένα screenshot/dump αν χρειαστεί ή απλά συνεχίζουμε
                
                # Scroll
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)
                
                links = page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('a[href*="/titles/"]')).map(a => a.href);
                }""")
                
                for link in links:
                    if link not in all_movie_urls:
                        all_movie_urls.append(link)
                        
            except Exception as e:
                print(f"Error loading list {list_url}: {e}")

        print(f"🟢 Found {len(all_movie_urls)} movies.")
        
        all_streams = []
        for i, movie_url in enumerate(all_movie_urls):
            
            result = process_movie(page, movie_url)
            if result:
                all_streams.append(result)
            
            if (i + 1) % 15 == 0:
                print("🔄 Restarting Browser (Memory Cleanup)...")
                context.close()
                browser.close()
                browser = p.firefox.launch(headless=True)
                context = browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0')
                page = context.new_page()
                page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        browser.close()

        if all_streams:
            smart_save_m3u(all_streams)
        else:
            print("❌ No streams found.")

if __name__ == "__main__":
    main()
