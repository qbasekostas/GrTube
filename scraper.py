from seleniumbase import SB
from bs4 import BeautifulSoup
import re
import time
import os
import math
import json

# --- NEW DOMAIN & URLS ---
BASE_URL = "https://greeksubsmovies.net"
START_URLS = [
    "https://greeksubsmovies.net/?sort=recent&filter=movie"
    #"https://greeksubsmovies.net/?sort=recent&filter=movie&page=2"
]
OUTPUT_FILE = "GrTube.m3u"
BATCH_SIZE = 5

def close_popups(sb, main_window):
    try:
        if len(sb.driver.window_handles) > 1:
            for handle in sb.driver.window_handles:
                if handle != main_window:
                    sb.driver.switch_to.window(handle)
                    sb.driver.close()
            sb.driver.switch_to.window(main_window)
    except: pass

def get_network_video(sb):
    """Network Sniffer (DevTools)"""
    try:
        logs = sb.execute_script("""
            return window.performance.getEntriesByType("resource")
                .map(r => r.name)
                .filter(n => n.match(/\.(mp4|m3u8|txt)|master/));
        """)
        for url in reversed(logs):
            if any(ext in url for ext in ['.mp4', '.m3u8', '.txt']) and not any(bad in url for bad in ['google', 'facebook', 'analytics', 'svg', 'jpg', 'png']):
                return url
    except: pass
    return None

def get_stream_with_devtools(sb, watch_url):
    final_referer = watch_url
    video_url = None
    sub_url = None

    try:
        if sb.get_current_url() != watch_url:
            sb.uc_open_with_reconnect(watch_url, reconnect_time=3)
        
        main_win = sb.driver.current_window_handle
        time.sleep(2)
        
        source = sb.get_page_source()

        # --- 1. ΥΠΟΤΙΤΛΟΙ ---
        # Τώρα τους παίρνουμε κατευθείαν από το tag <track>
        sub_match = re.search(r'<track[^>]*src=["\']([^"\']+\.(?:vtt|srt))["\']', source)
        if sub_match:
            sub_url = sub_match.group(1)
            if sub_url.startswith('/'): sub_url = BASE_URL + sub_url

        # --- 2. API CRACKER (Το νέο κόλπο) ---
        target_url = None
        tok_match = re.search(r'const _tok\s*=\s*["\']([^"\']+)["\']', source)
        vid_match = re.search(r'const _vid\s*=\s*(\d+)', source)
        
        if tok_match and vid_match:
            tok = tok_match.group(1)
            vid = vid_match.group(1)
            api_url = f"{BASE_URL}/api/video-src.php?t={tok}&v={vid}"
            
            # Κάνουμε fetch το API σαν να ήμασταν το site
            try:
                json_str = sb.execute_script(f"return fetch('{api_url}').then(r => r.text());")
                data = json.loads(json_str)
                if data and 'src' in data:
                    target_url = data['src']
                    # print(f"    [API Cracked] Got URL: {target_url}")
            except Exception as api_err:
                # print(f"    API Error: {api_err}")
                pass

        # --- 3. Iframe Fallback ---
        if not target_url:
            try:
                iframes = sb.driver.find_elements("css selector", "iframe")
                for frame in iframes:
                    src = frame.get_attribute("src")
                    if src and "google" not in src:
                        target_url = src
                        break
            except: pass

        if not target_url:
            return None, sub_url, final_referer

        # Αν το API μας έδωσε κατευθείαν .mp4 (Εύκολη περίπτωση)
        if re.search(r'\.(mp4|m3u8|txt)', target_url):
            return target_url, sub_url, final_referer

        # --- 4. Πάμε στον Player (upns.pro κλπ) για Sniffing ---
        if not target_url.startswith("http"): target_url = BASE_URL + target_url
        if target_url != watch_url:
            sb.uc_open_with_reconnect(target_url, reconnect_time=3)
            final_referer = target_url
            main_win = sb.driver.current_window_handle

        time.sleep(1)
        close_popups(sb, main_win)
        
        click_targets = [
            "svg[data-testid='MediaPlayIcon']", 
            "button:has(svg[data-testid='MediaPlayIcon'])",
            "button.rounded-full",
            "video", 
            "#player", 
            ".jw-display-icon", 
            ".play-button",
            "div[id*='player']",
            "body"
        ]
        
        for target in click_targets:
            try: 
                if sb.is_element_visible(target):
                    sb.click(target, timeout=0.5)
                    close_popups(sb, main_win)
                    sb.sleep(0.5)
            except: pass

        time.sleep(4) 
        video_url = get_network_video(sb)

        # Fallback αν δεν το έπιασε το Network
        if not video_url:
            src = sb.get_page_source().replace(r'\/', '/')
            match = re.search(r'(https?://[^"\'<>\s]+\.(?:mp4|m3u8|txt)(?:[^"\'<>\s]*)?)', src)
            if match and "google" not in match.group(1):
                video_url = match.group(1)

    except Exception as e:
        try: sb.driver.switch_to.window(sb.driver.window_handles[0])
        except: pass

    return video_url, sub_url, final_referer

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
        for s in new_streams:
            clean_title = s['title'].replace(",", " -").replace("\n", " ")
            f.write(f"#EXTINF:-1 group-title=\"Movies\",{clean_title}\n")
            f.write(f"#EXTVLCOPT:http-referrer={s['referer']}/\n")
            f.write(f"#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36\n")
            if s['subtitle']: f.write(f"#EXTVLCOPT:sub-file={s['subtitle']}\n")
            f.write(f"{s['url']}\n")
        for entry in unique_old_entries:
            for line in entry['raw_lines']: f.write(f"{line}\n")
    print(f"✅ Playlist updated! Total: {len(new_streams) + len(unique_old_entries)} movies.")

def get_all_movie_urls():
    movie_links = []
    print("🔵 Phase 1: Collecting URLs (.net API version)...")
    with SB(uc=True, test=True, headless=False, xvfb=True, block_images=False) as sb:
        for list_url in START_URLS:
            try:
                sb.uc_open_with_reconnect(list_url, reconnect_time=5)
                if "Just a moment" in sb.get_title():
                    sb.uc_gui_click_captcha(); sb.sleep(3)
                
                sb.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                sb.sleep(2)
                
                # Check for the NEW url structure (title.php)
                try: sb.wait_for_element_present("a[href*='/title.php?id=']", timeout=15)
                except: pass

                soup = BeautifulSoup(sb.get_page_source(), 'html.parser')
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if '/title.php?id=' in href:
                        full_link = href if href.startswith('http') else BASE_URL + href
                        if full_link not in movie_links: movie_links.append(full_link)
            except: pass
    print(f"🟢 Found {len(movie_links)} movies.")
    return movie_links

def process_batch(links):
    batch_streams = []
    with SB(uc=True, test=True, headless=False, xvfb=True, block_images=False) as sb:
        sb.driver.set_page_load_timeout(60)
        sb.driver.set_script_timeout(30)

        for url in links:
            print(f"   Processing: {url}")
            try:
                sb.uc_open_with_reconnect(url, reconnect_time=4)
                if "Just a moment" in sb.get_title():
                    try: sb.uc_gui_click_captcha(); sb.sleep(3)
                    except: pass
                
                handle_window = sb.driver.current_window_handle
                close_popups(sb, handle_window)

                soup = BeautifulSoup(sb.get_page_source(), 'html.parser')
                
                # Title Extraction 
                title = "Unknown"
                for div in soup.find_all('div', class_='card-title'):
                    title = div.text.strip()
                    break
                if title == "Unknown":
                    h1 = soup.find('h1')
                    if h1: title = h1.text.strip()
                if title == "Unknown" and soup.title:
                    title = soup.title.text.strip()
                
                watch_url = None
                label = "Stream"
                
                # Search Buttons (NEW FORMAT: /watch.php?)
                for a in soup.find_all('a', href=True):
                    if '/watch.php?' in a['href']:
                        txt = a.text.strip().lower()
                        if any(x in txt for x in ["trailer", "teaser", "clip"]): continue
                        
                        watch_url = a['href'] if a['href'].startswith('http') else BASE_URL + a['href']
                        
                        # Βρίσκουμε την ποιότητα (1080p) που είναι δίπλα στο κουμπί
                        parent = a.find_parent(class_=['video-row', 'feature-card'])
                        if parent:
                            strong = parent.find('strong')
                            if strong: label = strong.text.strip()
                        if label == "Stream": label = txt.replace("▶", "").strip() or "Stream"
                        
                        break 
                
                target = watch_url if watch_url else url 
                v, s, r = get_stream_with_devtools(sb, target)
                
                if v:
                    print(f"     + Found [{label}]: {v}")
                    batch_streams.append({'title': f"{title} [{label}]", 'url': v, 'subtitle': s, 'referer': r})
                else:
                    print("     - No stream found.")

            except Exception as e: print(f"    Skipped (Timeout/Error): {e}")
            
    return batch_streams

def main():
    all_links = get_all_movie_urls()
    if not all_links: 
        if not os.path.exists(OUTPUT_FILE):
             with open(OUTPUT_FILE, "w") as f: f.write("")
        return
    
    total_streams = []
    num_batches = math.ceil(len(all_links) / BATCH_SIZE)
    
    for i in range(num_batches):
        print(f"🟠 Batch {i+1}/{num_batches}...")
        batch = all_links[i*BATCH_SIZE : (i+1)*BATCH_SIZE]
        try:
            res = process_batch(batch)
            total_streams.extend(res)
        except: pass
        time.sleep(2)
        
    if total_streams: smart_save_m3u(total_streams)
    else: 
        print("❌ No streams.")
        if not os.path.exists(OUTPUT_FILE):
             with open(OUTPUT_FILE, "w") as f: f.write("")

if __name__ == "__main__":
    main()
