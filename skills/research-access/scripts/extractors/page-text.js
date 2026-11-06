(options) => {
  const MAIN_SELECTOR = ".se-main-container, #postViewArea, .ArticleContentBox, article, main, [role='main']";
  const container = document.querySelector(MAIN_SELECTOR) ?? document.body;
  const text = container.innerText.replace(/[ \t]+/g, " ").replace(/\n{2,}/g, "\n").trim();

  return {
    title: document.title,
    url: location.href,
    totalChars: text.length,
    truncated: text.length > options.maxChars,
    text: text.slice(0, options.maxChars),
  };
}
