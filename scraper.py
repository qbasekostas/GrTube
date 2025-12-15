from seleniumbase import SB
from bs4 import BeautifulSoup
import re
import time
import os
import json
import math

BASE_URL = "https://greektube.pro"
START_URLS = [
    "https://greektube.pro/movies?order=created_at%3Adesc",
    "https://greektube.pro/movies?order=created_at%3Adesc&page=2"
]
OUTPUT_FILE = "GrTube.m3u"
BATCH_SIZE = 6 # Μικρό batch για ασφάλεια

# --- NETWORK SNIFFER HELPER ---
def sniff_network_logs(sb):
    """
    Μιμείται το Network Tab του DevTools.
    Ρωτάει τον browser τι αρχεία έχει φορτώσει.
    """
    try:
        # 1. Έλεγχος για το ACTIVE Video Source (αν ο player έχει φορτώσει)
        video_tag_src = sb.execute_script("""
            var v = document.querySelector('video');
            return v ? v.src : null;
        """)
        if video_tag_src and "blob:" not in video_tag_src:
            return video_tag_src

        # 2. Έλεγχος του Network Traffic (Performance API)
        # Αυτό βλέπει ό,τι βλέπεις κι εσύ στο Network tab
        network_files = sb.execute_script("""
            return window.performance.getEntriesByType("resource")
                .map(x => x.name)
                .filter(x => x.includes('.txt') || x.includes('.mp4') || x.includes('.m3u8') || x.includes('master'));
        """)
        
        for url in network_files:
            # Φίλτρο για να μην πάρουμε σκουπίδια
            if any(ext in url for ext in ['.mp4', '.m3u8', '.txt']) and not any(bad in url for bad in ['google', 'facebook', 'analytics', 'svg', 'jpg']):
                return url

    except Exception as e:
        print(f"    Sniffer Error: {e}")
    return None

def extract_from_bootstrap_json(soup):
    try:
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string and 'window.bootstrapData' in script.string:
                js_content = script.string.strip()
                if "window.bootstrapData =" in js_content:
                    json_str = js_content.split("window.bootstrapData =")[1]
                    if json_str.strip().endswith(";"): json_str = json_str.strip()[:-1]
                    try:
                        data = json.loads(json_str)
                        loaders = data.get('loaders', {})
                        
                        # WatchPage
                        video_data = loaders.get('watchPage', {}).get('video', {})
                        if video_data and 'src' in video_data:
                            return video_data['src'].replace(r'\/', '/')
                        
                        # Primary
                        title_page = loaders.get('titlePage', {}).get('title', {})
                        primary = title_page.get('primary_video')
                        if primary and primary.get('category') == 'full':
                             vid_id = primary.get('id')
                             if vid_id: return f"{BASE_URL}/watch/{vid_id}"

                        # List
                        videos_list = loaders.get('titlePage', {}).get('videos', [])
                        for vid in videos_list:
                            if vid.get('category') == 'full' or (vid.get('type') == 'embed' and 'trailer' not in vid.get('name', '').lower()):
                                if vid.get('src'): return vid.get('src', '').replace(r'\/', '/')
                                if vid.get('id'): return f"{BASE_URL}/watch/{vid['id']}"
                    except: pass
    except: pass
    return None

def get_stream_and_sub(sb, watch_url):
    video_url = None
    sub_url = None
    final_referer = watch_url 
    
    try:
        if sb.get_current_url() != watch_url:
            sb.uc_open_with_reconnect(watch_url, reconnect_time=3)
        
        # --- 1. Bootstrap JSON Check ---
        source = sb.get_page_source()
        soup = BeautifulSoup(source, 'html.parser')
        bootstrap_link = extract_from_bootstrap_json(soup)
        
        target_url = bootstrap_link if bootstrap_link else watch_url
        if target_url.startswith("/"): target_url = BASE_URL + target_url
        
        # Αν βρήκαμε link άλλου site (upns.pro), πάμε εκεί
        if target_url != watch_url:
            # print(f"    -> Redirecting to Player: {target_url}")
            sb.uc_open_with_reconnect(target_url, reconnect_time=3)
            final_referer = target_url

        # --- 2. THE CLICK (Για να γεμίσει το Network Tab) ---
        sb.sleep(1)
        # Κλείνουμε τυχόν popups που πετάγονται
        if len(sb.driver.window_handles) > 1:
            sb.switch_to_window(0)
        
        # Κάνουμε κλικ για να ξεκινήσει η κίνηση δικτύου
        try: sb.click("body", timeout=0.5); sb.sleep(0.2)
        except: pass
        try: sb.click("video", timeout=0.5)
        except: pass
        try: sb.click("#player", timeout=0.5)
        except: pass
        try: sb.click(".jw-display-icon", timeout=0.5)
        except: pass
        
        # Περιμένουμε λίγο να γίνουν τα requests
        sb.sleep(4) 
        
        # --- 3. THE SNIFFER (Ψάχνουμε τα logs) ---
        video_url = sniff_network_logs(sb)
        
        # Αν δεν βρέθηκε με sniffer, δοκιμάζουμε το παλιό καλό regex στο source
        if not video_url:
            clean_source = sb.get_page_source().replace(r'\/', '/')
            vid_regex = r'(https?://[^"\'<>\s]+\.(?:mp4|m3u8|txt)(?:[^"\'<>\s]*)?)'
            match = re.search(vid_regex, clean_source)
            if match and not any(x in match.group(1) for x in ["google", "facebook"]):
                video_url = match.group(1)

        # Υπότιτλοι (συνήθως φαίνονται στο source)
        clean_source = sb.get_page_source().replace(r'\/', '/')
        sub_regex = r'(https?://[^"\'<>\s]+\.(?:vtt|srt)(?:[^"\'<>\s]*)?)'
        sub_match = re.search(sub_regex, clean_source)
        if sub_match: sub_url = sub_match.group(1)

    except Exception as e: 
        print(f"    ! Error: {e}")
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
    print("🔵 Phase 1: Collecting URLs...")
    with SB(uc=True, test=True, headless=False, xvfb=True, block_images=False) as sb:
        for list_url in START_URLS:
            try:
                sb.uc_open_with_reconnect(list_url, reconnect_time=5)
                if "Just a moment" in sb.get_title():
                    sb.uc_gui_click_captcha(); sb.sleep(3)
                
                sb.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                sb.sleep(2)
                try: sb.wait_for_element_present("a[href*='/titles/']", timeout=10)
                except: pass

                soup = BeautifulSoup(sb.get_page_source(), 'html.parser')
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if '/titles/' in href and 'page=' not in href:
                        full_link = href if href.startswith('http') else BASE_URL + href
                        if full_link not in movie_links: movie_links.append(full_link)
            except Exception as e: print(f"    Error: {e}")
    print(f"🟢 Found {len(movie_links)} total movies.")
    return movie_links

def process_batch(links_batch, batch_index, total_batches):
    batch_streams = []
    print(f"🟠 Batch {batch_index}/{total_batches} ({len(links_batch)} movies)...")
    
    with SB(uc=True, test=True, headless=False, xvfb=True, block_images=False) as sb:
        for i, m_url in enumerate(links_batch):
            try:
                if not sb.driver.service.is_connectable(): break
            except: break

            print(f"   Processing: {m_url}")
            try:
                sb.uc_open_with_reconnect(m_url, reconnect_time=4)
                if "Just a moment" in sb.get_title():
                    try: sb.uc_gui_click_captcha(); sb.sleep(5)
                    except: pass
                
                msource = sb.get_page_source()
                msoup = BeautifulSoup(msource, 'html.parser')
                title_tag = msoup.find('h1')
                if not title_tag:
                    print(f"     ❌ Page failed.")
                    continue
                title = title_tag.text.strip()
                
                watch_url = None
                label = "Stream"
                
                # 1. Search Buttons
                for a in msoup.find_all('a', href=True):
                    if '/watch/' in a['href']:
                        link_text = a.text.strip().lower()
                        if any(x in link_text for x in ["trailer", "teaser", "clip"]): continue
                        label = a.text.strip() or "Stream"
                        watch_url = a['href'] if a['href'].startswith('http') else BASE_URL + a['href']
                        break 
                
                # 2. Search Header Button (Loose)
                if not watch_url:
                    for a in msoup.find_all('a', href=True):
                        txt = a.get_text().lower()
                        if ('δείτε' in txt or 'start watching' in txt or 'play' in txt) and '/watch/' in a['href']:
                            watch_url = a['href'] if a['href'].startswith('http') else BASE_URL + a['href']
                            break

                # 3. Execution
                if watch_url:
                    stream_link, sub_link, dynamic_referer = get_stream_and_sub(sb, watch_url)
                else:
                    # Auto-Play Fallback
                    stream_link, sub_link, dynamic_referer = get_stream_and_sub(sb, m_url)

                if stream_link:
                    print(f"     + Found: {stream_link}")
                    stream_link = stream_link.split('"')[0].split("'")[0]
                    batch_streams.append({'title': f"{title} [{label}]", 'url': stream_link, 'subtitle': sub_link, 'referer': dynamic_referer})
                else:
                    print(f"     - No link found.")

            except Exception as e: print(f"     ! Error: {e}")
                
    return batch_streams

def main():
    all_movie_urls = get_all_movie_urls()
    if not all_movie_urls: return

    total_streams = []
    num_batches = math.ceil(len(all_movie_urls) / BATCH_SIZE)
    
    for i in range(num_batches):
        start_idx = i * BATCH_SIZE
        end_idx = start_idx + BATCH_SIZE
        batch_urls = all_movie_urls[start_idx:end_idx]
        
        try:
            results = process_batch(batch_urls, i+1, num_batches)
            total_streams.extend(results)
        except Exception as e:
            print(f"💥 Batch Error: {e}")
        
        if i < num_batches - 1: time.sleep(3)

    if total_streams: smart_save_m3u(total_streams)
    else: print("❌ No streams found.")

if __name__ == "__main__":
    main()
