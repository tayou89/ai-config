(options) => {
  const CARD_SELECTOR = "article, li";
  const HEADER_SELECTOR = ".review-header, .we-customer-review__header";
  const RATING_SELECTOR = "[aria-label*='별'], [aria-label*='star'], [class*='stars']";
  const REVIEW_LENGTH = 400;
  const collapse = (text) => (text ?? "").replace(/\s+/g, " ").trim();
  const cardOf = (header) => header.closest(CARD_SELECTOR) ?? header.parentElement;
  const toReview = (header) => {
    const card = cardOf(header);
    const headerText = collapse(header.innerText);
    const title = collapse(card.querySelector("h3, h4, [class*='title']")?.innerText);
    const ratingLabel = card.querySelector(RATING_SELECTOR)?.getAttribute("aria-label") ?? "";
    const body = collapse(card.innerText).replace(title, "").replace(headerText, "").replace(/더 보기$/, "").trim();

    return {
      date: collapse(header.querySelector("time")?.innerText) || headerText.split(" ").slice(0, 2).join(" "),
      rating: (ratingLabel.match(/\d/) ?? [""])[0],
      title,
      text: body.slice(0, REVIEW_LENGTH),
    };
  };
  const head = collapse(document.body.innerText.slice(0, 600));
  const headers = [...document.querySelectorAll(HEADER_SELECTOR)];
  const isFirstOfCard = (header, index) => headers.findIndex((other) => cardOf(other) === cardOf(header)) === index;

  return {
    app: collapse(document.querySelector("h1")?.innerText),
    rating: (head.match(/([\d.]+) 5점 만점/) ?? ["", ""])[1],
    ratingCount: (head.match(/([\d.,]+[만천]?개)의 평가/) ?? ["", ""])[1],
    reviews: headers.filter(isFirstOfCard).slice(0, options.limit).map(toReview),
  };
}
