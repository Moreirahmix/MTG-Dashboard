[← Back to main page](../)

The preprocessing step reads the raw Scryfall export and produces normalized card-detail JSON shards used by all visualizations. Each `card_details_NNN.json` is kept below 100,000,000 bytes. The generated `card_details_manifest.json` lists shard filenames in card-index order; the dashboard loads and concatenates them before preparing its data. For each non-memorabilia card, we derive and store:

- `name` – canonicalized front-face name.
- `cmc` – converted to numeric.
- `keywords` – Scryfall `keywords` array.
- `color_identity` – sorted W/U/B/R/G identity; lands are forced to colorless.
- `number_of_colours` – length of `color_identity`.
- `supertype`, `type`, `subtype` – parsed from `type_line` (front face only).
- `rarity`
- `release_date` – from `released_at`.
- `originality` – one of `original`, `reprint`, `functional reprint`, computed via rules over mana cost, type line, oracle text, P/T and release date.
- `complexity` – word count of oracle text (all faces).
- `digital` – Scryfall `digital` flag.
- `edhrec_rank`
- `price_usd`, `price_usd_foil`, `price_usd_etched`,
  `price_eur`, `price_eur_foil`, `price_tix` – numeric, 0 when missing/invalid.
- `set_name`
- `set_type` – prettified `set_type` (underscores → spaces).
- `image_normal` – primary display image.
- `faces` – minimal per-face info (`name`, `mana_cost`, `type_line`, `image_normal`) for multi-faced cards.
- `index` – stable sequential id used across views.

### Notes for the app
- The app requires `index` to equal the card's position in the array (`prepareCards` checks this).
- Keep each generated shard below `MAX_SHARD_BYTES` in `data-processing/data_process.py`; the manifest is written after all shards complete.
- `color_identity` must be in WUBRG order and `release_date` in ISO `YYYY-MM-DD` format: years are read straight from the string.
- At load time the app adds derived fields (`_ci`, `_nc`, `_year`, `_orig`, `_rar`) without changing the file's schema. See [Application core](stateManager.md#2-loading-and-preparing-the-data).
- After regenerating the dataset, run `npm test`. The test and benchmark helpers load the manifest and merge shards in the same order as the dashboard; the differential tests compare the new code with the frozen pre-refactor logic on the actual dataset ([Development](development.md)).