from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import re
import time
import random

BASE_URL = "https://greektube.pro"
START_URLS = [
    "https://greektube.pro/movies?order=created_at%3Adesc",
    "https://greektube.pro/movies?order=created_at%3Adesc&page=2"
]

# --- MANUAL STEALTH FUNCTION ---
# Κρύβει το γεγονός ότι είμαστε αυτοματοποιημένο ρομπότ
def apply_stealth(page):
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
    """)
    page.add_init_script("""
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
    """)
    page.add_init_script("""
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
    """)

def get_html_with_playwright(url):
    with sync_playwright() as p:
        # Χρήση Firefox (Gecko) - Συχνά περνάει καλύτερα το Cloudflare
        browser = p.firefox.launch(headless=True)
        
        # Ρύθμιση Context για να μοιάζει με Windows PC
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
            viewport={'width': 1920, 'height': 1080},
            locale='en-US',
            timezone_id='Europe/Athens'
        )
        
        page = context.new_page()
        apply_stealth(page) # Εφαρμογή απόκρυψης
        
        print(f"Loading: {url}")
        try:
            # Αυξάνουμε το timeout
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            
            # Έλεγχος για Cloudflare title
            time.sleep(5)
            if "Just a moment" in page.title() or "Cloudflare" in page.title():
                print("⚠️ Cloudflare challenge detected. Attempting to wait it out...")
                time.sleep(10)
                # Κίνηση ποντικιού
                page.mouse.move(random.randint(100, 500), random.randint(100, 500))
                time.sleep(1)
                page.mouse.click(random.randint(100, 500), random.randint(100, 500))
                time.sleep(5)
            
            content = page.content()
            browser.close()
            return content
            
        except Exception as e:
            print(f"Error fetching {url}: {e}")
            browser.close()
            return ""

def get_stream_link(html):
    regex = r'(https?://[^\s"\'<>]+\.(?:mp4|m3u8|txt))'
    match = re.search(regex, html)
    return match.group(1) if match else None

def parse_movies(html_list):
    soup = BeautifulSoup(html_list, 'html.parser')
    movie_links = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/titles/' in href and 'page=' not in href:
            full_link = href if href.startswith('http') else BASE_URL + href
            if full_link not in movie_links:
                movie_links.append(full_link)
    return movie_links

def process_movie(movie_url):
    print(f"Processing movie: {movie_url}")
    html = get_html_with_playwright(movie_url)
    if not html: return []
    
    soup = BeautifulSoup(html, 'html.parser')
    title_tag = soup.find('h1')
    title = title_tag.text.strip() if title_tag else "Unknown Movie"
    
    streams = []
    watch_buttons = []
    for a in soup.find_all('a', href=True):
        if '/watch/' in a['href']:
            label = a.text.strip()
            if "Trailer" in label or "trailer" in label.lower(): continue
            if not label: label = "Stream"
            full_watch_url = a['href'] if a['href'].startswith('http') else BASE_URL + a['href']
            watch_buttons.append((label, full_watch_url))
            
    for label, w_url in watch_buttons:
        print(f"  Checking stream: {label}")
        w_html = get_html_with_playwright(w_url)
        stream_url = get_stream_link(w_html)
        
        if stream_url:
            print(f"  + Found: {stream_url}")
            streams.append({
                'title': f"{title} [{label}]",
                'url': stream_url,
                'referer': BASE_URL
            })
            
    return streams

def save_m3u(streams):
    with open("playlist.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s in streams:
            clean_title = s['title'].replace(",", " -").replace("\n", " ")
            f.write(f"#EXTINF:-1 group-title=\"Movies\",{clean_title}\n")
            f.write(f"#EXTVLCOPT:http-referrer={s['referer']}/\n")
            f.write(f"#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0\n")
            f.write(f"{s['url']}\n")

if __name__ == "__main__":
    all_streams = []
    all_movie_urls = []
    
    for start_url in START_URLS:
        html = get_html_with_playwright(start_url)
        if html:
            urls = parse_movies(html)
            all_movie_urls.extend(urls)
            
    all_movie_urls = list(set(all_movie_urls))
    print(f"Found {len(all_movie_urls)} movies.")
    
    # Δοκιμάζουμε 3 ταινίες για αρχή για να δούμε αν δουλεύει
    for i, movie_url in enumerate(all_movie_urls[:3]): 
        streams = process_movie(movie_url)
        all_streams.extend(streams)
        
    if all_streams:
        save_m3u(all_streams)
        print("Playlist saved!")
    else:
        print("No streams found (probably blocked).")
