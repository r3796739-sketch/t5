"""
Website Scraper Utility
Crawls websites and extracts clean text content for chatbot training.
Supports WordPress API, HTML scraping, and sitemap parsing.
"""

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from typing import List, Dict, Optional, Set
import logging
import time
import re

logger = logging.getLogger(__name__)


class WebsiteScraper:
    """
    Website scraper that extracts clean text content from web pages.
    Supports WordPress REST API and general HTML scraping.
    """
    
    def __init__(
        self, 
        max_pages: int = 50,
        timeout: int = 10,
        user_agent: str = "YoppyChat-Bot/1.0"
    ):
        """
        Initialize the scraper.
        
        Args:
            max_pages: Maximum number of pages to crawl
            timeout: Request timeout in seconds
            user_agent: User agent string for requests
        """
        self.max_pages = max_pages
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': user_agent
        })
        self.visited_urls: Set[str] = set()
    
    def scrape_website(self, url: str, single_page: bool = False) -> Dict:
        """
        Scrape a website and extract all content.
        
        Args:
            url: Website URL to scrape
            single_page: If True, only scrape the exact URL without following links.
                         If False, auto-detects: specific page URLs scrape single page,
                         root/home URLs crawl the full site.
            
        Returns:
            Dictionary containing:
                - source_url: The original URL
                - pages: List of scraped page dictionaries
                - stats: Scraping statistics
                - method: 'wordpress', 'html', or 'single_page'
        """
        base_url = self._get_base_url(url)
        
        # Auto-detect: if the URL has a meaningful path, treat as single page
        is_single = single_page or self._is_specific_page(url)
        
        if is_single:
            logger.info(f"Single page mode for: {url}")
            page_data = self._scrape_single_page(url)
            pages = [page_data] if page_data else []
            method = 'single_page'
        elif self._is_wordpress_site(base_url):
            logger.info(f"Detected WordPress site: {base_url}")
            pages = self._scrape_wordpress(base_url)
            method = 'wordpress'
        else:
            logger.info(f"Using HTML scraping for: {base_url}")
            pages = self._scrape_html(url)
            method = 'html'
        
        stats = {
            'total_pages': len(pages),
            'total_words': sum(len(p['text'].split()) for p in pages if p.get('text')),
            'failed_pages': sum(1 for p in pages if p.get('error'))
        }
        
        return {
            'source_url': url,
            'pages': pages,
            'stats': stats,
            'method': method
        }
    
    def _get_base_url(self, url: str) -> str:
        """Extract base URL from a full URL."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    
    def _is_specific_page(self, url: str) -> bool:
        """
        Determine if a URL points to a specific page (not the home/root).
        
        A URL like https://example.com/ is a root URL -> crawl whole site
        A URL like https://example.com/some-page/ is specific -> single page
        
        Args:
            url: URL to check
            
        Returns:
            True if the URL appears to be a specific page, False if root/home
        """
        parsed = urlparse(url)
        path = parsed.path.strip('/')
        
        # If no path or just '/', it's a root URL
        if not path:
            return False
        
        # Common non-page paths that indicate "browse the whole site"
        root_like_paths = ['', 'index', 'index.html', 'index.php', 'home']
        if path.lower() in root_like_paths:
            return False
        
        # Has a meaningful path -> specific page
        logger.info(f"Auto-detected specific page URL: {url} (path: /{path})")
        return True
    
    def _is_wordpress_site(self, base_url: str) -> bool:
        """
        Check if a site is WordPress by trying to access the REST API.
        
        Args:
            base_url: Base URL of the website
            
        Returns:
            True if WordPress, False otherwise
        """
        try:
            wp_api_url = urljoin(base_url, '/wp-json/wp/v2/posts?per_page=1')
            response = self.session.get(wp_api_url, timeout=5)
            return response.status_code == 200
        except Exception:
            return False
    
    def _scrape_wordpress(self, base_url: str) -> List[Dict]:
        """
        Scrape content from a WordPress site using the REST API.
        
        Args:
            base_url: Base URL of WordPress site
            
        Returns:
            List of page dictionaries
        """
        pages = []
        page_num = 1
        per_page = 10
        
        while len(pages) < self.max_pages:
            try:
                # Fetch posts
                posts_url = urljoin(
                    base_url, 
                    f'/wp-json/wp/v2/posts?per_page={per_page}&page={page_num}'
                )
                response = self.session.get(posts_url, timeout=self.timeout)
                
                if response.status_code != 200:
                    break
                
                posts = response.json()
                
                if not posts:
                    break
                
                for post in posts:
                    if len(pages) >= self.max_pages:
                        break
                    
                    # Extract clean text from HTML content
                    text = self._extract_text_from_html(post.get('content', {}).get('rendered', ''))
                    
                    pages.append({
                        'url': post.get('link'),
                        'title': self._clean_html(post.get('title', {}).get('rendered', 'Untitled')),
                        'text': text,
                        'word_count': len(text.split()),
                        'date': post.get('date'),
                        'source': 'wordpress_api'
                    })
                
                page_num += 1
                time.sleep(0.5)  # Be nice to the server
                
            except Exception as e:
                logger.error(f"Error fetching WordPress posts page {page_num}: {e}")
                break
        
        logger.info(f"Scraped {len(pages)} WordPress pages")
        return pages
    
    def _scrape_html(self, start_url: str) -> List[Dict]:
        """
        Scrape content from a website using HTML parsing.
        
        Args:
            start_url: Starting URL to crawl
            
        Returns:
            List of page dictionaries
        """
        pages = []
        to_visit = [start_url]
        base_url = self._get_base_url(start_url)
        
        while to_visit and len(pages) < self.max_pages:
            url = to_visit.pop(0)
            
            if url in self.visited_urls:
                continue
            
            self.visited_urls.add(url)
            
            try:
                page_data = self._scrape_single_page(url)
                
                if page_data:
                    pages.append(page_data)
                    
                    # Extract and add new links to visit
                    if page_data.get('links'):
                        for link in page_data['links']:
                            # Only follow links on the same domain
                            if link.startswith(base_url) and link not in self.visited_urls:
                                to_visit.append(link)
                
                time.sleep(0.5)  # Rate limiting
                
            except Exception as e:
                logger.error(f"Error scraping {url}: {e}")
                pages.append({
                    'url': url,
                    'title': 'Error',
                    'text': '',
                    'error': str(e)
                })
        
        logger.info(f"Scraped {len(pages)} HTML pages")
        return pages
    
    def _scrape_single_page(self, url: str) -> Optional[Dict]:
        """
        Scrape a single page.
        
        Args:
            url: URL to scrape
            
        Returns:
            Page dictionary or None if failed
        """
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Extract title
            title = soup.find('title')
            title_text = title.get_text(strip=True) if title else 'Untitled'
            
            # Extract main content
            text = self._extract_main_content(soup)
            
            # Extract links
            links = self._extract_links(soup, url)
            
            return {
                'url': url,
                'title': title_text,
                'text': text,
                'word_count': len(text.split()),
                'links': links,
                'source': 'html_scrape'
            }
            
        except Exception as e:
            logger.error(f"Failed to scrape {url}: {e}")
            return None
    
    def _extract_main_content(self, soup: BeautifulSoup) -> str:
        """
        Extract main text content from a BeautifulSoup object.
        Prioritizes <article>, <main>, and content areas.
        
        Args:
            soup: BeautifulSoup object
            
        Returns:
            Extracted clean text
        """
        # Remove script and style elements
        for element in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            element.decompose()
        
        # Try to find main content areas
        main_content = None
        
        # Priority order for content extraction
        selectors = [
            'article',
            'main',
            '[role="main"]',
            '.post-content',
            '.entry-content',
            '.article-content',
            '#content',
            '.content'
        ]
        
        for selector in selectors:
            main_content = soup.select_one(selector)
            if main_content:
                break
        
        # Fall back to body if no main content found
        if not main_content:
            main_content = soup.find('body')
        
        if main_content:
            text = main_content.get_text(separator='\n', strip=True)
        else:
            text = soup.get_text(separator='\n', strip=True)
        
        # Clean up excessive whitespace
        text = re.sub(r'\n\s*\n', '\n\n', text)
        text = re.sub(r' +', ' ', text)
        
        return text.strip()
    
    def _extract_text_from_html(self, html: str) -> str:
        """
        Extract clean text from HTML string.
        
        Args:
            html: HTML string
            
        Returns:
            Clean text
        """
        soup = BeautifulSoup(html, 'html.parser')
        text = soup.get_text(separator='\n', strip=True)
        text = re.sub(r'\n\s*\n', '\n\n', text)
        return text.strip()
    
    def _clean_html(self, html: str) -> str:
        """Remove HTML tags from a string."""
        soup = BeautifulSoup(html, 'html.parser')
        return soup.get_text(strip=True)
    
    def _extract_links(self, soup: BeautifulSoup, base_url: str) -> List[str]:
        """
        Extract all links from a page.
        
        Args:
            soup: BeautifulSoup object
            base_url: Base URL for resolving relative links
            
        Returns:
            List of absolute URLs
        """
        links = []
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            absolute_url = urljoin(base_url, href)
            
            # Filter out non-http(s) links
            if absolute_url.startswith(('http://', 'https://')):
                links.append(absolute_url)
        
        return links
    
    def split_text_into_chunks(
        self, 
        text: str, 
        max_length: int = 500,
        overlap: int = 50
    ) -> List[str]:
        """
        Split long text into chunks suitable for embedding.
        
        Args:
            text: Text to split
            max_length: Maximum words per chunk
            overlap: Number of words to overlap between chunks
            
        Returns:
            List of text chunks
        """
        words = text.split()
        chunks = []
        
        i = 0
        while i < len(words):
            chunk_end = min(i + max_length, len(words))
            chunk_words = words[i:chunk_end]
            chunks.append(' '.join(chunk_words))
            i += (max_length - overlap)
        
        return chunks


def scrape_website(url: str, max_pages: int = 50) -> Dict:
    """
    Convenience function to scrape a website.
    
    Args:
        url: Website URL to scrape
        max_pages: Maximum number of pages to scrape
        
    Returns:
        Scraping results dictionary
    """
    scraper = WebsiteScraper(max_pages=max_pages)
    return scraper.scrape_website(url)


# Example usage
if __name__ == "__main__":
    # Test the scraper
    test_url = "https://example.com"
    
    result = scrape_website(test_url, max_pages=5)
    
    print(f"Scraped {result['stats']['total_pages']} pages")
    print(f"Method: {result['method']}")
    print(f"Total words: {result['stats']['total_words']}")
    
    if result['pages']:
        first_page = result['pages'][0]
        print(f"\nFirst page:")
        print(f"  Title: {first_page['title']}")
        print(f"  URL: {first_page['url']}")
        print(f"  Words: {first_page['word_count']}")
