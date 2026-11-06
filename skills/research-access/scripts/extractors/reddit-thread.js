(options) => {
  const HUMAN_CHECK_PATTERN = /whoa there|blocked by network security|prove you're human/i;
  const COMMENT_SELECTOR = ".commentarea > .sitetable > .thing.comment";
  const COMMENT_LENGTH = 400;
  const collapse = (text) => (text ?? "").replace(/\s+/g, " ").trim();
  const toComment = (comment) => ({
    score: collapse(comment.querySelector(":scope > .entry .score.unvoted")?.getAttribute("title") ?? ""),
    text: collapse(comment.querySelector(":scope > .entry .md")?.innerText).slice(0, COMMENT_LENGTH),
  });
  const isPinned = (comment) => comment.classList.contains("stickied");
  const post = document.querySelector("#siteTable > .thing.link");

  if (HUMAN_CHECK_PATTERN.test(document.title + " " + document.body.innerText.slice(0, 500))) {
    return { humanCheck: true };
  } else {
    const comments = [...document.querySelectorAll(COMMENT_SELECTOR)].filter((comment) => !isPinned(comment));

    return {
      title: collapse(post?.querySelector("a.title")?.innerText),
      subreddit: post?.getAttribute("data-subreddit") ?? "",
      score: post?.getAttribute("data-score") ?? "",
      commentCount: post?.getAttribute("data-comments-count") ?? "",
      posted: post?.querySelector("time")?.getAttribute("datetime") ?? "",
      body: collapse(post?.querySelector(".expando .md")?.innerText).slice(0, Math.floor(options.maxChars / 3)),
      comments: comments.slice(0, options.limit).map(toComment),
    };
  }
}
