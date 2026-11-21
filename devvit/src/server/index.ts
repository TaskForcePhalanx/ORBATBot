import express from 'express';
import { createServer, getServerPort, settings } from '@devvit/web/server';
import type { OnPostSubmitRequest, TriggerResponse } from '@devvit/web/shared';
import { message, parseAuthors, wanted } from './announce';

const app = express();
app.use(express.json());

// Announces a new post in a Discord channel. It only reads the post that
// Reddit hands the trigger: it never posts, comments, votes or messages, and it
// asks nobody to vote.
app.post<string, never, TriggerResponse, OnPostSubmitRequest>(
  '/internal/on-post-submit',
  async (req, res) => {
    const { post, author, subreddit } = req.body;
    try {
      const webhook = await settings.get<string>('discordWebhookUrl');
      if (!webhook) {
        console.log('No Discord webhook URL set; nothing announced.');
        res.status(200).json({});
        return;
      }
      const authors = parseAuthors(await settings.get<string>('authors'));
      const name = author?.name ?? '';
      if (!post || !wanted(name, authors)) {
        res.status(200).json({});
        return;
      }

      const response = await fetch(webhook, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: message(post, name, subreddit?.name ?? ''),
          // The title is quoted from Reddit; it must never ping anybody.
          allowed_mentions: { parse: [] },
        }),
      });
      if (!response.ok) {
        console.error(`Discord answered ${response.status}.`);
      }
    } catch (error) {
      console.error('Could not announce the post:', error);
    }
    res.status(200).json({});
  }
);

createServer(app).listen(getServerPort());
