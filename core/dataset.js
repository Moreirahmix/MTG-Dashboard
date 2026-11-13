async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to load ${url}: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

/** Load and merge card-detail shards in the order listed by their manifest. */
export async function loadCardDetails(baseUrl = "./data") {
  const dataUrl = baseUrl.endsWith("/") ? baseUrl.slice(0, -1) : baseUrl;
  const files = await fetchJson(`${dataUrl}/card_details_manifest.json`);
  if (!Array.isArray(files) || files.some(
    (file) => typeof file !== "string" || !/^card_details_\d+\.json$/.test(file)
  )) {
    throw new Error("Invalid card details manifest");
  }

  const shards = await Promise.all(files.map(async (file) => {
    const cards = await fetchJson(`${dataUrl}/${file}`);
    if (!Array.isArray(cards)) {
      throw new Error(`Invalid card details shard: ${file}`);
    }
    return cards;
  }));
  return shards.flat();
}
