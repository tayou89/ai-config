(options) => {
  const CARD_MARKER = "이 리뷰가 유용했나요?";
  const DATE_PATTERN = /\d{4}년 \d{1,2}월 \d{1,2}일/;
  const HELPFUL_PATTERN = /사용자 ([\d,]+)명이 이 리뷰가 유용하다고 평가함/;
  const RATING_PATTERN = /만점에 ([\d.]+)개|Rated ([\d.]+) stars?/i;
  const REVIEW_LENGTH = 400;
  const collapse = (text) => (text ?? "").replace(/\s+/g, " ").trim();
  const isCard = (element) => element.innerText.includes(CARD_MARKER) && DATE_PATTERN.test(element.innerText);
  const isInnermostCard = (element) => isCard(element) && ![...element.children].some(isCard);
  const toReview = (card) => {
    const text = collapse(card.innerText);
    const date = (text.match(DATE_PATTERN) ?? [""])[0];
    const body = text.slice(text.indexOf(date) + date.length).split(/사용자 [\d,]+명이|이 리뷰가 유용했나요/)[0].trim();
    const ratingLabel = card.querySelector("[role='img'][aria-label]")?.getAttribute("aria-label") ?? "";
    const ratingMatch = ratingLabel.match(RATING_PATTERN) ?? [];

    return {
      date,
      rating: ratingMatch[1] ?? ratingMatch[2] ?? "",
      helpful: (text.match(HELPFUL_PATTERN) ?? ["", "0"])[1],
      text: body.slice(0, REVIEW_LENGTH),
    };
  };
  const head = collapse(document.body.innerText.slice(0, 1500));
  const cards = [...document.querySelectorAll("div")].filter(isInnermostCard);

  return {
    app: collapse(document.querySelector("h1")?.innerText),
    rating: (head.match(/([\d.]+) star/) ?? ["", ""])[1],
    reviewCount: (head.match(/리뷰 ([\d.,]+[만천]?개)/) ?? ["", ""])[1],
    downloads: (head.match(/([\d,.]+[만천억]?\+) 다운로드/) ?? ["", ""])[1],
    reviews: cards.slice(0, options.limit).map(toReview),
  };
}
