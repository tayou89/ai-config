(options) => {
  const POST_PATTERN = /(blog|cafe)\.naver\.com\/[^/?#]+\/\d+/;
  const DISCLOSURE_PATTERN = /협찬|원고료|제공받|체험단|광고|대가성|내돈내산/g;
  const MIN_TITLE_LENGTH = 8;
  const SUMMARY_LENGTH = 200;
  const collapse = (text) => text.replace(/\s+/g, " ").trim();
  const toItem = (anchor, url) => {
    const box = anchor.closest("li") ?? anchor.parentElement?.parentElement ?? anchor;
    const title = collapse(anchor.innerText);
    const text = collapse(box.innerText);

    return {
      kind: url.includes("cafe.naver.com") ? "cafe" : "blog",
      title,
      summary: text.replace(title, "").trim().slice(0, SUMMARY_LENGTH),
      disclosures: [...new Set(text.match(DISCLOSURE_PATTERN) ?? [])],
      url,
    };
  };
  const isPostLink = (anchor) => (
    POST_PATTERN.test(anchor.href) &&
    collapse(anchor.innerText).length >= MIN_TITLE_LENGTH
  );
  const anchors = [...document.querySelectorAll("a[href]")].filter(isPostLink);
  const seen = new Set();
  const results = [];

  for (const anchor of anchors) {
    const url = anchor.href.split("?")[0];

    if (!seen.has(url) && results.length < options.limit) {
      seen.add(url);
      results.push(toItem(anchor, url));
    }
  }
  return { results };
}
