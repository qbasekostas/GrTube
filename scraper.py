from seleniumbase import SB
from bs4 import BeautifulSoup
import re
import time
import os
import math
import json

BASE_URL = "https://greektube.pro"
START_URLS = [
    "https://greektube.pro/movies?order=created_at%3Adesc",
    "https://greektube.pro/movies?order=created_at%3Adesc&page=2"
]
OUTPUT_FILE = "GrTube.m3u"
BATCH_SIZE = 5

def close_popups(sb, main_window):
    """Κλείνει τα παράθυρα διαφημίσεων"""
    try:
        if len(sb.driver.window_handles) > 1:
            for handle in sb.driver.window_handles:
                if handle != main_window:
                    sb.driver.switch_to.window(handle)
                    sb.driver.close()
            sb.driver.switch_to.window(main_window)
            return True
    except:
        try: sb.driver.switch_to.window(main_window)
        except: pass
    return False

def get_network_video(sb):
    """
    ΜΙΜΗΣΗ DEVTOOLS NETWORK TAB
    Ρωτάει τον browser τι αρχεία φόρτωσε.
    """
    try:
        # Script που επιστρέφει όλα τα URL που φορτώθηκαν (Resources)
        logs = sb.execute_script("""
            return window.performance.getEntriesByType("resource")
                .map(r => r.name)
                .filter(n => n.match(/\.(mp4|m3u8|txt)|master/));
        """)
        
        # Ελέγχουμε τα logs από το τέλος προς την αρχή (τα πιο πρόσφατα)
        for url in reversed(logs):
            # Αγνοούμε σκουπίδια
            if any(x in url for x in ["google", "facebook", "analytics", "svg", "png", "jpg", "vtt", "srt"]):
                continue
            
            # Αν βρούμε .txt, .mp4 ή .m3u8 που δεν είναι υπότιτλος
            if "master.txt" in url or ".mp4" in url or ".m3u8" in url:
                return url
                
    except Exception as e:
        print(f"    DevTools Error: {e}")
    return None

def get_stream_with_devtools(sb, watch_url):
    final_referer = watch_url
    video_url = None
    sub_url = None

    try:
        # 1. Πάμε στη σελίδα της ταινίας
        if sb.get_current_url() != watch_url:
            sb.uc_open_with_reconnect(watch_url, reconnect_time=3)
        
        main_win = sb.driver.current_window_handle

        # 2. Ψάχνουμε αν υπάρχει Iframe Player (π.χ. upns.pro)
        # Δεν μας νοιάζει το HTML, θέλουμε μόνο το SRC του iframe
        soup = BeautifulSoup(sb.get_page_source(), 'html.parser')
        
        # Βρίσκουμε το src του iframe ή το embed url από JS (πιο απλά τώρα)
        player_url = None
        
        # Check 1: JSON bootstrap (γρήγορος τρόπος να βρούμε το upns link)
        scripts = soup.find_all('script')
        for s in scripts:
            if s.string and 'upns.pro' in s.string:
                match = re.search(r'(https?://[^"\']*upns\.pro[^"\']*)', s.string)
                if match: 
                    player_url = match.group(1).replace(r'\/', '/')
                    break
        
        # Check 2: Iframe tag
        if not player_url:
            iframe = sb.find_element("iframe", timeout=2)
            if iframe:
                src = iframe.get_attribute("src")
                if "google" not in src:
                    player_url = src

        # 3. ΑΝ ΒΡΗΚΑΜΕ ΕΞΩΤΕΡΙΚΟ PLAYER -> ΠΑΜΕ ΕΚΕΙ!
        if player_url:
            # print(f"    -> Going to Player: {player_url}")
            sb.uc_open_with_reconnect(player_url, reconnect_time=3)
            final_referer = player_url
            main_win = sb.driver.current_window_handle
        
        # 4. ΚΛΙΚ & LOAD (Για να γεμίσει το Network Tab)
        # Κάνουμε κλικ για να ξεκινήσει το traffic
        time.sleep(1)
        try: sb.click("body", timeout=0.5)
        except: pass
        
        # Κλείνουμε διαφημίσεις
        if close_popups(sb, main_win):
            try: sb.click("body", timeout=0.5) # Ξανακλικ αν έκλεισε popup
            except: pass
            
        # Ψάχνουμε Play buttons
        click_targets = ["video", "#player", ".jw-display-icon", ".play-button"]
        for target in click_targets:
            try: 
                sb.click(target, timeout=0.5)
                break
            except: pass

        # ΠΕΡΙΜΕΝΟΥΜΕ ΤΟ NETWORK (4 δευτερόλεπτα)
        time.sleep(4)
        
        # 5. ΔΙΑΒΑΖΟΥΜΕ ΤΟ "DEVTOOLS" (Performance API)
        video_url = get_network_video(sb)
        
        # Αν δεν βρέθηκε, ίσως θέλει κι άλλο κλικ;
        if not video_url:
            # print("    -> Retrying click...")
            try: sb.click("video", timeout=1); time.sleep(3)
            except: pass
            video_url = get_network_video(sb)

        # Υπότιτλοι (από source code, πιο εύκολο)
        sub_match = re.search(r'(https?://[^"\'<>\s]+\.(?:vtt|srt)(?:[^"\'<>\s]*)?)', sb.get_page_source().replace(r'\/', '/'))
        if sub_match: sub_url = sub_match.group(1)

    except Exception as e:
        print(f"    Error: {e}")
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
                try: sb.wait_for_element_present("a[href*='/titles/']", timeout=15)
                except: pass

                soup = BeautifulSoup(sb.get_page_source(), 'html.parser')
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if '/titles/' in href and 'page=' not in href:
                        full_link = href if href.startswith('http') else BASE_URL + href
                        if full_link not in movie_links: movie_links.append(full_link)
            except: pass
    print(f"🟢 Found {len(movie_links)} movies.")
    return movie_links

def process_batch(links):
    batch_streams = []
    with SB(uc=True, test=True, headless=False, xvfb=True, block_images=False) as sb:
        for url in links:
            print(f"   Processing: {url}")
            try:
                sb.uc_open_with_reconnect(url, reconnect_time=4)
                if "Just a moment" in sb.get_title():
                    try: sb.uc_gui_click_captcha(); sb.sleep(3)
                    except: pass
                
                # Check popups
                handle_window = sb.driver.current_window_handle
                close_popups(sb, handle_window)

                soup = BeautifulSoup(sb.get_page_source(), 'html.parser')
                title_tag = soup.find('h1')
                if not title_tag: continue
                title = title_tag.text.strip()
                
                watch_url = None
                label = "Stream"
                
                # 1. Search Buttons
                for a in soup.find_all('a', href=True):
                    if '/watch/' in a['href']:
                        txt = a.text.strip().lower()
                        if any(x in txt for x in ["trailer", "teaser", "clip"]): continue
                        watch_url = a['href'] if a['href'].startswith('http') else BASE_URL + a['href']
                        break 
                
                # 2. Header Button
                if not watch_url:
                    for a in soup.find_all('a', href=True):
                        if '/watch/' in a['href'] and ('δείτε' in a.text.lower() or 'play' in a.text.lower()):
                            watch_url = a['href'] if a['href'].startswith('http') else BASE_URL + a['href']
                            break

                # 3. Execution (Network Sniffer Mode)
                target = watch_url if watch_url else url # Αν δεν βρει κουμπί, δοκιμάζει auto-play
                
                v, s, r = get_stream_with_devtools(sb, target)
                
                if v:
                    v = v.split('"')[0].split("'")[0]
                    print(f"     + Found (Net): {v}")
                    batch_streams.append({'title': title, 'url': v, 'subtitle': s, 'referer': r})
                else:
                    print("     - No stream found in Network Logs.")

            except Exception as e: print(f"    Error: {e}")
            
    return batch_streams

def main():
    all_links = get_all_movie_urls()
    if not all_links: return
    
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
    else: print("❌ No streams.")

if __name__ == "__main__":
    main()
