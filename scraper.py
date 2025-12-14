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

# --- ΒΟΗΘΗΤΙΚΕΣ ΣΥΝΑΡΤΗΣΕΙΣ ---

def extract_bootstrap_url(soup):
    """
    Ψάχνει μέσα στα <script> για το window.bootstrapData και εξάγει το 'src' του βίντεο.
    Αυτό είναι απαραίτητο για ταινίες που κρύβουν τον player σε JS objects.
    """
    try:
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string and 'window.bootstrapData' in script.string:
                # Χρησιμοποιούμε Regex για να βρούμε το src μέσα στο video object
                # Ψάχνουμε: 'video': { ... 'src': 'URL'
                # Το pattern πιάνει το src που είναι μέσα σε single quotes
                match = re.search(r"'video':\s*\{[^}]*?'src':\s*'([^']+)'", script.string, re.DOTALL)
                
                # Αν δεν πετύχει το ακριβές, ψάχνουμε πιο γενικά για src που μοιάζει με embed
                if not match:
                    match = re.search(r"'src':\s*'([^']+(?:upns\.pro|embed|player)[^']*)'", script.string)
                
                if match:
                    url = match.group(1)
                    # Καθαρίζουμε τα escaped slashes
                    url = url.replace(r'\/', '/')
                    return url
    except Exception as e:
        print(f"Error parsing bootstrap data: {e}")
    return None

def extract_links_from_source(source):
    video_url = None
    sub_url = None
    clean_source = source.replace(r'\/', '/')
    
    # Regex για βίντεο και master files (.txt)
    vid_regex = r'(https?://[^"\'<>]+\.(?:mp4|m3u8|txt)(?:[^"\'<>]*)?)'
    sub_regex = r'(https?://[^"\'<>]+\.(?:vtt|srt)(?:[^"\'<>]*)?)'
    
    vid_matches = re.findall(vid_regex, clean_source)
    for match in vid_matches:
        if "google" not in match and "facebook" not in match and "w3.org" not in match:
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
        # 1. Ανοίγουμε την αρχική σελίδα watch
        sb.uc_open_with_reconnect(watch_url, reconnect_time=3)
        sb.sleep(2) 
        
        source = sb.get_page_source()
        soup = BeautifulSoup(source, 'html.parser')

        # --- ΕΛΕΓΧΟΣ 1: Υπάρχει λινκ στο JS (Bootstrap Data); ---
        # Αυτό λύνει το πρόβλημα με τα λινκ που έστειλες
        bootstrap_link = extract_bootstrap_url(soup)
        
        if bootstrap_link and bootstrap_link.startswith('http'):
            # print(f"    -> Redirecting to embedded player: {bootstrap_link}")
            final_referer = bootstrap_link # O referer γίνεται ο embed player
            
            # Πηγαίνουμε κατευθείαν στον player (π.χ. upns.pro)
            sb.uc_open_with_reconnect(bootstrap_link, reconnect_time=3)
            
            # Κλικ στο Play (για σιγουριά)
            try: sb.click("video", timeout=1)
            except: pass
            try: sb.click(".jw-display-icon", timeout=1)
            except: pass
            
            sb.sleep(4) # Περιμένουμε να φορτώσει το master file
            
            # Ψάχνουμε στο source του player
            player_source = sb.get_page_source()
            video_url, sub_url = extract_links_from_source(player_source)
            
            if video_url:
                return video_url, sub_url, final_referer

        # --- ΕΛΕΓΧΟΣ 2: Κανονικό ψάξιμο (Αν δεν βρήκαμε bootstrap link) ---
        # Ψάχνουμε στην τρέχουσα σελίδα (ή αν το bootstrap απέτυχε)
        v, s = extract_links_from_source(source)
        if v: return v, s, final_referer

        # --- ΕΛΕΓΧΟΣ 3: Iframe Deep Search ---
        iframes = sb.find_elements("iframe")
        if iframes:
            for i in range(len(iframes)):
                try:
                    current_iframes = sb.find_elements("iframe")
                    if i >= len(current_iframes): break
                    frame = current_iframes[i]
                    frame_src = frame.get_attribute("src")
                    if not frame_src or any(x in frame_src for x in ["google", "facebook", "twitter", "ads"]): continue
                    
                    sb.switch_to_frame(frame)
                    try: sb.click("video", timeout=1) 
                    except: pass
                    sb.sleep(3) 
                    
                    frame_source = sb.get_page_source()
                    v_frame, s_frame = extract_links_from_source(frame_source)
                    if v_frame:
                        video_url = v_frame
                        sub_url = s_frame
                        if frame_src.startswith("http"): final_referer = frame_src
                        sb.switch_to_default_content()
                        break
                    sb.switch_to_default_content()
                except: sb.switch_to_default_content()
                
    except Exception as e: 
        print(f"Error getting stream {watch_url}: {e}")
        
    return video_url, sub_url, final_referer

# --- SMART SAVE ---
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
    with SB(uc=True, test=True, headless=False, xvfb=True) as sb:
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
                        # Εδώ βελτιώθηκε η λογική: Αν υπάρχουν πολλά βίντεο, παίρνουμε το 1ο που δεν είναι Trailer
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
