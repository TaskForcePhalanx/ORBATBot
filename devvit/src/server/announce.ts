// The decisions, kept apart from the server so they can be read (and tested)
// without Devvit: who is announced, and what the Discord message says.

export const MAX_MESSAGE = 2000;
export const MAX_TITLE = 240;

export function parseAuthors(raw: string | undefined): string[] {
  return (raw ?? '')
    .split(/[,\s]+/)
    .map((name) => name.trim().replace(/^\/?u\//i, ''))
    .filter(Boolean)
    .map((name) => name.toLowerCase());
}

/** No authors configured means every post is announced. */
export function wanted(author: string, authors: string[]): boolean {
  return authors.length === 0 || authors.includes(author.toLowerCase());
}

export function message(post: {
  title: string;
  permalink: string;
}, author: string, subreddit: string): string {
  const title = post.title.slice(0, MAX_TITLE);
  const url = `https://www.reddit.com${post.permalink}`;
  return `New post by u/${author} in r/${subreddit}\n**${title}**\n${url}`.slice(0, MAX_MESSAGE);
}
