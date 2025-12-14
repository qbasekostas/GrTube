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

def get_stream_url(sb, watch_url):
    try:
        # Ανοίγουμε τη σελίδα του player
        sb.uc_open_with_reconnect(watch_url, reconnect_time=3)
        sb.sleep(1) # Μικρή αναμονή να φορτώσει ο player
        
        # Παίρνουμε τον κώδικα
        source = sb.get_page_source()
        
        # Ψάχνουμε το link (.mp4, .m3u8 ή .txt)
        regex = r'(https?://[^\s"\'<>]+\.(?:mp4|m3u8|txt))'
        match = re.search(regex, source)
        if match:
            return match.group(1)
    except Exception as e:
        print(f"Error getting stream {watch_url}: {e}")
    return None

def main():
    all_streams = []
    
    # Ξεκινάμε το SeleniumBase με UC Mode (Undetected) και xvfb (εικονική οθόνη)
    with SB(uc=True, test=True, headless=False, xvfb=True) as sb:
        
        for list_url in START_URLS:
            print(f"Loading List: {list_url}")
            try:
                # Άνοιγμα λίστας
                sb.uc_open_with_reconnect(list_url, reconnect_time=4)
                
                # Προσπάθεια κλικ στο Captcha (αν υπάρχει)
                try:
                    sb.uc_gui_click_captcha()
                    print("Attempted to click Captcha...")
                except:
                    pass
                
                # Scroll down
                sb.sleep(2)
                sb.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                sb.sleep(2)
                
                source = sb.get_page_source()
                soup = BeautifulSoup(source, 'html.parser')
                
                # Εύρεση ταινιών στη σελίδα
                movie_links = []
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if '/titles/' in href and 'page=' not in href:
                        full_link = href if href.startswith('http') else BASE_URL + href
                        if full_link not in movie_links:
                            movie_links.append(full_link)
                
                print(f"Found {len(movie_links)} movies on page.")
                
                # --- ΕΠΕΞΕΡΓΑΣΙΑ ΤΑΙΝΙΩΝ ---
                # Τώρα τις παίρνει ΟΛΕΣ (αφαιρέθηκε το [:5])
                for i, m_url in enumerate(movie_links):
                    print(f"Processing ({i+1}/{len(movie_links)}): {m_url}")
                    
                    try:
                        # Άνοιγμα σελίδας ταινίας
                        sb.uc_open_with_reconnect(m_url, reconnect_time=2)
                        msource = sb.get_page_source()
                        msoup = BeautifulSoup(msource, 'html.parser')
                        
                        title_tag = msoup.find('h1')
                        title = title_tag.text.strip() if title_tag else "Unknown"
                        
                        # Βρίσκουμε το κουμπί Watch (αγνοούμε trailers)
                        watch_url = None
                        label = "Stream"
                        
                        # Ψάχνουμε όλα τα links που έχουν /watch/
                        for a in msoup.find_all('a', href=True):
                            if '/watch/' in a['href']:
                                temp_label = a.text.strip()
                                # Αγνοούμε τα Trailer
                                if "Trailer" in temp_label or "trailer" in temp_label.lower():
                                    continue
                                
                                if temp_label: label = temp_label
                                else: label = "Stream"
                                
                                watch_url = a['href'] if a['href'].startswith('http') else BASE_URL + a['href']
                                break # Παίρνουμε το πρώτο έγκυρο stream link
                        
                        if watch_url:
                            # print(f"  Checking Stream URL: {watch_url}")
                            stream_link = get_stream_url(sb, watch_url)
                            if stream_link:
                                print(f"  + Found: {stream_link}")
                                all_streams.append({
                                    'title': f"{title} [{label}]",
                                    'url': stream_link,
                                    'referer': BASE_URL
                                })
                            else:
                                print(f"  - No link found in {watch_url}")
                        else:
                            print("  - No watch button found (maybe only trailer?)")
                            
                    except Exception as e:
                        print(f"Error processing movie {m_url}: {e}")

            except Exception as e:
                print(f"Error on list {list_url}: {e}")

    # Αποθήκευση Playlist
    if all_streams:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            # Διόρθωση: Χρησιμοποιούμε το all_streams αντί για streams
            for s in all_streams:
                clean_title = s['title'].replace(",", " -").replace("\n", " ")
                f.write(f"#EXTINF:-1 group-title=\"Movies\",{clean_title}\n")
                f.write(f"#EXTVLCOPT:http-referrer={s['referer']}/\n")
                f.write(f"#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36\n")
                f.write(f"{s['url']}\n")
        print(f"✅ Playlist saved! Total videos: {len(all_streams)}")
    else:
        print("❌ No streams found.")

if __name__ == "__main__":
    main()
