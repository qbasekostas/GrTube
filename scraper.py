from seleniumbase import SB
from bs4 import BeautifulSoup
import re
import time
import os

BASE_URL = "https://greektube.pro"
START_URLS = [
    "https://greektube.pro/movies?order=created_at%3Adesc",
    #"https://greektube.pro/movies?order=created_at%3Adesc&page=2"
]
OUTPUT_FILE = "GrTube.m3u"

def extract_links_from_source(source):
    """Βοηθητική συνάρτηση που καθαρίζει τον κώδικα και ψάχνει λινκ"""
    video_url = None
    sub_url = None
    
    # 1. Αφαίρεση escaped slashes (π.χ. https:\/\/ -> https://)
    clean_source = source.replace(r'\/', '/')
    
    # 2. Regex που πιάνει .mp4, .m3u8, .txt ακόμα και αν έχουν παραμέτρους πίσω
    # Ψάχνει να ξεκινάει με http και να περιέχει την κατάληξη, μέχρι να βρει " ή ' ή < ή >
    vid_regex = r'(https?://[^"\'<>]+\.(?:mp4|m3u8|txt)(?:[^"\'<>]*)?)'
    
    # Regex για υπότιτλους
    sub_regex = r'(https?://[^"\'<>]+\.(?:vtt|srt)(?:[^"\'<>]*)?)'
    
    vid_matches = re.findall(vid_regex, clean_source)
    # Φιλτράρισμα: Αν βρει πολλά, προτιμάμε αυτά που δεν είναι .js ή .css (αν και το regex έχει extensions)
    for match in vid_matches:
        # Μερικές φορές πιάνει σκουπίδια, κάνουμε check
        if "google" not in match and "facebook" not in match:
            video_url = match
            break # Παίρνουμε το πρώτο καλό
            
    sub_match = re.search(sub_regex, clean_source)
    if sub_match:
        sub_url = sub_match.group(1)
        
    return video_url, sub_url

def get_stream_and_sub(sb, watch_url):
    video_url = None
    sub_url = None
    final_referer = watch_url 
    
    try:
        # Άνοιγμα σελίδας
        sb.uc_open_with_reconnect(watch_url, reconnect_time=3)
        sb.sleep(2) 
        
        # --- ΒΗΜΑ 1: Έλεγχος στην Κύρια Σελίδα ---
        source = sb.get_page_source()
        v, s = extract_links_from_source(source)
        if v:
            return v, s, final_referer

        # --- ΒΗΜΑ 2: Deep Search στα IFRAMES ---
        iframes = sb.find_elements("iframe")
        if iframes:
            # print(f"    > Found {len(iframes)} iframes. Deep scanning...")
            
            for i in range(len(iframes)):
                try:
                    # Refresh elements list
                    current_iframes = sb.find_elements("iframe")
                    if i >= len(current_iframes): break
                    
                    frame = current_iframes[i]
                    frame_src = frame.get_attribute("src")
                    
                    # Αγνοούμε διαφημίσεις
                    if not frame_src or any(x in frame_src for x in ["google", "facebook", "twitter", "ads"]):
                        continue
                        
                    # Μπαίνουμε στο iframe
                    sb.switch_to_frame(frame)
                    
                    # --- ΤΡΙΚ: Προσπάθεια Click στο Play ---
                    # Πολλά players δεν φορτώνουν το src αν δεν πατήσεις κλικ
                    try:
                        # Ψάχνουμε κοινά classes για play buttons
                        sb.click("video", timeout=1) 
                    except: pass
                    
                    try:
                        sb.click(".jw-display-icon", timeout=1) # JWPlayer
                    except: pass
                    
                    try:
                        sb.click(".play-button", timeout=1)
                    except: pass
                    
                    # Περιμένουμε να αντιδράσει το JS
                    sb.sleep(4) 
                    
                    # Παίρνουμε τον κώδικα του iframe
                    frame_source = sb.get_page_source()
                    
                    # Ψάχνουμε
                    v_frame, s_frame = extract_links_from_source(frame_source)
                    
                    if v_frame:
                        video_url = v_frame
                        sub_url = s_frame
                        
                        # Αν βρέθηκε σε iframe, ο referer είναι το src του iframe
                        if frame_src.startswith("http"):
                            final_referer = frame_src
                        
                        sb.switch_to_default_content()
                        break
                    
                    sb.switch_to_default_content()
                    
                except Exception as frame_err:
                    # print(f"    Frame error: {frame_err}")
                    sb.switch_to_default_content()
                        
    except Exception as e:
        print(f"Error getting stream {watch_url}: {e}")
        
    return video_url, sub_url, final_referer

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
                        if full_link not in movie_links:
                            movie_links.append(full_link)
                
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
                                # Clean up URL if it has junk at the end (extra quotes etc)
                                stream_link = stream_link.split('"')[0].split("'")[0]
                                
                                all_streams.append({
                                    'title': f"{title} [{label}]",
                                    'url': stream_link,
                                    'subtitle': sub_link,
                                    'referer': dynamic_referer
                                })
                            else:
                                print(f"  - No link found in {watch_url}")
                        else:
                            print("  - No watch button found")
                            
                    except Exception as e:
                        print(f"Error processing movie {m_url}: {e}")

            except Exception as e:
                print(f"Error on list {list_url}: {e}")

    if all_streams:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for s in all_streams:
                clean_title = s['title'].replace(",", " -").replace("\n", " ")
                f.write(f"#EXTINF:-1 group-title=\"Movies\",{clean_title}\n")
                f.write(f"#EXTVLCOPT:http-referrer={s['referer']}/\n")
                f.write(f"#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36\n")
                if s['subtitle']:
                    f.write(f"#EXTVLCOPT:sub-file={s['subtitle']}\n")
                f.write(f"{s['url']}\n")
        print(f"✅ Playlist saved! Total videos: {len(all_streams)}")
    else:
        print("❌ No streams found.")

if __name__ == "__main__":
    main()
