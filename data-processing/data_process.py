"""
Optimized MTG Card Processing Script
Fixed critical bugs and improved performance
"""

import os
import json
import shutil
from decimal import Decimal
from collections import Counter, defaultdict
from datetime import datetime
from scryfall_import import fetch_and_process_cards

MTG_COLOR_ORDER = {"W": 0, "U": 1, "B": 2, "R": 3, "G": 4}
MTG_SUPERTYPES = {"Basic", "Legendary", "Ongoing", "Token", "Snow", "World", "Host", "Elite"}
MTG_CARD_TYPES = {
    "Artifact", "Conspiracy", "Creature", "Dungeon", "Emblem", "Enchantment",
    "Hero", "Instant", "Kindred", "Land", "Phenomenon", "Plane", "Planeswalker",
    "Scheme", "Sorcery", "Vanguard", "Battle"
}
EXCLUDED_SET_TYPES = {"memorabilia", "alchemy", "token"}
EXCLUDED_SET_NAMES = {"Arena", "Alchemy", "Digital"}
MAX_SHARD_BYTES = 99_000_000


class DecimalEncoder(json.JSONEncoder):
    """Custom encoder for Decimal types."""
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


class CardDetailsShardWriter:
    """Write card details to JSON-array shards below the configured byte limit."""

    def __init__(self, output_dir, max_bytes=MAX_SHARD_BYTES):
        self.output_dir = output_dir
        self.max_bytes = max_bytes
        self.files = []
        self.file = None
        self.shard_size = 0
        self.shard_card_count = 0

    def _open_shard(self):
        filename = f"card_details_{len(self.files) + 1:03d}.json"
        self.files.append(filename)
        self.file = open(os.path.join(self.output_dir, filename), 'w', encoding='utf-8')
        self.file.write('[')
        self.shard_size = 1
        self.shard_card_count = 0

    def _close_shard(self):
        if self.file is not None:
            self.file.write(']')
            self.shard_size += 1
            self.file.close()
            self.file = None

    def write(self, card_details):
        encoded = json.dumps(
            card_details, separators=(',', ':'), cls=DecimalEncoder
        )
        encoded_size = len(encoded.encode('utf-8'))
        separator_size = 1 if self.shard_card_count else 0

        if encoded_size + 2 > self.max_bytes:
            raise ValueError("A card detail exceeds the maximum shard size")

        if self.file is None:
            self._open_shard()
        elif self.shard_size + separator_size + encoded_size + 1 > self.max_bytes:
            self._close_shard()
            self._open_shard()
            separator_size = 0

        if separator_size:
            self.file.write(',')
            self.shard_size += 1
        self.file.write(encoded)
        self.shard_size += encoded_size
        self.shard_card_count += 1

    def finish(self):
        self._close_shard()
        manifest_path = os.path.join(self.output_dir, 'card_details_manifest.json')
        with open(manifest_path, 'w', encoding='utf-8') as manifest:
            json.dump(self.files, manifest, separators=(',', ':'))


def get_release_date(card):
    """Parse release date, return datetime.max for unknown dates."""
    date_str = card.get('released_at', '')
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return datetime.max


def normalize_card_name(name):
    """Strip split card indicators from name."""
    return name.split('//')[0].strip() if name else ''


def parse_type_line_cached(cache, card_id, type_line):
    """Parse type line with caching to avoid redundant work."""
    if card_id in cache:
        return cache[card_id]
    
    front_type_line = type_line.split("//")[0].strip() if type_line else ''
    
    if '—' in front_type_line:
        type_part, subtype = map(str.strip, front_type_line.split('—', 1))
    else:
        type_part, subtype = front_type_line.strip(), ''
    
    words = type_part.split()
    supertypes = [w for w in words if w in MTG_SUPERTYPES]
    card_types = [w for w in words if w in MTG_CARD_TYPES]
    
    result = {
        "supertype": ' '.join(supertypes) if supertypes else '',
        "type": ' '.join(card_types) if card_types else '',
        "subtype": subtype,
        "type_set": set(card_types)
    }
    cache[card_id] = result
    return result


def get_front_face(card):
    """Extract front face once and cache it."""
    if 'card_faces' in card and isinstance(card['card_faces'], list) and card['card_faces']:
        return card['card_faces'][0]
    return card


def calculate_complexity(card):
    """Calculate oracle text complexity."""
    if 'card_faces' in card:
        text = ' '.join(face.get('oracle_text', '') for face in card['card_faces'])
    else:
        text = card.get('oracle_text', '')
    return len(text.split())


def build_fingerprint(front_face):
    """Create a compact fingerprint for quick comparison."""
    return (
        front_face.get('mana_cost', ''),
        front_face.get('type_line', ''),
        front_face.get('oracle_text', ''),
        front_face.get('power', ''),
        front_face.get('toughness', '')
    )


def determine_originality(card, parsed_cache, prior_by_fingerprint, functional_reprint_names, log_file):
    """Determine if card is original, reprint, or functional reprint.

    `prior_by_fingerprint` is a dict keyed by the compact card fingerprint. Each
    value is a small list of prior cards with that fingerprint (sorted by release
    date because the input is processed in date order). This keeps the hot path to
    a tight per-fingerprint scan instead of repeatedly scanning the whole dataset.
    """
    card_id = id(card)
    card_name = normalize_card_name(get_front_face(card).get('name', ''))

    if card.get('set_type') == 'memorabilia':
        return 'art_series'

    if card_name in functional_reprint_names:
        return 'functional reprint'

    front = get_front_face(card)
    type_line = front.get('type_line', '')
    parse_type_line_cached(parsed_cache, card_id, type_line)

    fingerprint = build_fingerprint(front)
    release_date = card.get('released_at', '')

    for prior_card in prior_by_fingerprint.get(fingerprint, []):
        prior_name = normalize_card_name(get_front_face(prior_card).get('name', ''))

        if prior_name == card_name or prior_name in functional_reprint_names:
            continue

        if prior_card.get('released_at', '') >= release_date:
            continue

        functional_reprint_names.add(card_name)

        msg = (
            f"Functional reprint detected: '{card_name}' "
            f"is a reprint of '{prior_name}' "
            f"(released earlier on {prior_card.get('released_at', 'unknown')})"
        )
        log_file.write(msg + '\n')
        return 'functional reprint'

    prior_by_fingerprint[fingerprint].append(card)
    return 'reprint' if card.get('reprint') else 'original'


def load_jsonl(filepath):
    """Load JSONL file properly (one JSON object per line)."""
    cards = []
    with open(filepath, 'r', encoding='utf-8') as file:
        for line_num, line in enumerate(file, 1):
            line = line.strip()
            if line:
                try:
                    cards.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"Warning: Skipping invalid JSON on line {line_num}: {e}")
    return cards


def clear_directory(directory):
    """Remove all contents of a directory while keeping the directory itself."""
    if not os.path.isdir(directory):
        return

    for entry in os.scandir(directory):
        if entry.is_dir(follow_symlinks=False):
            shutil.rmtree(entry.path)
        else:
            os.remove(entry.path)


def main():
    # Setup paths
    output_dir = './data'
    cache_dir = './scryfall_cache'
    src_dir = './scryfall_cache'
    clear_directory(output_dir)
    clear_directory(cache_dir)
    os.makedirs(output_dir, exist_ok=True)
    log_file_path = os.path.join(output_dir, 'functional_reprints_log.txt')
    output_writer = CardDetailsShardWriter(output_dir)
    
    log_file = open(log_file_path, 'w', encoding='utf-8')
    
    # Load cards from JSONL (FIXED: proper JSONL loading)
    print("Loading cards from JSONL...")
    all_cards = list(fetch_and_process_cards())
    all_cards.sort(key=get_release_date)
    print(f"Loaded {len(all_cards)} cards")
    
    # Track prior cards per fingerprint during the single ordered pass to avoid a
    # second O(n^2) scan over the full dataset when determining originality.
    print("Preparing fingerprint history...")
    prior_by_fingerprint = defaultdict(list)
    parsed_cache = {}
    functional_reprint_names = set()

    # Initialize counters
    color_count = Counter()
    color_identity_count = Counter()
    rarity_count = Counter()
    originality_count = Counter()
    price_field_count = Counter()
    supertype_count = Counter()
    type_count = Counter()
    subtype_count = Counter()
    
    # Process cards and write output
    print("Processing cards...")
    written_index = 0

    for card_idx, card in enumerate(all_cards):
        # Skip memorabilia early
        if card.get('set_type') in EXCLUDED_SET_TYPES:
            continue

        # Skip digital sets
        if any(name in card.get('set_name', '') for name in EXCLUDED_SET_NAMES):
            continue

        # Parse type line (cached)
        type_line = card.get('type_line', '')
        parsed = parse_type_line_cached(parsed_cache, id(card), type_line)
        supertype = parsed['supertype'] or 'Basic'
        type_ = parsed['type']
        subtype = parsed['subtype']

        # Handle color identity
        color_identity = card.get('color_identity', [])
        if "Land" in type_:
            color_identity = []
        else:
            color_identity = sorted(color_identity, key=lambda c: MTG_COLOR_ORDER.get(c, 99))

        # Update counters
        supertype_count[supertype] += 1
        type_count[type_] += 1
        if subtype:
            subtype_count[subtype] += 1
        color_count[len(color_identity)] += 1
        color_identity_count[tuple(color_identity)] += 1
        rarity_count[card.get('rarity', 'unknown')] += 1

        # Determine originality using a per-fingerprint prior-card cache.
        originality = determine_originality(
            card, parsed_cache, prior_by_fingerprint, functional_reprint_names, log_file
        )
        originality_count[originality] += 1

        # Price tracking
        prices = card.get('prices', {})
        for field in ["usd", "usd_foil", "usd_etched", "eur", "eur_foil", "tix"]:
            price_value = prices.get(field)
            try:
                if price_value and float(price_value) > 0:
                    price_field_count[field] += 1
            except (TypeError, ValueError):
                continue

        # Build face data
        image_uris = card.get('image_uris', {})
        faces_data = []

        if 'card_faces' in card:
            for face in card['card_faces']:
                faces_data.append({
                    "name": face.get('name'),
                    "mana_cost": face.get('mana_cost', ''),
                    "type_line": face.get('type_line', ''),
                    "image_normal": face.get('image_uris', {}).get('normal', ''),
                })
        else:
            faces_data.append({
                "name": card.get('name'),
                "mana_cost": card.get('mana_cost', ''),
                "type_line": card.get('type_line', ''),
                "image_normal": image_uris.get('normal', ''),
            })

        # Build card details
        card_details = {
            "name": card.get('name'),
            "cmc": float(card.get('cmc', 0)),
            "keywords": card.get('keywords', []),
            "color_identity": color_identity,
            "number_of_colours": len(color_identity),
            "supertype": supertype,
            "type": type_,
            "subtype": subtype,
            "rarity": card.get('rarity', 'unknown'),
            "release_date": card.get('released_at', ''),
            "originality": originality,
            "complexity": calculate_complexity(card),
            "digital": card.get('digital', False),
            "edhrec_rank": card.get('edhrec_rank', 0),
            "price_usd": float(prices.get('usd', 0) or 0),
            "price_usd_foil": float(prices.get('usd_foil', 0) or 0),
            "price_usd_etched": float(prices.get('usd_etched', 0) or 0),
            "price_eur": float(prices.get('eur', 0) or 0),
            "price_eur_foil": float(prices.get('eur_foil', 0) or 0),
            "price_tix": float(prices.get('tix', 0) or 0),
            "set_name": card.get('set_name', 'Unknown'),
            "set_type": prettify_set_type(card.get('set_type')),
            "image_normal": image_uris.get('normal', ''),
            "faces": faces_data,
            "index": written_index,
            "fit_for_scatter": not card.get('digital', False)
        }

        # Write to the current size-limited shard.
        output_writer.write(card_details)
        written_index += 1

        # Progress indicator
        if card_idx % 10000 == 0:
            print(f"Processed {card_idx}/{len(all_cards)} cards...")

    output_writer.finish()
    
    log_file.close()
    
    # Print summary statistics
    print("\n" + "="*50)
    print("PROCESSING COMPLETE")
    print("="*50)
    print(f"Total cards processed: {written_index}")
    print(f"\nOriginality distribution: {dict(originality_count)}")
    print(f"\nRarity distribution: {dict(rarity_count)}")
    print(f"\nPrice availability (non-zero):")
    for field, count in sorted(price_field_count.items()):
        print(f"  {field}: {count}")


def prettify_set_type(set_type):
    """Convert set type string to readable format."""
    return set_type.replace('_', ' ') if isinstance(set_type, str) else 'Unknown'


if __name__ == "__main__":
    main()
