# Drawing App Intelligence

## Goal

Build a highly interactive app that teaches people how to draw. Think Duolingo for drawing: quick feedback loops, structured critique, photo-based feedback (submit your drawing or drawing + reference and get feedback). Anything that adds value to the learning process.

## Approach

Scour ~186k posts and comments (and images) scraped from drawing-related subreddits to find signals: what students struggle with, what they ask for, what they'd pay for, what feedback patterns actually help people improve.

## Data Source

~489 GB scraped from 9 subreddits via Arctic Shift API (score >= 5, 2010-2027):

| Subreddit | Posts | Size | What's there |
|---|---|---|---|
| learntodraw | 43,291 | 74G | Beginners asking for help, posting attempts |
| DrawMe | 36,407 | 120G | Reference photos + artist drawings |
| RedditGetsDrawn | 25,169 | 59G | Reference photos + artist drawings |
| learnart | 23,348 | 26G | Learning questions, resource requests, progress |
| ArtCrit | 22,734 | 34G | Work submitted for critique + feedback threads |
| DrawMeNSFW | 15,603 | 40G | Reference photos + drawings |
| ArtProgressPics | 9,209 | 13G | Before/after comparisons, timelines |
| ArtFundamentals | 6,501 | 6.2G | Drawabox exercises, structured practice |
| SketchDaily | 3,858 | 121G | Daily prompt responses, practice sketches |

## Data Structure

Each post is a directory named by Reddit post ID (e.g. `data/learnart/135akr6/`) containing:

- `post.txt` — Title, author, score, URL, permalink, selftext
- `comments.txt` — Full comment tree with indentation (author + body, nested replies)
- `*.jpg` / `*.png` / `*.jpeg` / `*.webp` — Downloaded images (post images, gallery images, images linked in comments)
- `errors.txt` — (if any) failed image downloads or URL resolutions

Scraper config: `scrape.sh` / `scrape.py`. Posts tracked via `.seen_posts` file per subreddit.

## Key Subreddits by Use

**For understanding student needs:** learntodraw, learnart, ArtFundamentals
**For critique/feedback patterns:** ArtCrit, learnart
**For progress data (before/after):** ArtProgressPics
**For reference-to-drawing pairs:** DrawMe, RedditGetsDrawn
**For daily practice patterns:** SketchDaily
