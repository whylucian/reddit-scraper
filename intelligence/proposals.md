# Intelligence Extraction Proposals

Four passes over the data, ordered from cheapest/fastest to most ambitious.

---

## 1. Grep signal mining → Claude

Fast keyword/regex grep across all 186k posts for high-signal phrases, then feed matches to Claude for structured extraction.

**Signal phrases to grep for:**
- Needs/desires: "I wish", "I'd pay", "if only", "looking for", "recommend", "where can I"
- Struggles: "I struggle", "can't figure out", "frustrated", "stuck on", "gave up", "plateau"
- Breakthroughs: "finally", "clicked", "what helped me", "game changer", "improved"
- Products/spending: "bought", "subscribed", "course", "worth it", "waste of money"

**Output:** Grep matches → Claude extracts structured JSON (topic, pain point, skill level, product mentioned, sentiment). Aggregate counts and themes.

**Cost:** Essentially free (grep) + modest Claude quota for the matches.

---

## 2. High-signal thread deep analysis → Claude

Select posts by Reddit's own quality signals — high score, high comment count — and run them through Claude for deep reading. These are the threads where the community actually engaged, argued, gave detailed feedback.

**Selection criteria:**
- Score >= 50 or num_comments >= 20 (tweak thresholds per subreddit)
- Priority subreddits: learnart, learntodraw, ArtCrit, ArtFundamentals

**What to extract per thread:**
- What is the poster's skill level and what are they struggling with?
- What feedback did they receive? Was it actionable?
- Did anyone mention tools, courses, books, apps?
- Is there a willingness-to-pay signal?
- What would an automated version of this feedback look like?

**Cost:** Claude quota. Volume depends on thresholds — probably a few thousand threads.

---

## 3. Bulk text classification & extraction → Qwen (local)

The workhorse pass. Run all 186k posts+comments through a local Qwen model for classification and structured extraction at scale.

This is where the folded proposals live — student needs, feedback patterns, critique vocabulary, product/resource extraction, and critique template mining all happen here in one pass with a structured extraction prompt.

**Extract per post:**
- **Category:** question, progress pic, critique request, resource request, discussion, vent
- **Skill level:** beginner, intermediate, advanced (inferred from text)
- **Topic:** anatomy, perspective, color, shading, composition, gesture, tools, motivation, etc.
- **Struggles mentioned** (free text)
- **Feedback given** (in comments — what dimensions do critics address?)
- **Critique vocabulary** (recurring terms and phrases used by helpful commenters)
- **Products/resources mentioned** (books, courses, apps, channels, tools — with sentiment)
- **Willingness to pay signals**

**Model choice:**

| Model | Active params | Speed | Fit |
|---|---|---|---|
| Qwen3-Next-80B-A3B | 3B of 80B | ~144 tok/s, 10x throughput for long context | Best for bulk classification. Matches Qwen3-235B quality with 10% cost. 256K context handles long threads. |
| Qwen3.5-35B-A3B | 3B of 35B | Fast (sparse) | Surpasses Qwen3-235B-A22B. Good balance. |
| Qwen3.5-27B | 27B (dense) | Slower, needs more VRAM | Strongest quality (86.1 MMLU-Pro, 85.5 GPQA). Better for nuanced extraction but ~9x more compute than the A3B models. |

**Recommendation:** Qwen3-Next-80B-A3B for the bulk pass (fast, cheap, good enough for classification). Run Qwen3.5-27B on the most interesting subset if nuance matters.

**Cost:** Local compute only. The A3B models need ~6-8GB VRAM for the active params.

---

## 4. Image analysis → Qwen-VL bulk + Claude select

The image data is most of the 489GB. Two tiers:

**Tier A — Bulk vision classification (Qwen3-VL, local):**
- Classify skill level of submitted artwork
- Identify medium (pencil, digital, ink, paint, etc.)
- Identify subject (portrait, figure, landscape, still life, etc.)
- Flag before/after pairs in ArtProgressPics

Qwen3-VL comes in 30B-A3B (sparse, fast) and 32B (dense, higher quality). The 30B-A3B variant is practical for bulk. Strong visual benchmarks — 85.8% MathVista, 96.5% DocVQA.

**Tier B — Deep visual analysis (Claude, select images):**
- ArtProgressPics: What specifically improved between before/after? What's still weak?
- ArtCrit: Look at the artwork alongside the critique — does the feedback match the actual issues?
- DrawMe/RedditGetsDrawn: Reference-to-drawing pairs — what choices did artists make?

Uses existing Claude quota on curated subsets.

**Cost:** Tier A is local compute. Tier B is Claude vision quota on hundreds (not thousands) of images.

---

## Execution order

1 → 2 → 3 → 4. Each pass builds on the previous — grep findings inform the Qwen extraction prompt, Claude deep-dives validate what the bulk pass finds, image analysis adds a dimension text can't capture.

Sources:
- [Qwen3-Next-80B-A3B benchmarks](https://llm-stats.com/models/qwen3-next-80b-a3b-instruct)
- [Qwen3-Next architecture overview](https://simonwillison.net/2025/Sep/12/qwen3-next/)
- [Qwen3.5 27B vs 35B-A3B comparison](https://vertu.com/ai-tools/qwen-3-5-27b-vs-qwen-3-5-35b-a3b-which-local-llm-reigns-supreme/)
- [Qwen3.5 medium series release](https://awesomeagents.ai/news/qwen-3-5-medium-series/)
- [Qwen3-VL technical report](https://arxiv.org/abs/2511.21631)
- [Qwen3-VL overview](https://www.alibabacloud.com/blog/qwen3-vl-sharper-vision-deeper-thought-broader-action_602584)
