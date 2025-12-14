from seleniumbase import SB
from bs4 import BeautifulSoup
import re
import time
import os
import json

BASE_URL = "https://greektube.pro"
START_URLS = [
    "https://greektube.pro/movies?order=created_at%3Adesc",
    "https://greektube.pro/movies?order=created_at%3Adesc&page=2"
]
OUTPUT_FILE = "GrTube.m3u"

# --- POPUP KILLER ---
def close_popups_and_return(sb, main_window):
    """Κλείνει τυχόν καρτέλες διαφημίσεων και επιστρέφει στην αρχική"""
    try:
        # Αν υπάρχουν περισσότερα από 1 παράθυρα
        if len(sb.driver.window_handles) > 1:
            # print("    🔨 Killing Popup/Ad...")
            # Κλείσε όλα τα παράθυρα εκτός από το πρώτο (main)
            for handle in sb.driver.window_handles:
                if handle != main_window:
                    sb.driver.switch_to.window(handle)
                    sb.driver.close()
            # Γύρνα πίσω στο βασικό
            sb.driver.switch_to.window(main_window)
            return True # Επέστρεψε True ότι βρήκε και έκλεισε διαφήμιση
    except Exception as e:
        print(f"    Popup kill error: {e}")
        # Για ασφάλεια, γύρνα στο main
        try: sb.driver.switch_to.window(main_window)
        except: pass
    return False

def find_bootstrap_player(page_source):
    try:
        clean_source = page_source.replace(r'\/', '/')
        # Ψάχνουμε γνωστούς players
        player_regex = r'["\']src["\']\s*:\s*["\'](https?://[^"\']*(?:upns\.pro|eyetherapi|greenhaven)[^"\']*)["\']'
        match = re.search(player_regex, clean_source)
        if match: return match.group(1)
            
        # Generic Embed
        generic_embed = r'"video"\s*:\s*\{[^}]*?"src"\s*:\s*"([^"]+)"'
        match_generic = re.search(generic_embed, clean_source, re.DOTALL)
        if match_generic:
            url = match_generic.group(1)
            if url.startswith("http"): return url
    except: pass
    return None

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

def get_stream_and_sub(sb, watch_url):
    video_url = None
    sub_url = None
    final_referer = watch_url 
    
    try:
        sb.uc_open_with_reconnect(watch_url, reconnect_time=3)
        main_window_handle = sb.driver.current_window_handle # Αποθηκεύουμε το ID του παραθύρου μας
        
        # 1. Έλεγχος Bootstrap (JS)
        source = sb.get_page_source()
        player_link = find_bootstrap_player(source)
        
        if player_link:
            final_referer = player_link
            sb.uc_open_with_reconnect(player_link, reconnect_time=3)
            # Ενημερώνουμε το main handle γιατί αλλάξαμε σελίδα (αν και στο ίδιο tab)
            main_window_handle = sb.driver.current_window_handle 
            
            # --- POPUP BATTLE ---
            sb.sleep(1)
            
            # Λίστα με πιθανά σημεία κλικ
            click_targets = ["video", "#player", ".jw-display-icon", "body"]
            
            for target in click_targets:
                try:
                    sb.click(target, timeout=0.5)
                    # Ελέγχουμε αμέσως αν άνοιξε διαφήμιση
                    if close_popups_and_return(sb, main_window_handle):
                        # Αν άνοιξε διαφήμιση, το κλικ πήγε χαμένο. Πρέπει να ΞΑΝΑΚΑΝΟΥΜΕ κλικ.
                        # print("    Refocusing and clicking again...")
                        sb.sleep(0.5)
                        sb.click(target, timeout=0.5)
                except: pass
            
            sb.sleep(3) 
            player_source = sb.get_page_source()
            v, s = extract_final_link(player_source)
            if v: return v, s, final_referer

        # 2. Fallback Iframe
        sb.sleep(1)
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
                    
                    # Κλικ και εδώ (μέσα στο iframe)
                    try: 
                        sb.click("video", timeout=1)
                        # Δεν μπορούμε εύκολα να ελέγξουμε παράθυρα όσο είμαστε σε iframe context
                        # Ελπίζουμε ότι το SeleniumBase θα μείνει στο frame
                    except: pass
                    
                    sb.sleep(3)
                    frame_source = sb.get_page_source()
                    v, s = extract_final_link(frame_source)
                    if v:
                        if frame_src.startswith("http"): final_referer = frame_src
                        sb.switch_to_default_content()
                        return v, s, final_referer
                    sb.switch_to_default_content()
                except: sb.switch_to_default_content()

        # 3. Last Check
        v, s = extract_final_link(source)
        if v: return v, s, final_referer
                
    except Exception as e: 
        print(f"Error getting stream {watch_url}: {e}")
        # Emergency cleanup if crashed
        try:
             if len(sb.driver.window_handles) > 1:
                 sb.driver.switch_to.window(sb.driver.window_handles[0])
        except: pass
        
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

def main():
    all_streams = []
    # Προσθέσαμε το block_images=True για ταχύτητα
    with SB(uc=True, test=True, headless=False, xvfb=True, block_images=True) as sb:
        for list_url in START_URLS:
            print(f"Loading List: {list_url}")
            try:
                sb.uc_open_with_reconnect(list_url, reconnect_time=4)
                try: sb.uc_gui_click_captcha()
                except: pass
                sb.sleep(2)
                sb.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                sb.sleep(2)
                
                source = sb.get_page_source()
                soup = BeautifulSoup(source, 'html.parser')
                
                movie_links = []
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if '/titles/' in href and 'page=' not in href:
                        full_link = href if href.startswith('http') else BASE_URL + href
                        if full_link not in movie_links: movie_links.append(full_link)
                print(f"Found {len(movie_links)} movies on page.")
                
                for i, m_url in enumerate(movie_links):
                    print(f"Processing ({i+1}/{len(movie_links)}): {m_url}")
                    try:
                        sb.uc_open_with_reconnect(m_url, reconnect_time=2)
                        msource = sb.get_page_source()
                        msoup = BeautifulSoup(msource, 'html.parser')
                        title_tag = msoup.find('h1')
                        title = title_tag.text.strip() if title_tag else "Unknown"
                        
                        watch_url = None
                        label = "Stream"
                        for a in msoup.find_all('a', href=True):
                            if '/watch/' in a['href']:
                                temp_label = a.text.strip()
                                if "Trailer" in temp_label or "trailer" in temp_label.lower(): continue
                                if temp_label: label = temp_label
                                else: label = "Stream"
                                watch_url = a['href'] if a['href'].startswith('http') else BASE_URL + a['href']
                                break 
                        
                        if watch_url:
                            stream_link, sub_link, dynamic_referer = get_stream_and_sub(sb, watch_url)
                            if stream_link:
                                print(f"  + Found: {stream_link}")
                                stream_link = stream_link.split('"')[0].split("'")[0]
                                all_streams.append({
                                    'title': f"{title} [{label}]",
                                    'url': stream_link,
                                    'subtitle': sub_link,
                                    'referer': dynamic_referer
                                })
                            else: print(f"  - No link found in {watch_url}")
                        else: print("  - No watch button found")
                    except Exception as e: print(f"Error processing movie {m_url}: {e}")
            except Exception as e: print(f"Error on list {list_url}: {e}")

    if all_streams: smart_save_m3u(all_streams)
    else: print("❌ No streams found.")

if __name__ == "__main__":
    main()
