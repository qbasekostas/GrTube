from playwright.sync_api import sync_playwright
import time
import os
import json
import re
import random

BASE_URL = "https://greektube.pro"
START_URLS = [
    "https://greektube.pro/movies?order=created_at%3Adesc",
    "https://greektube.pro/movies?order=created_at%3Adesc&page=2"
]
OUTPUT_FILE = "GrTube.m3u"

# --- NETWORK BLOCKER (ΓΙΑ ΤΑΧΥΤΗΤΑ) ---
def intercept_route(route):
    """Μπλοκάρει εικόνες, fonts και διαφημίσεις για να μην κολλάει η σελίδα"""
    if route.request.resource_type in ["image", "stylesheet", "font", "media"]:
        route.abort()
    else:
        route.continue_()

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
        
        # Πηγαίνουμε με timeout 20s
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        
        # Human Interaction Simulation
        try:
            time.sleep(1)
            page.mouse.move(random.randint(100, 500), random.randint(100, 500))
            page.mouse.click(200, 200)
            time.sleep(0.5)
            # Προσπάθεια κλικ σε στοιχεία βίντεο
            page.evaluate("""() => {
                const buttons = document.querySelectorAll('video, .play-button, .jw-display-icon');
                if(buttons.length > 0) buttons[0].click();
            }""")
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
        page.goto(movie_url, wait_until="domcontentloaded", timeout=30000)
        
        # Ανίχνευση Cloudflare
        if "Just a moment" in page.title():
            print("    ⚠️ Cloudflare detected. Waiting...")
            time.sleep(5)
            # Κίνηση ποντικιού
            page.mouse.move(100, 100)
            time.sleep(1)
        
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
        
        # Priority Check Logic
        try:
            video_data = loaders.get('watchPage', {}).get('video', {})
            if video_data and 'src' in video_data:
                video_src = video_data['src']
        except: pass

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
                return {'title': title, 'url': final_url, 'subtitle': sub_url, 'referer': referer}
        else:
            print("    - No video source found.")

    except Exception as e:
        print(f"    Error: {e}")
    return None

def main():
    with sync_playwright() as p:
        # ΣΗΜΑΝΤΙΚΟ: headless=False για να νομίζει ότι υπάρχει οθόνη (μέσω Xvfb)
        browser = p.firefox.launch(headless=False) 
        
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
            viewport={'width': 1920, 'height': 1080},
            ignore_https_errors=True
        )
        page = context.new_page()
        
        # ΜΠΛΟΚΑΡΙΣΜΑ ΦΟΡΤΩΣΗΣ ΕΙΚΟΝΩΝ (Ταχύτητα + Λιγότερα Timeouts)
        page.route("**/*", intercept_route)

        # Stealth
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        all_movie_urls = []
        print("🔵 Phase 1: Collecting URLs (Headful Xvfb Mode)...")
        
        for list_url in START_URLS:
            try:
                page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
                
                # Human-like wait
                time.sleep(3)
                if "Just a moment" in page.title():
                    print("    ⚠️ Cloudflare on list. Moving mouse...")
                    page.mouse.move(200, 200)
                    time.sleep(5)

                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)
                
                links = page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('a[href*="/titles/"]')).map(a => a.href);
                }""")
                
                for link in links:
                    if link not in all_movie_urls:
                        all_movie_urls.append(link)
                
                print(f"    Collected {len(links)} from page.")
                        
            except Exception as e:
                print(f"    Error loading list: {e}")

        print(f"🟢 Found {len(all_movie_urls)} total movies.")
        
        all_streams = []
        for i, movie_url in enumerate(all_movie_urls):
            result = process_movie(page, movie_url)
            if result:
                all_streams.append(result)
            
            # Restart Context (Soft Restart) κάθε 20 ταινίες
            if (i + 1) % 20 == 0:
                print("🔄 Restarting Context...")
                context.close()
                context = browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0')
                page = context.new_page()
                page.route("**/*", intercept_route)
                page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        browser.close()

        if all_streams:
            smart_save_m3u(all_streams)
        else:
            print("❌ No streams found. (Check GitHub Artifacts/Logs)")
            # Δημιουργία κενού αρχείου για να μην σκάσει το git
            with open(OUTPUT_FILE, "w") as f: f.write("")

if __name__ == "__main__":
    main()
