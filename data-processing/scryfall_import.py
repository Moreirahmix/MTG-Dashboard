"""
Scryfall API Integration Module
Handles fetching and downloading bulk card data from Scryfall
"""

import os
import json
import gzip
import time
import hashlib
import requests
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Any, Generator

SCRYFALL_API_BASE = "https://api.scryfall.com"
SCRYFALL_BULK_ENDPOINT = "/bulk-data"
DEFAULT_CACHE_DIR = "./scryfall_cache"
REQUEST_TIMEOUT = 60
RATE_LIMIT_DELAY = 0.25


class ScryfallAPIError(Exception):
    """Custom exception for Scryfall API errors."""
    pass


def make_api_request(
    endpoint: str,
    params: Optional[Dict[str, str]] = None,
    cache_age_hours: int = 1,
    cache_dir: str = DEFAULT_CACHE_DIR
) -> Dict[str, Any]:
    """
    Make a request to Scryfall API with caching and rate limiting.
    
    Args:
        endpoint: API path (e.g., '/bulk-data/cards')
        params: Optional query parameters
        cache_age_hours: How long to cache the response (in hours)
        cache_dir: Directory for caching responses
    
    Returns:
        Parsed JSON response
    """
    url = f"{SCRYFALL_API_BASE}{endpoint}"
    
    os.makedirs(cache_dir, exist_ok=True)
    
    cache_key = hashlib.md5(f"{url}:{json.dumps(params or {})}".encode()).hexdigest()
    cache_file = Path(cache_dir) / f"{cache_key}.json"
    
    if cache_file.exists():
        cache_age = datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)
        if cache_age < timedelta(hours=cache_age_hours):
            with open(cache_file, 'r') as f:
                return json.load(f)
    
    headers = {
        "User-Agent": "MTGCardProcessor/1.0 (your@email.com)",
        "Accept": "application/json"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response is not None else None

        if status_code == 429:
            time.sleep(RATE_LIMIT_DELAY * 2)
            response = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
        elif status_code == 404:
            raise ScryfallAPIError(f"Endpoint not found: {endpoint}")
        elif status_code is not None:
            raise ScryfallAPIError(f"HTTP error: {status_code}")
        else:
            raise ScryfallAPIError(f"HTTP error: {e}")
    except requests.exceptions.RequestException as e:
        raise ScryfallAPIError(f"Request failed: {e}")
    
    data = response.json()
    with open(cache_file, 'w') as f:
        json.dump(data, f)
    
    return data


def get_bulk_download_info(cache_dir: str = DEFAULT_CACHE_DIR) -> Dict[str, Any]:
    """
    Fetch metadata for the cards bulk data file.
    
    Returns:
        Dictionary containing:
        - jsonl_download_uri: URL to download the gzipped JSONL file
        - compressed_size: Size in bytes
        - updated_at: Last update timestamp
        - uri: API URI for this bulk data item
    """
    bulk_data = make_api_request(SCRYFALL_BULK_ENDPOINT, cache_dir=cache_dir)
    
    target_bulk = None
    for bulk_item in bulk_data.get('data', []):
        if bulk_item['type'] in ('all_cards'):
            target_bulk = bulk_item
            break
    
    if not target_bulk:
        raise ScryfallAPIError("Could not find oracle_cards bulk data")
    
    return {
        "download_uri": target_bulk.get('download_uri'),
        "jsonl_download_uri": target_bulk.get('jsonl_download_uri'),
        "compressed_size": target_bulk.get('compressed_size'),
        "updated_at": target_bulk.get('updated_at'),
        "uri": target_bulk.get('uri'),
        "type": target_bulk.get('type'),
        "name": target_bulk.get('name')
    }


def download_jsonl_stream(
    download_url: str,
    output_path: str,
    chunk_size: int = 8192,
    show_progress: bool = True
) -> str:
    """
    Download gzipped JSONL file with streaming decompression.
    
    Args:
        download_url: URL from jsonl_download_uri
        output_path: Where to save the uncompressed JSONL
        chunk_size: Bytes to read at a time
        show_progress: Whether to show download progress
    
    Returns:
        Path to downloaded file
    """
    headers = {
        "User-Agent": "MTGCardProcessor/1.0 (your@email.com)"
    }
    
    start_time = time.time()
    bytes_downloaded = 0
    
    with requests.get(download_url, headers=headers, stream=True, timeout=300) as response:
        response.raise_for_status()
        total_size = int(response.headers.get('content-length', 0))
        
        with gzip.GzipFile(fileobj=response.raw, mode='rb') as gz_file:
            with open(output_path, 'wb') as out_file:
                while True:
                    chunk = gz_file.read(chunk_size)
                    if not chunk:
                        break
                    
                    out_file.write(chunk)
                    
                    if show_progress and total_size > 0:
                        bytes_downloaded = min(response.raw.tell(), total_size)
                        elapsed = time.time() - start_time
                        percent = (bytes_downloaded / total_size) * 100
                        speed = bytes_downloaded / (elapsed if elapsed > 0 else 1)
                        
                        print(
                            f"\rDownloading: {percent:6.1f}% | "
                            f"{bytes_downloaded / (1024*1024):.1f}MB / "
                            f"{total_size / (1024*1024):.1f}MB | "
                            f"{speed / (1024*1024):.2f}MB/s",
                            end='',
                            flush=True
                        )
    
    if show_progress:
        print(f"\nDownload complete in {time.time() - start_time:.1f}s")
    
    return output_path


def stream_jsonl_lines(
    filepath: str,
    buffer_size: int = 8192
) -> Generator[Dict[str, Any], None, None]:
    """
    Stream JSONL file line by line (memory efficient).
    
    Yields:
        Parsed JSON object for each line
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                print(f"Warning: Invalid JSON on line {line_num}: {e}")
                continue


def fetch_and_process_cards(
    cache_dir: str = DEFAULT_CACHE_DIR,
    force_refresh: bool = False
) -> Generator[Dict[str, Any], None, None]:
    """
    Main function: Fetch latest card data from Scryfall and stream it.
    
    Args:
        cache_dir: Directory for API responses and downloads
        force_refresh: If True, re-download even if cached
    
    Yields:
        Card data dictionaries
    """
    output_file = Path(cache_dir) / "cards.jsonl"
    
    print("Fetching bulk data metadata...")
    bulk_info = get_bulk_download_info(cache_dir)
    
    print(f"Bulk data type: {bulk_info['name']}")
    print(f"Last updated: {bulk_info['updated_at']}")
    print(f"Compressed size: {bulk_info['compressed_size'] / (1024*1024):.1f} MB")
    
    should_download = (
        force_refresh or 
        not output_file.exists() or
        datetime.fromtimestamp(output_file.stat().st_mtime) < datetime.fromisoformat(bulk_info['updated_at'])
    )
    
    if should_download:
        print("\nDownloading card data from Scryfall...")
        download_jsonl_stream(
            bulk_info['jsonl_download_uri'],
            str(output_file)
        )
    else:
        print(f"\nUsing cached file: {output_file}")
    
    print("Streaming cards...")
    card_count = 0
    for card in stream_jsonl_lines(str(output_file)):
        yield card
        card_count += 1
        
        if card_count % 10000 == 0:
            print(f"  Processed {card_count} cards...")
    
    print(f"Total cards streamed: {card_count}")


def get_compressed_jsonl_direct(
    cache_dir: str = DEFAULT_CACHE_DIR
) -> Generator[Dict[str, Any], None, None]:
    """
    Alternative: Stream directly from Scryfall without saving locally.
    Useful for one-off processing without disk storage.
    
    Warning: Less reliable for large datasets due to potential connection issues.
    """
    bulk_info = get_bulk_download_info(cache_dir)
    download_url = bulk_info['jsonl_download_uri']
    
    headers = {
        "User-Agent": "MTGCardProcessor/1.0 (your@email.com)"
    }
    
    with requests.get(download_url, headers=headers, stream=True, timeout=300) as response:
        response.raise_for_status()
        
        with gzip.GzipFile(fileobj=response.raw, mode='rb') as gz_file:
            for line in gz_file:
                line = line.decode('utf-8').strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue