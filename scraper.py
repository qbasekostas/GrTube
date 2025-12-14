from seleniumbase import SB
from bs4 import BeautifulSoup
import re
import time
import os

BASE_URL = "https://greektube.pro"
START_URLS = [
    "https://greektube.pro/movies?order=created_at%3Adesc",
    "https://greektube.pro/movies?order=created_at%3Adesc&page=2"
]
OUTPUT_FILE = "playlist.m3u"

def get_stream_and_sub(sb, watch_url):
    video_url = None
    sub_url = None
    final_referer = watch_url # Default referer: η σελίδα της ταινίας
    
    try:
        # Ανοίγουμε τη σελίδα του player
        sb.uc_open_with_reconnect(watch_url, reconnect_time=3)
        sb.sleep(2) # Λίγο παραπάνω χρόνο για να φορτώσουν τα iframes
        
        # Regex για βίντεο και υπότιτλους
        vid_regex = r'(https?://[^\s"\'<>]+\.(?:mp4|m3u8|txt))'
        sub_regex = r'(https?://[^\s"\'<>]+\.(?:vtt|srt))'
        
        # --- ΒΗΜΑ 1: Έλεγχος στην κύρια σελίδα ---
        source = sb.get_page_source()
        vid_match = re.search(vid_regex, source)
        
        if vid_match:
            video_url = vid_match.group(1)
            # Ψάχνουμε και για υπότιτλο στην κύρια σελίδα
            sub_match = re.search(sub_regex, source)
            if sub_match: sub_url = sub_match.group(1)
            
        else:
            # --- ΒΗΜΑ 2: Έλεγχος μέσα στα IFRAMES (Deep Search) ---
            # Βρίσκουμε όλα τα iframes
            iframes = sb.find_elements("iframe")
            if iframes:
                # print(f"  > Found {len(iframes)} iframes. Searching inside...")
                
                # Δοκιμάζουμε να μπούμε σε κάθε iframe
                for i in range(len(iframes)):
                    try:
                        # Πρέπει να ξαναβρούμε το element γιατί το DOM μπορεί να άλλαξε
                        current_iframes = sb.find_elements("iframe")
                        if i >= len(current_iframes): break
                        
                        frame = current_iframes[i]
                        frame_src = frame.get_attribute("src")
                        
                        # Αγνοούμε iframes διαφημίσεων/google/facebook για ταχύτητα
                        if not frame_src or "google" in frame_src or "facebook" in frame_src:
                            continue
                            
                        # Μπαίνουμε στο iframe
                        sb.switch_to_frame(frame)
                        frame_source = sb.get_page_source()
                        
                        # Ψάχνουμε πάλι μέσα στο iframe
                        frame_vid_match = re.search(vid_regex, frame_source)
                        
                        if frame_vid_match:
                            video_url = frame_vid_match.group(1)
                            
                            # ΣΗΜΑΝΤΙΚΟ: Αλλάζουμε τον Referer στο URL του Iframe!
                            # Π.χ. αν το iframe είναι greektube.upns.pro, αυτός είναι ο referer
                            if frame_src and frame_src.startswith("http"):
                                final_referer = frame_src
                                # Καθαρίζουμε τον referer να είναι το base url του iframe (προαιρετικά)
                                # Αλλά συνήθως αρκεί το full url ή το domain. Ας κρατήσουμε το full url του iframe.
                            
                            # Ψάχνουμε υπότιτλο μέσα στο iframe
                            frame_sub_match = re.search(sub_regex, frame_source)
                            if frame_sub_match: sub_url = frame_sub_match.group(1)
                            
                            # Βγαίνουμε πίσω στην κύρια σελίδα και σταματάμε το ψάξιμο
                            sb.switch_to_default_content()
                            break
                        
                        # Βγαίνουμε πίσω για να πάμε στο επόμενο
                        sb.switch_to_default_content()
                        
                    except Exception as frame_err:
                        # print(f"    Error searching iframe {i}: {frame_err}")
                        sb.switch_to_default_content()
                        
    except Exception as e:
        print(f"Error getting stream/sub {watch_url}: {e}")
        
    return video_url, sub_url, final_referer

def main():
    all_streams = []
    
    # Ξεκινάμε το SeleniumBase
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
                                if "Trailer" in temp_label or "trailer" in temp_label.lower():
                                    continue
                                if temp_label: label = temp_label
                                else: label = "Stream"
                                watch_url = a['href'] if a['href'].startswith('http') else BASE_URL + a['href']
                                break 
                        
                        if watch_url:
                            # Καλούμε τη νέα συνάρτηση που επιστρέφει ΚΑΙ τον σωστό Referer
                            stream_link, sub_link, dynamic_referer = get_stream_and_sub(sb, watch_url)
                            
                            if stream_link:
                                print(f"  + Video: {stream_link}")
                                print(f"    (Ref: {dynamic_referer})") # Debug print για να δούμε τι βρήκε
                                
                                if sub_link:
                                    print(f"  + Subtitle: {sub_link}")
                                
                                all_streams.append({
                                    'title': f"{title} [{label}]",
                                    'url': stream_link,
                                    'subtitle': sub_link,
                                    'referer': dynamic_referer # <-- Ο σωστός Referer (είτε main είτε iframe)
                                })
                            else:
                                print(f"  - No link found in {watch_url}")
                        else:
                            print("  - No watch button found")
                            
                    except Exception as e:
                        print(f"Error processing movie {m_url}: {e}")

            except Exception as e:
                print(f"Error on list {list_url}: {e}")

    # Αποθήκευση Playlist
    if all_streams:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for s in all_streams:
                clean_title = s['title'].replace(",", " -").replace("\n", " ")
                f.write(f"#EXTINF:-1 group-title=\"Movies\",{clean_title}\n")
                
                # Dynamic Referer
                f.write(f"#EXTVLCOPT:http-referrer={s['referer']}\n")
                
                # User Agent
                f.write(f"#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36\n")
                
                # Subtitle
                if s['subtitle']:
                    f.write(f"#EXTVLCOPT:sub-file={s['subtitle']}\n")
                
                f.write(f"{s['url']}\n")
        print(f"✅ Playlist saved! Total videos: {len(all_streams)}")
    else:
        print("❌ No streams found.")

if __name__ == "__main__":
    main()
