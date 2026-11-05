# LinkedIn Manager

A small local app for drafting LinkedIn posts. You write the post, attach and
order photos, see an approximate feed preview with the "…see more" cut-off and
the 3,000-character limit, then copy the text into LinkedIn.

It uses only Python's standard library, so there is nothing to install.

## Run it on your Mac

```bash
git clone https://github.com/lowkey4295/jarvis.git   # first time only
cd jarvis
git checkout ccr-e0601ad8-wm7rem
python3 linkedin_manager/server.py --open
```

The app opens at <http://localhost:8000>. Press `Ctrl+C` in the terminal to stop
it. If port 8000 is busy, add `--port 8080`.

## Using it

1. Pick a draft on the left. Two ACTREC-visit drafts are preloaded.
2. Edit the text. Changes save automatically (`⌘S` saves right away).
3. Drag photos from Finder onto the drop zone. Click thumbnails to add them to
   the post or remove them; the number on each one is its order in the post.
4. Type your name and headline at the top of the preview card.
5. Click **Copy post text**, open LinkedIn, start a post, paste, and add the
   same photos in the same order. Then click **Mark as posted**.

The **Notes** box holds a pre-posting checklist and a ready-made first comment
with sources.

## Where your data lives

| What   | Where                          | In git? |
| ------ | ------------------------------ | ------- |
| Drafts | `linkedin_manager/data/posts.json` | No  |
| Photos | `linkedin_manager/media/`          | No  |
| Starter drafts | `linkedin_manager/seed_posts.json` | Yes |

Drafts and photos are git-ignored, so private photos (for example of children
in hospital) stay on your machine. On first run the app copies the starter
drafts into `data/posts.json`. Delete that file to reset to the starter drafts.

HEIC photos from an iPhone show in Safari but not in Chrome. Export them as
JPEG (Photos → File → Export) if you use Chrome.
