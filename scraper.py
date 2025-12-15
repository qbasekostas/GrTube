from playwright.sync_api import sync_playwright
import time
import os
import json
import re

BASE_URL = "https://greektube.pro"
START_URLS = [
    "https://greektube.pro/movies?order=created_at%3Adesc",
    "https://greektube.pro/movies?order=created_at%3Adesc&page=2"
]
OUTPUT_FILE = "GrTube.m3u"

def get_final_video_url(page, url):
    """
    Πηγαίνει σε ένα εξωτερικό URL (π.χ. upns.pro), διαχειρίζεται τα κλικ
    και επιστρέφει το τελικό link βίντεο.
    """
    try:
        # Μπλοκάρουμε τα popup (διαφημίσεις)
        page.on("popup", lambda popup: popup.close())
        
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        
        # Κάνουμε μερικά κλικ για να ξυπνήσει ο player
        try:
            page.click('body', timeout=1000)
            page.click('video', timeout=1000)
        except: pass
        
        time.sleep(3) # Λίγη αναμονή για το network
        
        # Ψάχνουμε στον κώδικα για το τελικό link
        content = page.content().replace(r'\/', '/')
        
        # Regex για .mp4, .m3u8, .txt
        vid_match = re.search(r'(https?://[^"\'<>\s]+\.(?:mp4|m3u8|txt)(?:[^"\'<>\s]*)?)', content)
        sub_match = re.search(r'(https?://[^"\'<>\s]+\.(?:vtt|srt)(?:[^"\'<>\s]*)?)', content)
        
        if vid_match:
            # Φίλτρο για σκουπίδια
            v = vid_match.group(1)
            if not any(x in v for x in ["google", "facebook", "w3.org"]):
                return v, sub_match.group(1) if sub_match else None
                
    except Exception as e:
        print(f"    Error in external player: {e}")
        
    return None, None

def process_movie(page, movie_url):
    print(f"Processing: {movie_url}")
    try:
        page.goto(movie_url, wait_until="domcontentloaded", timeout=20000)
        
        # Έλεγχος Cloudflare
        if "Just a moment" in page.title():
            print("    ⚠️ Cloudflare detected. Waiting...")
            time.sleep(5)
        
        # --- THE MAGIC TRICK ---
        # Αντί να ψάχνουμε το HTML, ζητάμε από τον browser τα δεδομένα!
        # Αυτό επιστρέφει το JSON αντικείμενο έτοιμο.
        bootstrap_data = page.evaluate("() => window.bootstrapData")
        
        if not bootstrap_data:
            print("    ❌ No data found.")
            return None

        # 1. Παίρνουμε τον τίτλο από τα δεδομένα (πιο αξιόπιστο)
        # Η δομή είναι loaders -> titlePage -> title -> name
        try:
            title = bootstrap_data['loaders']['titlePage']['title']['name']
        except:
            title = "Unknown Movie"

        video_src = None
        label = "Stream"
        
        loaders = bootstrap_data.get('loaders', {})
        
        # ΣΤΡΑΤΗΓΙΚΗ 1: Υπάρχει ήδη βίντεο στη σελίδα (π.χ. Dream House);
        try:
            video_data = loaders.get('watchPage', {}).get('video', {})
            if video_data and 'src' in video_data:
                video_src = video_data['src']
                # print("    -> Found direct video data.")
        except: pass

        # ΣΤΡΑΤΗΓΙΚΗ 2: Ψάχνουμε στη λίστα βίντεο (Footer)
        if not video_src:
            videos = loaders.get('titlePage', {}).get('videos', [])
            for vid in videos:
                # Φίλτρο: Όχι trailers
                is_trailer = 'trailer' in vid.get('name', '').lower() or vid.get('category') == 'trailer'
                if not is_trailer:
                    # Αν είναι embed link
                    if vid.get('src'):
                        video_src = vid.get('src')
                        label = vid.get('name', 'Stream')
                        # print(f"    -> Found video in list: {label}")
                        break
                    # Αν είναι εσωτερικό ID (π.χ. /watch/12345)
                    elif vid.get('id'):
                        video_src = f"{BASE_URL}/watch/{vid['id']}"
                        break
        
        # ΣΤΡΑΤΗΓΙΚΗ 3: Primary Video (Header Button)
        if not video_src:
            primary = loaders.get('titlePage', {}).get('title', {}).get('primary_video')
            if primary and primary.get('category') == 'full':
                if primary.get('src'): video_src = primary.get('src')
                elif primary.get('id'): video_src = f"{BASE_URL}/watch/{primary['id']}"

        # --- ΕΠΕΞΕΡΓΑΣΙΑ LINK ---
        if video_src:
            # Καθαρισμός
            video_src = video_src.replace(r'\/', '/')
            
            # Αν είναι link του greektube (/watch/...), πρέπει να πάμε εκεί
            if video_src.startswith(BASE_URL) or video_src.startswith('/'):
                if video_src.startswith('/'): video_src = BASE_URL + video_src
                # Καλούμε αναδρομικά τον εαυτό μας (αλλά τώρα θα πιάσει τη Στρατηγική 1)
                # Ή πιο απλά, πηγαίνουμε εκεί με τον browser.
                return get_final_video_url(page, video_src)[0], None, video_src, title

            # Αν είναι εξωτερικό link (upns.pro)
            elif "http" in video_src:
                final_url, sub_url = get_final_video_url(page, video_src)
                if final_url:
                    print(f"    + Found: {final_url}")
                    return {
                        'title': title,
                        'url': final_url,
                        'subtitle': sub_url,
                        'referer': video_src
                    }
        else:
            print("    - No video source found in data.")

    except Exception as e:
        print(f"    Error: {e}")
        
    return None

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
        for s in new_streams:
            clean_title = s['title'].replace(",", " -").replace("\n", " ")
            f.write(f"#EXTINF:-1 group-title=\"Movies\",{clean_title}\n")
            f.write(f"#EXTVLCOPT:http-referrer={s['referer']}/\n")
            f.write(f"#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Firefox/115.0\n")
            if s['subtitle']: f.write(f"#EXTVLCOPT:sub-file={s['subtitle']}\n")
            f.write(f"{s['url']}\n")
        for entry in unique_old_entries:
            for line in entry['raw_lines']: f.write(f"{line}\n")
    print(f"✅ Playlist updated! Total: {len(new_streams) + len(unique_old_entries)} movies.")

def main():
    with sync_playwright() as p:
        # Χρησιμοποιούμε Firefox γιατί περνάει καλύτερα το Cloudflare
        browser = p.firefox.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
            viewport={'width': 1920, 'height': 1080}
        )
        page = context.new_page()

        # Stealth: Κρύβουμε ότι είμαστε ρομπότ
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        all_movie_urls = []
        print("🔵 Phase 1: Collecting URLs...")
        for list_url in START_URLS:
            try:
                page.goto(list_url, wait_until="domcontentloaded")
                time.sleep(3) # Cloudflare wait
                
                # Scroll to bottom
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)
                
                # Get links
                links = page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('a[href*="/titles/"]')).map(a => a.href);
                }""")
                # Filter duplicates and irrelevant links
                for link in links:
                    if link not in all_movie_urls:
                        all_movie_urls.append(link)
                        
            except Exception as e:
                print(f"Error loading list: {e
