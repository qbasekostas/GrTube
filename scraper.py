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
BATCH_SIZE = 20 

# --- HELPERS ---

def extract_final_link(source):
    video_url = None
    sub_url = None
    clean_source = source.replace(r'\/', '/')
    
    vid_regex = r'(https?://[^"\'<>\s]+\.(?:mp4|m3u8|txt)(?:[^"\'<>\s]*)?)'
    sub_regex = r'(https?://[^"\'<>\s]+\.(?:vtt|srt)(?:[^"\'<>\s]*)?)'
    
    vid_matches = re.findall(vid_regex, clean_source)
    for match in vid_matches:
        if not any(bad in match for bad in ["google", "facebook", "w3.org", "schema", "image.tmdb", "cloudflare", "jquery"]):
            video_url = match
            break
            
    sub_match = re.search(sub_regex, clean_source)
    if sub_match: sub_url = sub_match.group(1)
    return video_url, sub_url

def handle_popups_smartly(sb, main_window):
    """
    Ελέγχει τα νέα παράθυρα.
    Αν είναι Player (upns, embed) -> Τραβάει το link.
    Αν είναι Διαφήμιση -> Το κλείνει.
    """
    found_video = None
    found_sub = None
    found_referer = None

    try:
        # Αν υπάρχουν έξτρα παράθυρα
        if len(sb.driver.window_handles) > 1:
            for handle in sb.driver.window_handles:
                if handle != main_window:
                    sb.driver.switch_to.window(handle)
                    time.sleep(1.5) # Περιμένουμε να φορτώσει το URL
                    
                    current_url = sb.get_current_url()
                    # print(f"      Checking Popup: {current_url}")

                    # ΕΛΕΓΧΟΣ: Είναι αυτός ο Player;
                    if any(x in current_url for x in ["upns.pro", "eyetherapi", "embed", "greektube"]):
                        # ΝΑΙ! Είναι ο player. Τραβάμε τα δεδομένα.
                        # print("      ! IT IS A PLAYER POPUP !")
                        sb.sleep(3)
                        src = sb.get_page_source()
                        v, s = extract_final_link(src)
                        if v:
                            found_video = v
                            found_sub = s
                            found_referer = current_url
                    
                    # Κλείνουμε το παράθυρο (είτε ήταν ad είτε player που διαβάσαμε)
                    sb.driver.close()
            
            # Επιστροφή στη βάση
            sb.driver.switch_to.window(main_window)
    except Exception as e:
        print(f"      Popup Error: {e}")
        try: sb.driver.switch_to.window(main_window)
        except: pass

    return found_video, found_sub, found_referer

def extract_from_bootstrap_json(soup):
    try:
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string and 'window.bootstrapData' in script.string:
                js_content = script.string.strip()
                if "window.bootstrapData =" in js_content:
                    json_str = js_content.split("window.bootstrapData =")[1]
                    if json_str.strip().endswith(";"):
                        json_str = json_str.strip()[:-1]
                    try:
                        data = json.loads(json_str)
                        loaders = data.get('loaders', {})
                        
                        # Check WatchPage
                        video_data = loaders.get('watchPage', {}).get('video', {})
                        if video_data and 'src' in video_data:
                            return video_data['src'].replace(r'\/', '/')
                            
                        # Check Primary
                        title_page = loaders.get('titlePage', {}).get('title', {})
                        primary = title_page.get('primary_video')
                        if primary and primary.get('category') == 'full':
                             vid_id = primary.get('id')
                             if vid_id: return f"{BASE_URL}/watch/{vid_id}"

                        # Check List
                        videos_list = loaders.get('titlePage', {}).get('videos', [])
                        for vid in videos_list:
                             # Φίλτρο για CAM-TS, Full, κλπ
                            if vid.get('category') == 'full' or (vid.get('type') == 'embed' and 'trailer' not in vid.get('name', '').lower()):
                                if vid.get('src'): return vid.get('src', '').replace(r'\/', '/')
                                if vid.get('id'): return f"{BASE_URL}/watch/{vid['id']}"
                    except: pass
    except: pass
    return None

def get_stream_and_sub(sb, watch_url, is_watch_page=True):
    video_url = None
    sub_url = None
    final_referer = watch_url 
    
    try:
        if sb.get_current_url() != watch_url:
            sb.uc_open_with_reconnect(watch_url, reconnect_time=3)
        
        main_window_handle = sb.driver.current_window_handle 
        
        # 1. Έλεγχος Popups ΠΡΙΝ κάνουμε οτιδήποτε (μήπως άνοιξε ήδη;)
        v, s, r = handle_popups_smartly(sb, main_window_handle)
        if v: return v, s, r

        source = sb.get_page_source()
        soup = BeautifulSoup(source, 'html.parser')

        # 2. Bootstrap JSON
        bootstrap_link = extract_from_bootstrap_json(soup)
        if bootstrap_link:
            if not bootstrap_link.startswith("http"): bootstrap_link = BASE_URL + bootstrap_link
            
            # Αν το link είναι εξωτερικό (upns.pro), το ανοίγουμε
            sb.uc_open_with_reconnect(bootstrap_link, reconnect_time=3)
            final_referer = bootstrap_link
            
            # Κλικ για να ξυπνήσει
            sb.sleep(1)
            try: sb.click("body", timeout=0.5)
            except: pass
            
            # Έλεγχος Popups μετά το κλικ
            v, s, r = handle_popups_smartly(sb, main_window_handle)
            if v: return v, s, r
            
            sb.sleep(4)
            v, s = extract_final_link(sb.get_page_source())
            if v: return v, s, final_referer

        # 3. Iframe Fallback
        iframes = sb.find_elements("iframe")
        if iframes:
            for i in range(len(iframes)):
                try:
                    current_iframes = sb.find_elements("iframe")
                    if i >= len(current_iframes): break
                    frame = current_iframes[i]
                    frame_src = frame.get_attribute("src")
                    if not frame_src or "google" in frame_src: continue
                    
                    sb.switch_to_frame(frame)
                    try: sb.click("video", timeout=1) 
                    except: pass
                    sb.sleep(3)
                    
                    v, s = extract_final_link(sb.get_page_source())
                    if v:
                        if frame_src.startswith("http"): final_referer = frame_src
                        sb.switch_to_default_content()
                        return v, s, final_referer
                    sb.switch_to_default_content()
                except: sb.switch_to_default_content()

        v, s = extract_final_link(source)
        if v: return v, s, final_referer
                
    except Exception as e: 
        print(f"    ! Error getting stream: {e}")
        
    return None, None, final_referer

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
            
            # --- CRASH GUARD: Αν ο driver πέθανε, σταματάμε το batch ---
            try:
                if not sb.driver.service.is_connectable():
                    print("    🚨 Driver died! Stopping batch to restart.")
                    break
            except:
                break

            print(f"   Processing: {m_url}")
            try:
                sb.uc_open_with_reconnect(m_url, reconnect_time=4)
                
                if "Just a moment" in sb.get_title():
                    try: sb.uc_gui_click_captcha()
                    except: pass
                    sb.sleep(5)
                
                # Check popups immediately upon landing
                main_win = sb.driver.current_window_handle
                handle_popups_smartly(sb, main_win)

                msource = sb.get_page_source()
                msoup = BeautifulSoup(msource, 'html.parser')
                title_tag = msoup.find('h1')
                if not title_tag:
                    print(f"     ❌ Page failed.")
                    continue
                title = title_tag.text.strip()
                
                watch_url = None
                label = "Stream"
                
                # 1. Search Buttons (Excluding trailers)
                for a in msoup.find_all('a', href=True):
                    if '/watch/' in a['href']:
                        link_text = a.text.strip().lower()
                        if any(x in link_text for x in ["trailer", "teaser", "clip"]): continue
                        label = a.text.strip() or "Stream"
                        watch_url = a['href'] if a['href'].startswith('http') else BASE_URL + a['href']
                        break 
                
                # 2. Search Header Button (Stronger Logic)
                if not watch_url:
                    play_btn = msoup.find('a', string=re.compile(r'Δείτε τώρα|Start watching|Play', re.I))
                    if play_btn and 'href' in play_btn.attrs:
                        watch_url = play_btn['href'] if play_btn['href'].startswith('http') else BASE_URL + play_btn['href']
                        # print("     -> Using Header Button")

                # 3. Get Stream
                if watch_url:
                    stream_link, sub_link, dynamic_referer = get_stream_and_sub(sb, watch_url)
                else:
                    # 4. Fallback Auto-Play (Current Page)
                    # print("     -> Checking Auto-Play...")
                    stream_link, sub_link, dynamic_referer = get_stream_and_sub(sb, m_url, is_watch_page=False)

                if stream_link:
                    print(f"     + Found: {stream_link}")
                    stream_link = stream_link.split('"')[0].split("'")[0]
                    batch_streams.append({
                        'title': f"{title} [{label}]",
                        'url': stream_link,
                        'subtitle': sub_link,
                        'referer': dynamic_referer
                    })
                else:
                    print(f"     - No link found.")

            except Exception as e: 
                print(f"     ! Error: {e}")
                
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
        
        # Αν κρασάρει το batch, το script δεν θα σκάσει, απλά θα πάει στο επόμενο
        try:
            results = process_batch(batch_urls, i+1, num_batches)
            total_streams.extend(results)
        except Exception as e:
            print(f"💥 Critical Batch Error: {e}. Restarting browser...")
        
        if i < num_batches - 1:
            time.sleep(3)

    if total_streams: smart_save_m3u(total_streams)
    else: print("❌ No streams found.")

if __name__ == "__main__":
    main()
