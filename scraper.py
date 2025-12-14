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
        sb.uc_open_with_reconnect(watch_url, reconnect_time=3)
        # Ψάχνουμε το link στον κώδικα
        source = sb.get_page_source()
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
                # Άνοιγμα με reconnect για να μπερδέψουμε το Cloudflare
                sb.uc_open_with_reconnect(list_url, reconnect_time=4)
                
                # Προσπάθεια αυτόματης λύσης Captcha αν εμφανιστεί
                try:
                    sb.uc_gui_click_captcha()
                    print("Attempted to click Captcha...")
                except:
                    pass
                
                # Scroll για να φορτώσουν όλα
                sb.sleep(2)
                sb.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                sb.sleep(2)
                
                source = sb.get_page_source()
                soup = BeautifulSoup(source, 'html.parser')
                
                # Εύρεση ταινιών
                movie_links = []
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    if '/titles/' in href and 'page=' not in href:
                        full_link = href if href.startswith('http') else BASE_URL + href
                        if full_link not in movie_links:
                            movie_links.append(full_link)
                
                print(f"Found {len(movie_links)} movies on page.")
                
                # Επεξεργασία ταινιών (μόνο οι πρώτες 5 για δοκιμή να μην λήξει ο χρόνος)
                for i, m_url in enumerate(movie_links[:5]):
                    print(f"Processing: {m_url}")
                    
                    # Άνοιγμα σελίδας ταινίας
                    sb.uc_open_with_reconnect(m_url, reconnect_time=2)
                    msource = sb.get_page_source()
                    msoup = BeautifulSoup(msource, 'html.parser')
                    
                    title_tag = msoup.find('h1')
                    title = title_tag.text.strip() if title_tag else "Unknown"
                    
                    # Βρίσκουμε το κουμπί Watch
                    watch_url = None
                    label = "Stream"
                    for a in msoup.find_all('a', href=True):
                        if '/watch/' in a['href']:
                            label = a.text.strip()
                            if "Trailer" in label: continue
                            if not label: label = "Stream"
                            watch_url = a['href'] if a['href'].startswith('http') else BASE_URL + a['href']
                            break # Παίρνουμε το πρώτο link για τώρα
                    
                    if watch_url:
                        print(f"  Checking Stream URL: {watch_url}")
                        stream_link = get_stream_url(sb, watch_url)
                        if stream_link:
                            print(f"  + Found: {stream_link}")
                            all_streams.append({
                                'title': f"{title} [{label}]",
                                'url': stream_link,
                                'referer': BASE_URL
                            })
            except Exception as e:
                print(f"Error on list {list_url}: {e}")

    # Αποθήκευση
    if all_streams:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for s in streams:
                clean_title = s['title'].replace(",", " -").replace("\n", " ")
                f.write(f"#EXTINF:-1 group-title=\"Movies\",{clean_title}\n")
                f.write(f"#EXTVLCOPT:http-referrer={s['referer']}/\n")
                f.write(f"#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36\n")
                f.write(f"{s['url']}\n")
        print("Playlist saved!")
    else:
        print("No streams found.")

if __name__ == "__main__":
    main()
