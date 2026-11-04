export function normalizedProductName(value: string): string {
  return (value || "").toLowerCase().replaceAll("×", "x")
    .replace(/\bhard[\s_-]*(?:disk|drive)\b|\bharddisk\b|\bharddrive\b|\bhdd\b/g, "harddisk")
    .replace(/\bsolid[\s_-]*state[\s_-]*drive\b|\bsolidstate\b|\bssd\b/g, "ssd")
    .replace(/(\d+(?:\.\d+)?)\s*(tb|gb|mb|mp|inch|inches)\b/g, "$1$2")
    .match(/[a-z0-9]+(?:\.[0-9]+)?/g)?.sort().join(" ") || "";
}

export function productMatchesDescription(product: { name: string; aliases?: string[] }, description: string): boolean {
  const identity = normalizedProductName(description);
  return Boolean(identity) && [product.name, ...(product.aliases || [])]
    .some(name => normalizedProductName(name) === identity);
}
