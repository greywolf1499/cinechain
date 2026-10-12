import argparse
import json
import re
import sys
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi import requests


class TVTropesMovieScraper:
    def __init__(self, impersonate: str = "chrome"):
        """Use Chrome-style TLS for this opt-in proof-of-concept."""
        self.session = requests.Session(impersonate=impersonate)

    def normalize_url(self, user_input: str) -> str:
        """
        Converts user input (title, slug, or full URL) into a TV Tropes Film URL.
        Examples:
            'The Matrix'                     -> 'https://tvtropes.org/pmwiki/pmwiki.php/Film/TheMatrix'
            'spider-man'                     -> 'https://tvtropes.org/pmwiki/pmwiki.php/Film/SpiderMan'
            'Don\'t Look Up'                 -> 'https://tvtropes.org/pmwiki/pmwiki.php/Film/DontLookUp'
            'https://tvtropes.org/.../Film/' -> keeps as is
        """
        raw = user_input.strip()
        if raw.startswith(("http://", "https://")):
            return raw
        if raw.startswith("Film/"):
            return f"https://tvtropes.org/pmwiki/pmwiki.php/{raw}"

        # Clean punctuation and build WikiWord / CamelCase slug
        cleaned = re.sub(r"[^\w\s\-]", "", raw)
        parts = [p for p in re.split(r"[\s\-_]+", cleaned) if p]

        if not parts:
            raise ValueError("Invalid movie name provided.")

        # Preserve casing if already CamelCase, otherwise capitalize words
        if len(parts) == 1 and not parts[0].islower():
            slug = parts[0]
        else:
            slug = "".join(p.capitalize() for p in parts)

        return f"https://tvtropes.org/pmwiki/pmwiki.php/Film/{slug}"

    def fetch_page(self, url: str) -> str:
        """
        Fetches the HTML of a page using curl_cffi with anti-bot checks.
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        res = self.session.get(url, headers=headers, timeout=15)

        if res.status_code == 404:
            raise FileNotFoundError(
                f"Page not found (404) at {url}. "
                "Please verify the movie title or provide the direct TV Tropes URL."
            )
        elif res.status_code != 200:
            raise ConnectionError(f"HTTP error {res.status_code} when fetching {url}")

        # Check for Cloudflare challenge response
        cf_blocks = ["Just a moment...", "cf-browser-verification", "Attention Required!"]
        if any(block in res.text for block in cf_blocks):
            raise PermissionError("Encountered Cloudflare challenge page. Try again later.")

        return res.text

    def find_subpages(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        """
        Some massive movies (e.g., The Matrix, Avengers) split tropes into
        subpages like 'Tropes A to D', 'Tropes E to M', etc.
        """
        article = soup.find("div", id="main-article") or soup.find("div", class_="article-content")
        if not article:
            return []

        subpage_pattern = re.compile(r"Tropes\s+[A-Za-z0-9]+\s+to\s+[A-Za-z0-9]+", re.IGNORECASE)
        subpage_href_pattern = re.compile(r"Tropes[A-Za-z0-9]+To[A-Za-z0-9]+", re.IGNORECASE)

        subpages = []
        seen = {base_url}

        for a in article.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if subpage_pattern.search(text) or subpage_href_pattern.search(href):
                full_url = urljoin(base_url, href)
                if full_url not in seen:
                    seen.add(full_url)
                    subpages.append(full_url)

        return subpages

    def extract_tropes_from_html(self, html: str, base_url: str) -> list[dict]:
        """
        Extracts tropes, trope URLs, and descriptions from TV Tropes list items.
        """
        soup = BeautifulSoup(html, "html.parser")
        article = (
            soup.find("div", id="main-article")
            or soup.find("div", class_="article-content")
            or soup.body
        )
        if not article:
            return []

        tropes = []
        seen = set()

        for li in article.find_all("li"):
            # Tropes link to the 'Main' namespace: /pmwiki/pmwiki.php/Main/<TropeName>
            trope_link = li.find(
                "a", href=lambda h: h and ("/pmwiki/pmwiki.php/Main/" in h or "/Main/" in h)
            )
            if not trope_link:
                continue

            trope_name = trope_link.get_text(strip=True)
            if not trope_name:
                continue

            trope_url = urljoin(base_url, trope_link["href"])

            # Clone <li> and strip nested sub-lists so child trope text doesn't bleed in
            li_clone = BeautifulSoup(str(li), "html.parser").find("li")
            for sub_list in li_clone.find_all(["ul", "ol"]):
                sub_list.decompose()

            # Clean up whitespace
            full_text = re.sub(r"\s+", " ", li_clone.get_text(separator=" ", strip=True))

            # Remove the trope title prefix to isolate the description
            desc = full_text
            if desc.lower().startswith(trope_name.lower()):
                desc = desc[len(trope_name) :].lstrip(" :-\u2013\u2014").strip()

            key = (trope_name, desc[:60])
            if key not in seen:
                seen.add(key)
                tropes.append(
                    {
                        "trope": trope_name,
                        "url": trope_url,
                        "description": desc,
                    }
                )

        return tropes

    def scrape_movie(self, movie_input: str) -> list[dict]:
        """
        Main pipeline: resolves URL, fetches main page, handles subpages if present,
        and aggregates tropes.
        """
        target_url = self.normalize_url(movie_input)
        print(f"[*] Accessing: {target_url}")

        main_html = self.fetch_page(target_url)
        soup = BeautifulSoup(main_html, "html.parser")

        # Check if the movie is split across subpages
        subpages = self.find_subpages(soup, target_url)

        if subpages:
            print(f"[*] Detected {len(subpages)} trope subpage(s). Scraping subpages...")
            all_tropes = []
            for sub_url in subpages:
                print(f"    -> Fetching subpage: {sub_url}")
                time.sleep(1)  # Polite crawl delay
                sub_html = self.fetch_page(sub_url)
                page_tropes = self.extract_tropes_from_html(sub_html, sub_url)
                all_tropes.extend(page_tropes)
            return all_tropes
        else:
            return self.extract_tropes_from_html(main_html, target_url)


def main():
    parser = argparse.ArgumentParser(description="Cache a film's TV Tropes links locally.")
    parser.add_argument("movie", nargs="*", help="Movie title, slug, or TV Tropes URL")
    args = parser.parse_args()
    if args.movie:
        movie_query = " ".join(args.movie)
    else:
        movie_query = input("Enter movie title, slug, or TV Tropes URL: ")

    scraper = TVTropesMovieScraper()

    try:
        results = scraper.scrape_movie(movie_query)
    except (OSError, ValueError, requests.exceptions.RequestException) as exc:
        print(f"[!] Error: {exc}")
        sys.exit(1)
    finally:
        scraper.session.close()

    print(f"\n[+] Successfully extracted {len(results)} tropes!\n")

    # Preview first 5 entries
    for item in results[:5]:
        print(f"• {item['trope']}")
        print(f"  URL: {item['url']}")
        print(f"  Example: {item['description']}\n")

    if len(results) > 5:
        print(f"... and {len(results) - 5} more tropes.")

    # Save output to JSON
    safe_name = re.sub(r"[^\w\-]", "_", movie_query.strip())
    filename = f"{safe_name}_tropes.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"[+] Saved full results to: {filename}")


if __name__ == "__main__":
    main()
