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

# --- ΒΕΛΤΙΩΜΕΝΟ PARSING (REGEX MODE) ---
def find_hidden_player_url(page_source):
    """
    Ψάχνει για το src του βίντεο απευθείας στον κώδικα (bootstrapData) με Regex.
    Αγνοεί αν το JSON είναι έγκυρο ή όχι.
    """
    try:
        # 1. Ψάχνουμε το pattern: "src": "https://...upns.pro..."
        # Το '?' στο τέλος το κάνει non-greedy (σταματάει στο πρώτο ")
        # Δοκιμάζουμε με διπλά εισαγωγικά "
        match = re.search(r'"src"\s*:\s*"([^"]+)"', page_source)
        if not match:
            # Δοκιμάζουμε με μονά εισαγωγικά '
            match = re.search(r"'src'\s*:\s*'([^']+)'", page_source)
        
        if match:
            url = match.group(1)
            # Καθαρίζουμε τα escaped slashes (https:\/\/ -> https://)
            url = url.replace(r'\/', '/')
            
            # Έλεγχος: Είναι όντως βίντεο player;
            if any(x in url for x in ['upns.pro', 'embed', 'player', 'youtube', 'vimeo']):
                return url
                
    except Exception as e:
        print(f"Error parsing hidden url: {e}")
    return None

def extract_links_from_source(source):
    video_url = None
    sub_url = None
    clean_source = source.replace(r'\/', '/')
    
    # Regex για βίντεο (.mp4, .m3u8, .txt) - Βελτιωμένο
    # Πιάνει και links που έχουν παραμέτρους (π.χ. .txt?token=...)
    vid_regex = r'(https?://[^"\'<>]+\.(?:mp4|m3u8|txt)(?:[^"\'<>\s]*)?)'
    
    # Regex για υπότιτλους
    sub_regex = r'(https?://[^"\'<>]+\.(?:vtt|srt)(?:[^"\'<>\s]*)?)'
    
    vid_matches = re.findall(vid_regex, clean_source)
    for match in vid_matches:
        # Φίλτρο για σκουπίδια
        if not any(bad in match for bad in ["google", "facebook", "w3.org", "schema.org", "image.tmdb", "cloudflare"]):
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
        # Άνοιγμα σελίδας
        sb.uc_open_with_reconnect(watch_url, reconnect_time=3)
        sb.sleep(2) 
        
        # --- ΜΕΘΟΔΟΣ 1: Εύρεση Κρυμμένου Player (Bootstrap) ---
        full_source = sb.get_page_source()
        hidden_player = find_hidden_player_url(full_source)
        
        if hidden_player and hidden_player.startswith('http'):
            # print(f"    -> Redirecting to hidden player: {hidden_player}")
            final_referer = hidden_player
            
            # Πάμε στον player (π.χ. upns.pro)
            sb.uc_open_with_reconnect(hidden_player, reconnect_time=3)
            
            # --- ΠΟΛΛΑΠΛΑ ΚΛΙΚ ΓΙΑ ΝΑ ΞΥΠΝΗΣΕΙ Ο PLAYER ---
            # Κάνουμε κλικ στο κέντρο και σε πιθανά κουμπιά
            sb.sleep(1)
            try: sb.click("body", timeout=1) # Γενικό κλικ για focus
            except: pass
            
            # Ψάχνουμε διάφορα play buttons
            selectors = ["video", ".jw-display-icon", "#player", ".play-button", "button[aria-label='Play']"]
            for sel in selectors:
                try: 
                    sb.click(sel, timeout=0.5)
                    # print(f"       Clicked {sel}")
                except: pass
            
            sb.sleep(4) # Αναμονή για φόρτωση master file
            
            player_source = sb.get_page_source()
            video_url, sub_url = extract_links_from_source(player_source)
            
            if video_url: return video_url, sub_url, final_referer

        # --- ΜΕΘΟΔΟΣ 2: Κανονικό Scan (Αν δεν πέτυχε το redirect) ---
        v, s = extract_links_from_source(full_source)
        if v: return v, s, final_referer

        # --- ΜΕΘΟΔΟΣ 3: Iframe Deep Search ---
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
                                # Clean garbage from URL end
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
